//! Mirror a shared-memory RGB framebuffer to an iDotMatrix LED panel over BLE.
//!
//! The framebuffer is a plain file of WIDTH*HEIGHT*3 bytes (RGB888, row-major,
//! origin top-left). Any program can draw by writing to it; the daemon uploads
//! the contents whenever they change.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{bail, Context, Result};
use btleplug::api::{Central, Characteristic, Manager as _, Peripheral as _, ScanFilter, WriteType};
use btleplug::platform::{Adapter, Manager, Peripheral};
use clap::{Parser, Subcommand};
use futures::{FutureExt, Stream, StreamExt};
use tokio::time::{sleep, timeout, Instant};
use uuid::Uuid;

const WRITE_UUID: Uuid = Uuid::from_u128(0x0000fa02_0000_1000_8000_00805f9b34fb);
const NOTIFY_UUID: Uuid = Uuid::from_u128(0x0000fa03_0000_1000_8000_00805f9b34fb);
/// Advertised name prefixes of iDotMatrix panels (older and newer firmware).
const NAME_PREFIXES: &[&str] = &["IDM-", "LED_BLE_"];
/// Images are uploaded in chunks of at most this many PNG bytes.
const IMAGE_CHUNK: usize = 4096;

#[derive(Parser)]
#[command(version, about)]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// List nearby iDotMatrix panels
    Scan {
        /// Seconds to scan
        #[arg(long, default_value_t = 10)]
        seconds: u64,
    },
    /// Run the framebuffer daemon
    Run(RunArgs),
}

#[derive(clap::Args)]
struct RunArgs {
    /// BLE address of the panel (default: first panel found)
    #[arg(long, short)]
    address: Option<String>,
    /// Framebuffer file (created if missing)
    #[arg(long, short, default_value = "/dev/shm/billboard.rgb")]
    framebuffer: PathBuf,
    /// Panel width and height in pixels
    #[arg(long, default_value_t = 32)]
    size: u32,
    /// Maximum frames per second
    #[arg(long, default_value_t = 20.0)]
    fps: f64,
    /// Brightness in percent (5-100)
    #[arg(long, value_parser = clap::value_parser!(u8).range(5..=100))]
    brightness: Option<u8>,
    /// Rotate the image clockwise by this many degrees before sending
    #[arg(long, default_value_t = 0, value_parser = parse_rotation)]
    rotate: u16,
}

fn parse_rotation(s: &str) -> Result<u16, String> {
    match s.parse() {
        Ok(d @ (0 | 90 | 180 | 270)) => Ok(d),
        _ => Err("must be 0, 90, 180 or 270".into()),
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    match Cli::parse().command {
        Command::Scan { seconds } => scan(seconds).await,
        Command::Run(args) => run(args).await,
    }
}

async fn adapter() -> Result<Adapter> {
    let manager = Manager::new().await.context("cannot reach the Bluetooth stack")?;
    manager
        .adapters()
        .await?
        .into_iter()
        .next()
        .context("no Bluetooth adapter found (is it powered on / unblocked?)")
}

async fn panel_name(p: &Peripheral) -> Option<String> {
    let name = p.properties().await.ok()??.local_name?;
    NAME_PREFIXES.iter().any(|pre| name.starts_with(pre)).then_some(name)
}

async fn scan(seconds: u64) -> Result<()> {
    let central = adapter().await?;
    central.start_scan(ScanFilter::default()).await?;
    sleep(Duration::from_secs(seconds)).await;
    central.stop_scan().await?;
    let mut found = 0;
    for p in central.peripherals().await? {
        if let Some(name) = panel_name(&p).await {
            println!("{}  {}", p.address(), name);
            found += 1;
        }
    }
    if found == 0 {
        eprintln!("no panels found");
    }
    Ok(())
}

/// Scan until the requested panel (or any panel) shows up.
async fn find_panel(central: &Adapter, address: Option<&str>) -> Result<Peripheral> {
    central.start_scan(ScanFilter::default()).await?;
    let found = async {
        loop {
            for p in central.peripherals().await? {
                let matches = match address {
                    Some(a) => p.address().to_string().eq_ignore_ascii_case(a),
                    None => panel_name(&p).await.is_some(),
                };
                if matches {
                    return Ok::<_, anyhow::Error>(p);
                }
            }
            sleep(Duration::from_millis(500)).await;
        }
    }
    .await;
    central.stop_scan().await.ok();
    found
}

struct Panel {
    peripheral: Peripheral,
    write: Characteristic,
    notifications: std::pin::Pin<Box<dyn Stream<Item = btleplug::api::ValueNotification> + Send>>,
}

impl Panel {
    async fn connect(central: &Adapter, address: Option<&str>) -> Result<Self> {
        let peripheral = find_panel(central, address).await?;
        eprintln!("connecting to {}", peripheral.address());
        peripheral.connect().await?;
        peripheral.discover_services().await?;
        let chars = peripheral.characteristics();
        let find = |uuid| chars.iter().find(|c| c.uuid == uuid).cloned();
        let write = find(WRITE_UUID).context("panel has no write characteristic")?;
        let notify = find(NOTIFY_UUID).context("panel has no notify characteristic")?;
        peripheral.subscribe(&notify).await?;
        let notifications = peripheral.notifications().await?;
        eprintln!("connected (mtu {})", peripheral.mtu());
        Ok(Self { peripheral, write, notifications })
    }

    async fn send(&self, data: &[u8]) -> Result<()> {
        let chunk = usize::from(self.peripheral.mtu().saturating_sub(3)).max(20);
        for part in data.chunks(chunk) {
            self.peripheral.write(&self.write, part, WriteType::WithoutResponse).await?;
        }
        Ok(())
    }

    /// Upload a PNG and wait (briefly) for the panel's acknowledgement, which
    /// keeps us from queueing frames faster than the panel can show them.
    async fn show_png(&mut self, png: &[u8]) -> Result<()> {
        // Drop stale notifications so we wait for this frame's ack.
        while let Some(Some(_)) = self.notifications.next().now_or_never() {}
        self.send(&image_payload(png)).await?;
        let _ = timeout(Duration::from_secs(1), self.notifications.next()).await;
        Ok(())
    }

    async fn is_connected(&self) -> bool {
        self.peripheral.is_connected().await.unwrap_or(false)
    }
}

fn image_payload(png: &[u8]) -> Vec<u8> {
    let chunks: Vec<&[u8]> = png.chunks(IMAGE_CHUNK).collect();
    // The meaning of this header field is unknown; this matches the vendor app.
    let tag = (png.len() + chunks.len()) as u16;
    let mut out = Vec::with_capacity(png.len() + chunks.len() * 9);
    for (i, chunk) in chunks.iter().enumerate() {
        out.extend_from_slice(&tag.to_le_bytes());
        out.extend_from_slice(&[0, 0, if i == 0 { 0 } else { 2 }]);
        out.extend_from_slice(&(png.len() as u32).to_le_bytes());
        out.extend_from_slice(chunk);
    }
    out
}

fn rotate(rgb: &[u8], size: usize, degrees: u16) -> Vec<u8> {
    if degrees == 0 {
        return rgb.to_vec();
    }
    let mut out = vec![0; rgb.len()];
    for y in 0..size {
        for x in 0..size {
            // Destination pixel (x, y) takes its colour from source (sx, sy).
            let (sx, sy) = match degrees {
                90 => (y, size - 1 - x),
                180 => (size - 1 - x, size - 1 - y),
                _ => (size - 1 - y, x),
            };
            let (d, s) = ((y * size + x) * 3, (sy * size + sx) * 3);
            out[d..d + 3].copy_from_slice(&rgb[s..s + 3]);
        }
    }
    out
}

fn encode_png(rgb: &[u8], size: u32) -> Result<Vec<u8>> {
    let mut out = Vec::new();
    let mut enc = png::Encoder::new(&mut out, size, size);
    enc.set_color(png::ColorType::Rgb);
    enc.set_depth(png::BitDepth::Eight);
    let mut writer = enc.write_header()?;
    writer.write_image_data(rgb)?;
    writer.finish()?;
    Ok(out)
}

/// Read the framebuffer, padding or truncating to the expected size.
/// The file is reopened every time so writers may replace it atomically.
fn read_framebuffer(path: &Path, len: usize) -> Result<Vec<u8>> {
    let mut data = std::fs::read(path).with_context(|| format!("reading {}", path.display()))?;
    data.resize(len, 0);
    Ok(data)
}

fn create_framebuffer(path: &Path, len: usize) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    if !path.exists() {
        std::fs::write(path, vec![0; len])?;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o666))?;
        eprintln!("created {}", path.display());
    } else if std::fs::metadata(path)?.len() != len as u64 {
        eprintln!("warning: {} is not {len} bytes; missing pixels are black", path.display());
    }
    Ok(())
}

async fn run(args: RunArgs) -> Result<()> {
    if !(args.fps > 0.0) {
        bail!("--fps must be positive");
    }
    let len = (args.size * args.size * 3) as usize;
    create_framebuffer(&args.framebuffer, len)?;
    let central = adapter().await?;
    let frame_time = Duration::from_secs_f64(1.0 / args.fps);

    loop {
        let result: Result<()> = async {
            let mut panel = Panel::connect(&central, args.address.as_deref()).await?;
            // Enter DIY mode, which displays uploaded images.
            panel.send(&[5, 0, 4, 1, 1]).await?;
            if let Some(b) = args.brightness {
                panel.send(&[5, 0, 4, 0x80, b]).await?;
            }
            let mut last: Option<Vec<u8>> = None;
            let (mut sent, mut report_at) = (0u32, Instant::now());
            loop {
                let started = Instant::now();
                let frame = read_framebuffer(&args.framebuffer, len)?;
                if last.as_ref() != Some(&frame) {
                    let png = encode_png(&rotate(&frame, args.size as usize, args.rotate), args.size)?;
                    panel.show_png(&png).await?;
                    last = Some(frame);
                    sent += 1;
                    if report_at.elapsed() >= Duration::from_secs(10) {
                        let secs = report_at.elapsed().as_secs_f64();
                        eprintln!("{sent} frames in {secs:.0}s ({:.1} fps)", f64::from(sent) / secs);
                        (sent, report_at) = (0, Instant::now());
                    }
                } else if !panel.is_connected().await {
                    bail!("panel disconnected");
                }
                sleep(frame_time.saturating_sub(started.elapsed())).await;
            }
        }
        .await;
        if let Err(e) = result {
            eprintln!("error: {e:#}; reconnecting in 2s");
        }
        sleep(Duration::from_secs(2)).await;
    }
}
