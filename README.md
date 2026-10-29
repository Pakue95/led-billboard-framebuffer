# billboard

Shared-memory framebuffer for iDotMatrix LED panels (32×32 RGB, BLE).

The panel is controlled over Bluetooth Low Energy; its USB port only supplies
power. `billboard run` connects to the panel and mirrors a plain file of raw
pixels to it, so any program in any language can draw by writing bytes.

## Framebuffer format

`/dev/shm/billboard.rgb` (change with `--framebuffer`), created on start-up
with mode 0666:

- 32 × 32 pixels × 3 bytes = **3072 bytes**
- RGB888, row-major, origin top-left: pixel `(x, y)` is at offset `(y * 32 + x) * 3`

The daemon reads the file up to `--fps` times per second and uploads it only
when the contents changed. Two ways to write:

- **mmap / write in place** — simplest; a frame caught mid-write is corrected
  on the next tick.
- **write a temp file and `rename()` it over** — atomic; the daemon reopens
  the path every tick.

## Usage

```sh
billboard scan                     # list nearby panels (names IDM-… or LED_BLE_…)
billboard run                      # connect to the first panel found
billboard run --address 58:6A:E5:4E:A5:B7 --brightness 60 --rotate 90
```

The daemon reconnects automatically if the panel drops or is power-cycled.
Measured on a Raspberry Pi 4: ~28 fps for typical content, ~15 fps for random
noise (which barely compresses as PNG).

Bluetooth must be unblocked and powered (`rfkill unblock bluetooth`,
`bluetoothctl power on`).

## Examples

```sh
# solid red
python3 -c "open('/dev/shm/billboard.rgb','wb').write(b'\xff\0\0'*1024)"
# an image (PIL resizes it to 32x32)
python3 -c "from PIL import Image; open('/dev/shm/billboard.rgb','wb').write(Image.open('pic.png').convert('RGB').resize((32,32)).tobytes())"
# clear
head -c 3072 /dev/zero > /dev/shm/billboard.rgb
```

## Building

```sh
cargo build --release   # -> target/release/billboard
```

libdbus is compiled in (`dbus` crate, `vendored` feature), so the binary only
needs glibc and a running BlueZ at runtime. Install with
`install -m755 target/release/billboard /usr/local/bin/` and optionally
`billboard.service`.

## Protocol notes

GATT service `0x00FA`: commands are written to `fa02`, replies arrive on `fa03`.

| Command            | Bytes                                    |
|--------------------|------------------------------------------|
| DIY (image) mode   | `05 00 04 01 01`                         |
| Brightness         | `05 00 04 80 <percent>`                  |
| Screen off / on    | `05 00 07 01 00` / `05 00 07 01 01`      |
| Fullscreen colour  | `07 00 02 02 <r> <g> <b>`                |
| Image upload       | per 4096-byte PNG chunk: `<len(png)+chunks:u16le> 00 00 <0 first, 2 after> <len(png):u32le> <chunk>` |

The panel acknowledges an image with a notification (`05 00 00 00 01`); the
daemon waits for it (up to 1 s) before sending the next frame.
