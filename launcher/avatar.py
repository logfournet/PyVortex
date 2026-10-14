"""Backend for rendering a player's in-game avatar (face) in the launcher GUI.

This module is pure Python standard library (struct + zlib + urllib) and does
NOT import tkinter and has NO external dependencies. It produces *data* (nested
lists of hex colour strings) that the GUI turns into images.

Two public avatar providers are tried, in order:

  1. mineatar.io (preferred, UUID-based):

         GET https://api.mineatar.io/face/<uuid>?scale=<n>

     Returns a square face PNG whose side is ``8 * scale`` px (e.g. scale=8 ->
     64x64).  This endpoint requires a real UUID; usernames return HTTP 400, so
     it is only used when the account carries a UUID (premium accounts).

  2. mc-heads.net (fallback, username-based):

         GET https://mc-heads.net/avatar/<username>/<size>

     Works for ANY username: a real Mojang username returns that player's skin
     face, and an unknown/offline username (e.g. "SFC") returns the default
     Steve face.  Used for offline/cracked accounts (which have no UUID).

The returned PNG is decoded with a pure-stdlib decoder (struct + zlib, undoing
the row filters) supporting the common 8-bit colour types (grayscale, RGB,
palette, grayscale+alpha, RGBA), then resized to the exact requested size.  If
everything fails we fall back gracefully to an "initial" (letter) avatar.
"""

import struct
import zlib
import urllib.parse
import urllib.request

_MINEATAR_URL = "https://api.mineatar.io/face"
_MCHEADS_URL = "https://mc-heads.net/avatar"
_USER_AGENT = "PyVortex-Launcher/1.0.0"


# --------------------------------------------------------------------------- #
#  Network helper (self-contained, mirrors core.py's urllib approach)
# --------------------------------------------------------------------------- #
def _http_get(url, headers=None, timeout=60):
    """GET `url` and return the raw bytes. Raises on any failure."""
    req_headers = {"User-Agent": _USER_AGENT}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# --------------------------------------------------------------------------- #
#  1) Download the face PNG
# --------------------------------------------------------------------------- #
def fetch_avatar_png(name, size=128):
    """Download https://mc-heads.net/avatar/{urllib-quoted name}/{size} and return
    the PNG bytes.  Return None on any failure (network/HTTP/empty).  URL-quote
    the name with urllib.parse.quote.  Send a normal User-Agent.
    """
    try:
        quoted = urllib.parse.quote(str(name or ""), safe="")
        url = "%s/%s/%d" % (_MCHEADS_URL, quoted, int(size))
        png = _http_get(url)
        if not png:
            return None
        return png
    except Exception:
        return None


def fetch_mineatar_png(uuid, size=64):
    """Download https://api.mineatar.io/face/{uuid}?scale={n} and return the PNG.

    The mineatar face endpoint only accepts a UUID (usernames -> HTTP 400) and
    returns a square PNG whose side is ``8 * scale`` px, so we round the wanted
    size up to the nearest multiple of 8 and let the caller resize.  Return None
    on any failure (empty/blank uuid, network, HTTP, empty body).
    """
    try:
        uid = str(uuid or "").strip()
        if not uid:
            return None
        scale = max(1, (int(size) + 7) // 8)
        url = "%s/%s?scale=%d" % (
            _MINEATAR_URL, urllib.parse.quote(uid, safe=""), scale)
        png = _http_get(url)
        if not png:
            return None
        return png
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  2) Pure PNG decoding (struct + zlib only) -> RGB hex rows
# --------------------------------------------------------------------------- #
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# colour type -> number of samples per pixel (bit depth 8)
_PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    """PNG Paeth predictor (exact)."""
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_scanlines(raw, width, height, bytes_per_pixel):
    """Undo PNG row filters (None/Sub/Up/Average/Paeth).

    `raw` is the raw defiltered byte stream (already decompressed) with each
    row carrying a leading filter-type byte.  Returns a flat bytearray of
    `height * row_bytes` pixel data.
    """
    bpp = bytes_per_pixel
    stride = width * bpp
    out = bytearray(height * stride)

    for y in range(height):
        f = raw[y * (stride + 1)]  # filter type byte
        row = raw[y * (stride + 1) + 1: y * (stride + 1) + 1 + stride]
        row_start = y * stride

        for x in range(stride):
            a = out[row_start + x - bpp] if x >= bpp else 0       # left
            b = out[row_start - stride + x] if y > 0 else 0        # above
            c = out[row_start - stride + x - bpp] if (y > 0 and x >= bpp) else 0  # upper-left

            raw_byte = row[x]
            if f == 0:      # None
                val = raw_byte
            elif f == 1:    # Sub
                val = raw_byte + a
            elif f == 2:    # Up
                val = raw_byte + b
            elif f == 3:    # Average
                val = raw_byte + (a + b) // 2
            elif f == 4:    # Paeth
                val = raw_byte + _paeth(a, b, c)
            else:
                raise ValueError("unknown PNG filter type %d" % f)

            out[row_start + x] = val & 0xFF
    return out


def png_to_rgba_rows(png_bytes):
    """Decode an 8-bit, non-interlaced PNG using struct+zlib:

      - validate signature; parse chunks (IHDR / PLTE / IDAT / IEND);
        concatenate all IDAT payloads; zlib.decompress.
      - undo the five PNG row filters (None/Sub/Up/Average/Paeth) via the
        _unfilter_scanlines / _paeth helpers.
      - support the common 8-bit colour types: 0 grayscale, 2 RGB, 3 palette,
        4 grayscale+alpha, 6 RGBA.  Alpha is ignored (the caller masks corners).

    Return (width, height, rows) where rows is a list of `height` rows, each a
    list of `width` '#rrggbb' hex strings.  Raise ValueError (or return None) on
    malformed/unsupported input (unsupported bit depth, colour type or
    interlacing).
    """
    if not png_bytes or png_bytes[:8] != _PNG_SIGNATURE:
        raise ValueError("bad PNG signature")

    # ---- parse chunks -------------------------------------------------- #
    pos = 8
    width = height = None
    bit_depth = color_type = interlace = None
    idat = bytearray()
    palette = None

    while pos < len(png_bytes):
        if pos + 8 > len(png_bytes):
            raise ValueError("truncated PNG chunk header")
        length = struct.unpack(">I", png_bytes[pos:pos + 4])[0]
        ctype = png_bytes[pos + 4:pos + 8]
        data = png_bytes[pos + 8: pos + 8 + length]
        pos += 12 + length  # length(4) + type(4) + data(length) + crc(4)

        if ctype == b"IHDR":
            if len(data) < 13:
                raise ValueError("short IHDR")
            width, height, bit_depth, color_type, _, _, interlace = \
                struct.unpack(">IIBBBBB", data[:13])
        elif ctype == b"PLTE":
            palette = data
        elif ctype == b"IDAT":
            idat.extend(data)
        elif ctype == b"IEND":
            break

    if width is None or height is None:
        raise ValueError("missing IHDR")
    if bit_depth != 8:
        raise ValueError("unsupported bit depth: %r" % bit_depth)
    if color_type not in _PNG_CHANNELS:
        raise ValueError("unsupported color type: %r" % color_type)
    if interlace != 0:
        raise ValueError("interlaced PNG not supported")
    if color_type == 3 and not palette:
        raise ValueError("palette PNG without PLTE")

    # ---- decompress (IDAT may be split across many chunks) ------------ #
    scanlines = zlib.decompress(bytes(idat))

    # ---- defilter ------------------------------------------------------ #
    bpp = _PNG_CHANNELS[color_type]
    raw = _unfilter_scanlines(scanlines, width, height, bpp)

    # ---- colour rows --------------------------------------------------- #
    def sample(idx):
        return raw[idx] & 0xFF

    png_rows = []
    for y in range(height):
        start = y * width * bpp
        row = []
        for x in range(width):
            idx = start + x * bpp
            if color_type in (0, 4):        # grayscale (+ alpha)
                g = sample(idx)
                row.append("#%02x%02x%02x" % (g, g, g))
            elif color_type == 2:           # RGB
                row.append("#%02x%02x%02x" % (sample(idx), sample(idx + 1),
                                              sample(idx + 2)))
            elif color_type == 6:           # RGBA
                row.append("#%02x%02x%02x" % (sample(idx), sample(idx + 1),
                                              sample(idx + 2)))
            else:                           # palette (3)
                pi = sample(idx) * 3
                if pi + 2 < len(palette):
                    row.append("#%02x%02x%02x" % (palette[pi],
                                                  palette[pi + 1],
                                                  palette[pi + 2]))
                else:
                    row.append("#000000")
        png_rows.append(row)
    return (width, height, png_rows)


def resize_rows(rows, out_size):
    """Nearest-neighbour resize a square-ish grid of rows to out_size x out_size.

    Keeps the output cheap and dependency-free (no PIL).  Returns a new list of
    `out_size` rows, each with exactly `out_size` entries.
    """
    out_size = int(out_size)
    if out_size <= 0 or not rows:
        return rows
    src_h = len(rows)
    src_w = len(rows[0]) if rows[0] else 0
    if src_w == out_size and src_h == out_size:
        return rows
    out = []
    for y in range(out_size):
        sy = min(src_h - 1, y * src_h // out_size)
        srow = rows[sy]
        if src_w == out_size:
            out.append(list(srow))
            continue
        out.append([srow[min(src_w - 1, x * src_w // out_size)]
                    for x in range(out_size)])
    return out


# --------------------------------------------------------------------------- #
#  3) Deterministic background colour from a name
# --------------------------------------------------------------------------- #
def account_color(name):
    """Deterministic pleasing hex background colour derived from name's hash."""
    import hashlib
    digest = hashlib.md5((name or "").encode("utf-8")).digest()
    # A small curated palette of pleasant, distinct colours.
    palette = [
        "#1f6feb", "#238636", "#bc4c00", "#bf8700", "#da3633",
        "#8957e5", "#0a8e7a", "#b35900", "#1a7f37", "#395773",
    ]
    return palette[digest[0] % len(palette)]


# --------------------------------------------------------------------------- #
#  4) High-level convenience used by the GUI
# --------------------------------------------------------------------------- #
def _initial_data(name):
    return {
        "kind": "initial",
        "initial": ((name or "")[:1].upper() if name else "?"),
        "bg": account_color(name),
        "fg": "#ffffff",
    }


def _rows_from_png(png, size):
    """Decode `png` and return exactly `size` x `size` hex rows, or None."""
    if not png:
        return None
    try:
        _w, _h, rows = png_to_rgba_rows(png)
        if rows:
            return resize_rows(rows, size)
    except Exception:
        pass
    return None


def avatar_data(name, size, uuid=None):
    """Return avatar display data. Never raises.

    Tries, in order: mineatar.io (by UUID, premium accounts), then mc-heads.net
    (by username, works for offline accounts too).  On success:

        {"kind": "face", "rows": <size x size list>}.

    On failure:

        {"kind": "initial", "initial": <'A' or '?'>,
         "bg": <hex>, "fg": "#ffffff"}.
    """
    try:
        size = int(size)
    except Exception:
        size = 32

    rows = _rows_from_png(fetch_mineatar_png(uuid, size), size) if uuid else None
    if rows is None:
        rows = _rows_from_png(fetch_avatar_png(name, size), size)
    if rows is not None:
        return {"kind": "face", "rows": rows}
    return _initial_data(name)


# --------------------------------------------------------------------------- #
#  Self-test (offline, no network)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import struct as _struct
    import zlib as _zlib

    def _chunk(ctype, payload):
        body = ctype + payload
        return _struct.pack(">I", len(payload)) + body + \
            _struct.pack(">I", _zlib.crc32(body) & 0xFFFFFFFF)

    def _make_test_png(width, height, pixel_fn, filter_enum=0,
                       color_type=6):
        """Synthesize a valid PNG of the given colour type using the given
        per-pixel function.

        Every row uses the requested filter type (guaranteed valid because we
        build essentially flat/derived rows and reverse-encode the filter), so
        decoding exercises each predictor exactly.
        """
        bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
        stride = width * bpp
        rows = []
        prev = b"\x00" * stride  # previous reconstructed (= original) row
        for y in range(height):
            orig = bytearray()
            for x in range(width):
                px = pixel_fn(x, y)
                if color_type in (0, 4):
                    orig += bytes((px[0] & 0xFF,))
                elif color_type == 2:
                    orig += bytes((px[0] & 0xFF, px[1] & 0xFF, px[2] & 0xFF))
                else:
                    orig += bytes((px[0] & 0xFF, px[1] & 0xFF,
                                   px[2] & 0xFF, px[3] & 0xFF))
            filt = bytearray(orig)
            # PNG filtering subtracts the *original* neighbour bytes.
            for i in range(stride):
                left = orig[i - bpp] if i >= bpp else 0
                up = prev[i]
                ul = prev[i - bpp] if i >= bpp else 0
                v = orig[i]
                if filter_enum == 1:
                    filt[i] = (v - left) & 0xFF
                elif filter_enum == 2:
                    filt[i] = (v - up) & 0xFF
                elif filter_enum == 3:
                    filt[i] = (v - (left + up) // 2) & 0xFF
                elif filter_enum == 4:
                    filt[i] = (v - _paeth(left, up, ul)) & 0xFF
            rows.append(bytes([filter_enum]) + bytes(filt))
            prev = bytes(orig)
        raw = b"".join(rows)

        ihdr = _struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
        return (_PNG_SIGNATURE
                + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", _zlib.compress(raw))
                + _chunk(b"IEND", b""))

    # Simple per-pixel function: a vertical gradient-ish checkered grid.
    def pixel_fn(x, y):
        r = (x * 37) & 0xFF
        g = (y * 53) & 0xFF
        b = ((x + y) * 11) & 0xFF
        return (r, g, b, 255)

    print("Testing PNG filter reconstruction (png_to_rgba_rows)...")
    for filt in (0, 1, 2, 3, 4):
        w, h = 16, 16
        png = _make_test_png(w, h, pixel_fn, filter_enum=filt)
        pw, ph, rows = png_to_rgba_rows(png)
        assert (pw, ph) == (w, h), "bad size for filter %d: %r" % (filt, (pw, ph))
        assert len(rows) == h and len(rows[0]) == w, "bad dims for filter %d" % filt
        # Centre pixel must round-trip to its expected hex colour.
        er, eg, eb, ea = pixel_fn(w // 2, h // 2)
        expect = "#%02x%02x%02x" % (er, eg, eb)
        assert rows[h // 2][w // 2] == expect, \
            "colour mismatch for filter %d: %s != %s" % (filt, rows[h // 2][w // 2], expect)
    print("All PNG filter types round-tripped correctly.")

    print("Testing RGB (colour type 2) decode, as returned by mineatar.io...")
    png_rgb = _make_test_png(24, 24, pixel_fn, color_type=2)
    pw, ph, rows = png_to_rgba_rows(png_rgb)
    er, eg, eb, ea = pixel_fn(24 // 2, 24 // 2)
    assert rows[12][12] == "#%02x%02x%02x" % (er, eg, eb), rows[12][12]
    print("RGB decode OK.")

    print("Testing nearest-neighbour resize_rows...")
    small = [[("#%02x0000" % (x * 10)) for x in range(8)] for _ in range(8)]
    resized = resize_rows(small, 20)
    assert len(resized) == 20 and len(resized[0]) == 20, "bad resize dims"
    print("resize_rows OK.")

    # ---- unsupported input should fail gracefully ------------------------ #
    try:
        png_to_rgba_rows(b"not a png")
        raise AssertionError("png_to_rgba_rows should reject bad signature")
    except ValueError:
        pass

    # ---- avatar_data fallback (no network required) ---------------------- #
    # A name whose PNG fails to validate forces the "initial" branch without
    # needing network: monkeypatch fetch_avatar_png to return junk.
    _real_fetch = fetch_avatar_png
    fetch_avatar_png = lambda name, size=128: b"garbage-not-png"  # noqa: E731
    try:
        d = avatar_data("alex", 32)
        assert d["kind"] == "initial" and d["initial"] == "A", d
        d2 = avatar_data("", 8)
        assert d2["kind"] == "initial" and d2["initial"] == "?", d2
    finally:
        fetch_avatar_png = _real_fetch

    assert account_color("alex") == account_color("alex")
    assert isinstance(account_color("alex"), str)

    print("avatar_data fallback + account_color OK.")
    print("avatar.py self-test passed.")
