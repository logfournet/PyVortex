"""Generate a Minecraft-grass-block .ico for PyVortex."""
import struct, zlib, os, random

def make_grass_pixels(w, h, rng):
    """Return list of (r,g,b) for a w×h grass block."""
    grass_base = (89, 176, 60)
    grass_dark = (62, 140, 38)
    grass_light = (110, 200, 78)
    dirt_base = (139, 94, 60)
    dirt_dark = (107, 63, 31)
    dirt_light = (160, 107, 69)
    dirt_spot = (74, 46, 26)
    boundary = [int(h * 0.28) + rng.randint(-int(h * 0.08), int(h * 0.10)) for _ in range(w)]
    pixels = []
    for y in range(h):
        for x in range(w):
            is_grass = y < boundary[x]
            if is_grass:
                n = rng.random()
                if n < 0.18:
                    c = grass_dark
                elif n < 0.35:
                    c = grass_light
                else:
                    c = grass_base
            else:
                n = rng.random()
                if n < 0.08:
                    c = dirt_spot
                elif n < 0.22:
                    c = dirt_dark
                elif n < 0.35:
                    c = dirt_light
                else:
                    c = dirt_base
            r = max(0, min(255, c[0] + rng.randint(-6, 6)))
            g = max(0, min(255, c[1] + rng.randint(-6, 6)))
            b = max(0, min(255, c[2] + rng.randint(-6, 6)))
            pixels.append((r, g, b))
    return pixels

def write_png(pixels, w, h):
    raw = b""
    for y in range(h):
        raw += b"\x00"
        for x in range(w):
            r, g, b = pixels[y * w + x]
            raw += struct.pack("BBB", r, g, b) + b"\xff"
    def chunk(ctype, data):
        c = ctype + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xffffffff)
        return struct.pack(">I", len(data)) + c + crc
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))

if __name__ == "__main__":
    sizes = [16, 32, 48, 64, 128, 256]
    entries = []
    chunks = []
    offset = 6 + len(sizes) * 16
    for s in sizes:
        rng = random.Random(42)
        px = make_grass_pixels(s, s, rng)
        png = write_png(px, s, s)
        ico_w = 0 if s >= 256 else s
        ico_h = 0 if s >= 256 else s
        entries.append(struct.pack("BBBBHHII",
                                   ico_w, ico_h, 0, 0, 1, 32,
                                   len(png), offset))
        chunks.append(png)
        offset += len(png)
    header = struct.pack("HHH", 0, 1, len(sizes))
    ico_data = header + b"".join(entries) + b"".join(chunks)
    out = os.path.join(os.path.dirname(__file__), "..", "launcher", "assets", "pyvortex.ico")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(ico_data)
    print(f"Written {out} ({os.path.getsize(out)} bytes, {len(sizes)} resolutions)")
