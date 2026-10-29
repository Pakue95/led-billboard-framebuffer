#!/usr/bin/env python3
"""Demo animations for the billboard framebuffer.

Plays animations on the panel through the shared-memory framebuffer, or
renders them to a GIF (needs Pillow) for READMEs and demos.

    ./demo.py                          # all animations, looping, on the panel
    ./demo.py plasma fire --duration 8
    ./demo.py --gif demo.gif           # one pass of everything into a GIF
    ./demo.py --list
"""

import argparse
import colorsys
import math
import os
import random
import sys
import time

W = H = 32
FRAME_BYTES = W * H * 3


def clamp(v):
    return 0 if v < 0 else 255 if v > 255 else int(v)


def hsv(h, s=1.0, v=1.0):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def put(buf, x, y, rgb):
    i = (y * W + x) * 3
    buf[i:i + 3] = bytes(rgb)


def add(buf, x, y, rgb):
    if 0 <= x < W and 0 <= y < H:
        i = (y * W + x) * 3
        for k in range(3):
            buf[i + k] = min(255, buf[i + k] + rgb[k])


# Each animation is a generator taking (fps, rng) and yielding 3072-byte frames.

def plasma(fps, rng):
    palette = [hsv(i / 256) for i in range(256)]
    f = 0
    while True:
        t = f / fps
        buf = bytearray(FRAME_BYTES)
        cx = W / 2 + math.sin(t * 0.7) * 12
        cy = H / 2 + math.cos(t * 0.5) * 12
        for y in range(H):
            for x in range(W):
                v = (math.sin(x / 5 + t)
                     + math.sin((y / 3 + t) / 2)
                     + math.sin((x + y + t * 8) / 7)
                     + math.sin(math.hypot(x - cx, y - cy) / 4))
                put(buf, x, y, palette[int((v + 4) * 32 + t * 40) & 255])
        yield buf
        f += 1


def fire(fps, rng):
    palette = []
    for i in range(256):
        palette.append((clamp(i * 3), clamp(i * 3 - 255), clamp(i * 3 - 510)))
    heat = [[0] * W for _ in range(H + 2)]
    while True:
        for x in range(W):
            heat[H + 1][x] = rng.choice((0, 255, 255, 200))
            heat[H][x] = rng.choice((0, 255, 160))
        for y in range(H):
            row, below, below2 = heat[y], heat[y + 1], heat[y + 2]
            for x in range(W):
                s = (below[(x - 1) % W] + below[x] + below[(x + 1) % W]
                     + below2[x])
                row[x] = max(0, s // 4 - rng.randrange(18))
        buf = bytearray(FRAME_BYTES)
        for y in range(H):
            for x in range(W):
                put(buf, x, y, palette[min(255, heat[y][x])])
        yield buf


def rainbow(fps, rng):
    f = 0
    while True:
        t = f / fps
        buf = bytearray(FRAME_BYTES)
        for y in range(H):
            for x in range(W):
                a = math.atan2(y - H / 2 + 0.5, x - W / 2 + 0.5) / (2 * math.pi)
                r = math.hypot(x - W / 2 + 0.5, y - H / 2 + 0.5) / W
                put(buf, x, y, hsv(a + r * 1.5 - t * 0.4))
        yield buf
        f += 1


def matrix(fps, rng):
    drops = [[rng.uniform(-H, 0), rng.uniform(8, 20), rng.randint(6, 16)]
             for _ in range(W)]
    glyphs = [[rng.random() for _ in range(W)] for _ in range(H)]
    while True:
        buf = bytearray(FRAME_BYTES)
        for x, d in enumerate(drops):
            d[0] += d[1] / fps
            head = int(d[0])
            for k in range(d[2]):
                y = head - k
                if 0 <= y < H:
                    if k == 0:
                        put(buf, x, y, (200, 255, 200))
                    else:
                        fade = (1 - k / d[2]) ** 1.5
                        g = fade * (0.55 + 0.45 * glyphs[y][x])
                        put(buf, x, y, (0, clamp(255 * g), clamp(60 * g)))
            if head - d[2] > H:
                d[:] = [rng.uniform(-10, 0), rng.uniform(8, 20),
                        rng.randint(6, 16)]
        for _ in range(8):
            glyphs[rng.randrange(H)][rng.randrange(W)] = rng.random()
        yield buf


def starfield(fps, rng):
    def star():
        return [rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(0.1, 1)]
    stars = [star() for _ in range(90)]
    f = 0
    while True:
        t = f / fps
        buf = bytearray(FRAME_BYTES)
        hue = t * 0.05
        for s in stars:
            s[2] -= 0.6 / fps
            sx = int(W / 2 + s[0] / s[2] * W / 2)
            sy = int(H / 2 + s[1] / s[2] * H / 2)
            if s[2] <= 0.02 or not (0 <= sx < W and 0 <= sy < H):
                s[:] = star()
                s[2] = 1
                continue
            v = min(1, (1 - s[2]) * 1.4)
            add(buf, sx, sy, hsv(hue, 0.35, v))
        yield buf
        f += 1


def life(fps, rng):
    def seed():
        return [[rng.random() < 0.35 for _ in range(W)] for _ in range(H)]
    grid = seed()
    age = [[0] * W for _ in range(H)]
    history = []
    tick = max(1, fps // 10)
    f = 0
    while True:
        if f % tick == 0:
            new = [[False] * W for _ in range(H)]
            for y in range(H):
                for x in range(W):
                    n = sum(grid[(y + dy) % H][(x + dx) % W]
                            for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                            if dy or dx)
                    new[y][x] = n == 3 or (grid[y][x] and n == 2)
                    age[y][x] = age[y][x] + 1 if new[y][x] else 0
            key = hash(tuple(map(tuple, new)))
            history = (history + [key])[-12:]
            if history.count(key) > 2:
                new = seed()
                age = [[0] * W for _ in range(H)]
                history = []
            grid = new
        buf = bytearray(FRAME_BYTES)
        for y in range(H):
            for x in range(W):
                if grid[y][x]:
                    put(buf, x, y, hsv(0.55 - min(age[y][x], 40) / 60, 0.9))
        yield buf
        f += 1


def metaballs(fps, rng):
    balls = [(rng.uniform(0.3, 1.0), rng.uniform(0.3, 1.0),
              rng.uniform(0, 6.3), rng.uniform(0, 6.3)) for _ in range(4)]
    f = 0
    while True:
        t = f / fps
        pos = [(W / 2 + math.sin(t * a + p) * 11, H / 2 + math.cos(t * b + q) * 11)
               for a, b, p, q in balls]
        buf = bytearray(FRAME_BYTES)
        for y in range(H):
            for x in range(W):
                v = sum(30 / ((x - bx) ** 2 + (y - by) ** 2 + 1) for bx, by in pos)
                if v > 1:
                    put(buf, x, y, hsv(0.8 + min(v, 2.5) * 0.06 + t * 0.05,
                                       0.85, min(1, v - 0.6)))
                elif v > 0.6:
                    put(buf, x, y, hsv(0.6 + t * 0.05, 1, (v - 0.6) * 0.8))
        yield buf
        f += 1


def ripples(fps, rng):
    cur = [[0.0] * W for _ in range(H)]
    prev = [[0.0] * W for _ in range(H)]
    f = 0
    while True:
        if f % max(1, fps // 2) == 0:
            x, y = rng.randrange(2, W - 2), rng.randrange(2, H - 2)
            prev[y][x] = 500
        nxt = [[0.0] * W for _ in range(H)]
        for y in range(1, H - 1):
            for x in range(1, W - 1):
                nxt[y][x] = ((cur[y - 1][x] + cur[y + 1][x] + cur[y][x - 1]
                              + cur[y][x + 1]) / 2 - prev[y][x]) * 0.96
        prev, cur = cur, nxt
        buf = bytearray(FRAME_BYTES)
        for y in range(H):
            for x in range(W):
                v = cur[y][x]
                put(buf, x, y, (clamp(v * 3), clamp(30 + v * 4), clamp(90 + v * 5)))
        yield buf
        f += 1


ANIMATIONS = {fn.__name__: fn for fn in
              (plasma, fire, rainbow, matrix, starfield, life, metaballs, ripples)}


def blend(a, b, k):
    return bytearray(int(x + (y - x) * k) for x, y in zip(a, b))


def frames(names, fps, duration, fade, seed, loop):
    """Yield frames for each animation in turn, crossfading between them."""
    rng = random.Random(seed)
    per = int(duration * fps)
    nfade = min(int(fade * fps), per // 2)
    nxt = None
    while True:
        for i, name in enumerate(names):
            gen = nxt or ANIMATIONS[name](fps, rng)
            nxt = None
            last = i == len(names) - 1 and not loop
            for n in range(per):
                frame = next(gen)
                if nfade and not last and n >= per - nfade:
                    if nxt is None:
                        nxt = ANIMATIONS[names[(i + 1) % len(names)]](fps, rng)
                    k = (n - (per - nfade) + 1) / (nfade + 1)
                    frame = blend(frame, next(nxt), k)
                yield frame
        if not loop:
            return


def write_frame(path, frame):
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(frame)
    os.chmod(tmp, 0o666)  # keep the daemon's world-writable contract
    os.replace(tmp, path)


def play(path, frame_iter, fps):
    interval = 1 / fps
    deadline = time.monotonic()
    for frame in frame_iter:
        write_frame(path, frame)
        deadline += interval
        delay = deadline - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            deadline = time.monotonic()


def save_gif(path, frame_iter, fps, scale, style):
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        sys.exit("--gif needs Pillow: pip install pillow")
    size = (W * scale, H * scale)
    mask = None
    if style == "led" and scale >= 3:
        mask = Image.new("RGB", size)
        draw = ImageDraw.Draw(mask)
        pad = max(1, scale // 8)
        for y in range(H):
            for x in range(W):
                draw.ellipse((x * scale + pad, y * scale + pad,
                              (x + 1) * scale - pad - 1, (y + 1) * scale - pad - 1),
                             fill=(255, 255, 255))
    images = []
    for frame in frame_iter:
        img = Image.frombytes("RGB", (W, H), bytes(frame)).resize(size, Image.NEAREST)
        if mask:
            img = ImageChops.multiply(img, mask)
        # no dithering: much smaller files, and flat LEDs don't need it
        images.append(img.quantize(256, dither=Image.Dither.NONE))
    images[0].save(path, save_all=True, append_images=images[1:],
                   duration=round(1000 / fps), loop=0, disposal=1)
    print(f"wrote {path}: {len(images)} frames, {size[0]}x{size[1]}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("animations", nargs="*", metavar="NAME",
                   help="animations to play (default: all)")
    p.add_argument("--list", action="store_true", help="list animations and exit")
    p.add_argument("--framebuffer", default="/dev/shm/billboard.rgb",
                   help="framebuffer path (default: %(default)s)")
    p.add_argument("--fps", type=int,
                   help="frames per second (default: 12 on the panel, which is about"
                        " what BLE sustains for busy frames; 20 for --gif)")
    p.add_argument("--duration", type=float, default=6,
                   help="seconds per animation (default: %(default)s)")
    p.add_argument("--fade", type=float, default=0.75,
                   help="crossfade seconds between animations (default: %(default)s)")
    p.add_argument("--seed", type=int, help="random seed, for reproducible output")
    p.add_argument("--once", action="store_true", help="play through once instead of looping")
    p.add_argument("--gif", metavar="FILE", help="render one pass to a GIF instead of playing")
    p.add_argument("--scale", type=int, default=10, help="GIF pixel size (default: %(default)s)")
    p.add_argument("--style", choices=("led", "pixel"), default="led",
                   help="GIF look: round LED dots or square pixels (default: %(default)s)")
    args = p.parse_args()

    if args.list:
        print("\n".join(ANIMATIONS))
        return
    names = args.animations or list(ANIMATIONS)
    unknown = [n for n in names if n not in ANIMATIONS]
    if unknown:
        p.error(f"unknown animation(s): {', '.join(unknown)} (see --list)")
    if args.fps is None:
        args.fps = 20 if args.gif else 12
    if args.fps < 1 or args.duration <= 0:
        p.error("--fps and --duration must be positive")

    if args.gif:
        it = frames(names, args.fps, args.duration, args.fade, args.seed, loop=False)
        save_gif(args.gif, it, args.fps, args.scale, args.style)
        return
    it = frames(names, args.fps, args.duration, args.fade, args.seed, loop=not args.once)
    try:
        play(args.framebuffer, it, args.fps)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
