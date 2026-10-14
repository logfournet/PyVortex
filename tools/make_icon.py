"""Generate a Minecraft-dirt-block .ico for PyVortex."""
import struct, zlib, os, sys

DIRT = [
    # 16x16 Minecraft-dirt-like pattern (base-16 palette indices)
    0,0,0,0,1,0,0,0,0,0,1,0,0,0,0,0,
    0,0,2,0,0,0,0,1,0,0,0,0,2,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,1,0,0,0,3,0,0,0,0,3,0,0,0,1,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    2,0,0,0,1,0,0,0,0,0,0,1,0,0,0,2,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,0,0,3,0,0,0,0,0,0,0,0,3,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    0,1,0,0,0,2,0,0,0,0,2,0,0,0,1,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
    1,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1,
    0,0,0,0,0,0,3,0,0,3,0,0,0,0,0,0,
    0,0,2,0,0,0,0,0,0,0,0,0,0,2,0,0,
    0,0,0,0,1,0,0,0,0,0,0,1,0,0,0,0,
    0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,
]

PALETTE = [
    (139, 94, 60),   # 0 = base brown
    (107, 63, 31),   # 1 = dark brown
    (160, 107, 69),  # 2 = light brown
    (74, 46, 26),    # 3 = shadow
]

def nearest_neighbour_scale(src, sw, sh, factor):
    """Scale up a sw×sh image by factor (integer) — nearest neighbour."""
    dw, dh = sw * factor, sh * factor
    dst = [0] * (dw * dh)
    for dy in range(dh):
        sy = dy // factor
        for dx in range(dw):
            sx = dx // factor
            dst[dy * dw + dx] = src[sy * sw + sx]
    return dst, dw, dh

def write_png(pixels, w, h, palette):
    """Write a 32-bit RGBA PNG from palette-indexed pixel data."""
    raw = b""
    for y in range(h):
        raw += b"\x00"  # filter byte (none)
        for x in range(w):
            r, g, b = palette[pixels[y * w + x]]
            raw += struct.pack("BBB", r, g, b) + b"\xff"
    def chunk(ctype, data):
        c = ctype + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xffffffff)
        return struct.pack(">I", len(data)) + c + crc
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))

def write_ico(png_bytes):
    """Wrap PNG bytes in a Windows .ico container (Vista+)."""
    count = 1
    # Directory entry
    w = 0 if png_bytes[16] >= 256 else png_bytes[16]
    h = 0 if png_bytes[20] >= 256 else png_bytes[20]
    dir_entry = struct.pack("BBBBHHII",
                            w, h, 0, 0, 1, 32,
                            len(png_bytes), 22)  # offset=6+16=22
    header = struct.pack("HHH", 0, 1, count)
    return header + dir_entry + png_bytes

if __name__ == "__main__":
    base, bw, bh = DIRT, 16, 16
    # Scale to 32 and 16 for the icon
    sizes = [16, 32, 48, 64]
    all_pngs = []
    for s in sizes:
        factor = s // 16
        px, pw, ph = nearest_neighbour_scale(base, bw, bh, factor)
        all_pngs.append((pw, ph, px))
    
    # Build multi-resolution ICO
    entries = []
    chunks = []
    offset = 6 + len(sizes) * 16
    for w, h, px in all_pngs:
        png = write_png(px, w, h, PALETTE)
        # Directory entry
        ico_w = 0 if w >= 256 else w
        ico_h = 0 if h >= 256 else h
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
