#!/usr/bin/env python3
"""Build a glyph atlas (atlas.bin) from system TTF fonts.

Reads TrueType outlines (cmap / glyf / loca / hmtx), flattens the quadratic
curves and rasterizes each glyph with 4x vertical supersampling into a
grayscale bitmap. Output format (all little-endian):

  magic "FNT3", u32 n_glyphs, u32 n_runs
  per run: u8 name_len, name bytes, u16 px, i16 ascent, i16 descent, u16 line
  per glyph (20 bytes): u32 codepoint, u16 run, i16 advance, i16 dx, i16 dy,
      u16 w, u16 h, u32 bitmap_offset
  bitmap bytes: one byte per pixel, row major, w*h per glyph.

dy is the distance from the text baseline up to the top row of the bitmap.
dx is the horizontal offset from the pen position to the bitmap's left edge.
advance is the pen advance in pixels. A space has w=h=0 but a real advance.

Usage: python3 tools/make_atlas.py [out/atlas.bin]
"""

import struct
import sys

SERIF = "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"
SERIFB = "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
SANSB = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# (name, font file, pixel size)
RUNS = [
    ("mast", SERIFB, 54),
    ("hero", SERIFB, 76),
    ("h1", SERIFB, 29),
    ("h2", SERIFB, 22),
    ("deck", SERIF, 20),
    ("body", SANS, 14),
    ("bodyb", SANSB, 14),
    ("kick", SANSB, 13),
    ("small", SANS, 12),
    ("stat", SERIFB, 24),
]

EXTRA = "\u2014\u2013\u2018\u2019\u201c\u201d\u2022\u2026\u00b7\u25b2\u25bc\u00e9"
CHARSET = "".join(chr(c) for c in range(32, 127)) + EXTRA


def u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def i16(b, o):
    return struct.unpack_from(">h", b, o)[0]


def u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


class Font:
    def __init__(self, path):
        self.data = open(path, "rb").read()
        d = self.data
        n = u16(d, 4)
        self.tables = {}
        for i in range(n):
            o = 12 + i * 16
            tag = d[o:o + 4].decode("latin1")
            self.tables[tag] = (u32(d, o + 8), u32(d, o + 12))
        head = self.tables["head"]
        self.units = u16(d, head[0] + 18)
        self.loc_fmt = i16(d, head[0] + 50)
        maxp = self.tables["maxp"]
        self.num_glyphs = u16(d, maxp[0] + 4)
        hhea = self.tables["hhea"]
        self.num_hmetrics = u16(d, hhea[0] + 34)
        self.ascent = i16(d, hhea[0] + 4)
        self.descent = i16(d, hhea[0] + 6)
        self.cmap = self._build_cmap()

    def _table(self, tag):
        o, ln = self.tables[tag]
        return self.data[o:o + ln]

    def _build_cmap(self):
        c = self._table("cmap")
        nsub = u16(c, 2)
        best4 = best12 = None
        for i in range(nsub):
            plat, enc = u16(c, 4 + i * 8), u16(c, 6 + i * 8)
            off = u32(c, 8 + i * 8)
            fmt = u16(c, off)
            if fmt == 12 and (plat, enc) in ((3, 10), (0, 4), (0, 3)):
                best12 = off
            if fmt == 4 and (plat, enc) in ((3, 1), (0, 3), (0, 1)):
                best4 = off
        m = {}
        if best12 is not None:
            ngroups = u32(c, best12 + 12)
            for i in range(ngroups):
                o = best12 + 16 + i * 12
                s, e, g = u32(c, o), u32(c, o + 4), u32(c, o + 8)
                for cp in range(s, e + 1):
                    m[cp] = g + (cp - s)
            return m
        o = best4
        seg = u16(c, o + 6) // 2
        end_off = o + 14
        start_off = end_off + 2 * seg + 2
        delta_off = start_off + 2 * seg
        range_off = delta_off + 2 * seg
        for i in range(seg):
            end = u16(c, end_off + 2 * i)
            start = u16(c, start_off + 2 * i)
            delta = i16(c, delta_off + 2 * i)
            ro = u16(c, range_off + 2 * i)
            for cp in range(start, end + 1):
                if cp == 0xFFFF:
                    continue
                if ro == 0:
                    m[cp] = (cp + delta) % 65536
                else:
                    go = range_off + 2 * i + ro + 2 * (cp - start)
                    m[cp] = u16(c, go)
        return m

    def glyph_outline(self, gid):
        """Return list of contours; each contour is a list of (x, y, on_curve)."""
        loca = self._table("loca")
        if self.loc_fmt == 0:
            g0 = u16(loca, gid * 2) * 2
            g1 = u16(loca, gid * 2 + 2) * 2
        else:
            g0 = u32(loca, gid * 4)
            g1 = u32(loca, gid * 4 + 4)
        if g0 == g1:
            return []
        glyf = self._table("glyf")
        g = glyf[g0:g1]
        ncont = i16(g, 0)
        if ncont >= 0:
            return self._simple(g, ncont)
        return self._composite(g)

    def _simple(self, g, ncont):
        ends = [u16(g, 10 + 2 * i) for i in range(ncont)]
        npts = ends[-1] + 1 if ends else 0
        ilen = u16(g, 10 + 2 * ncont)
        p = 10 + 2 * ncont + 2 + ilen
        flags = []
        while len(flags) < npts:
            f = g[p]
            p += 1
            flags.append(f)
            if f & 0x08:
                rep = g[p]
                p += 1
                flags.extend([f] * rep)
        xs = []
        x = 0
        for f in flags:
            if f & 0x02:
                dx = g[p]
                p += 1
                x += -dx if (f & 0x10) == 0 else dx
            elif (f & 0x10) == 0:
                dx = i16(g, p)
                p += 2
                x += dx
            xs.append(x)
        ys = []
        y = 0
        for f in flags:
            if f & 0x04:
                dy = g[p]
                p += 1
                y += -dy if (f & 0x20) == 0 else dy
            elif (f & 0x20) == 0:
                dy = i16(g, p)
                p += 2
                y += dy
            ys.append(y)
        contours = []
        start = 0
        for end in ends:
            pts = [(xs[i], ys[i], bool(flags[i] & 0x01))
                   for i in range(start, end + 1)]
            contours.append(pts)
            start = end + 1
        return contours

    def _composite(self, g):
        p = 10
        contours = []
        while True:
            flags = u16(g, p)
            comp_gid = u16(g, p + 2)
            p += 4
            if flags & 0x0001:  # args are words
                arg1 = i16(g, p)
                arg2 = i16(g, p + 2)
                p += 4
            else:
                arg1 = g[p] if g[p] < 128 else g[p] - 256
                arg2 = g[p + 1] if g[p + 1] < 128 else g[p + 1] - 256
                p += 2
            if flags & 0x0002:  # args are xy values
                dx, dy = arg1, arg2
            else:
                dx = dy = 0
            sx = sy = 1.0
            x01 = y10 = 0.0
            if flags & 0x0008:  # scale
                s = struct.unpack_from(">h", g, p)[0] / 16384.0
                sx = sy = s
                p += 2
            elif flags & 0x0080:  # x/y scale
                sx = struct.unpack_from(">h", g, p)[0] / 16384.0
                sy = struct.unpack_from(">h", g, p + 2)[0] / 16384.0
                p += 4
            elif flags & 0x0040:  # 2x2
                sx = struct.unpack_from(">h", g, p)[0] / 16384.0
                x01 = struct.unpack_from(">h", g, p + 2)[0] / 16384.0
                y10 = struct.unpack_from(">h", g, p + 4)[0] / 16384.0
                sy = struct.unpack_from(">h", g, p + 6)[0] / 16384.0
                p += 8
            sub = self.glyph_outline(comp_gid)
            for contour in sub:
                out = []
                for (x, y, on) in contour:
                    nx = x * sx + y * x01 + dx
                    ny = x * y10 + y * sy + dy
                    out.append((nx, ny, on))
                contours.append(out)
            if not (flags & 0x0020):  # more components
                break
        return contours


def flatten(contour):
    """Expand a contour to a polygon of line segments (quadratic curves split)."""
    pts = list(contour)
    if not pts:
        return []
    # Rotate so the contour starts on-curve.
    if not pts[0][2]:
        # Start at midpoint between last and first point.
        for i, pt in enumerate(pts):
            if pt[2]:
                pts = pts[i:] + pts[:i]
                break
        else:
            # No on-curve points at all: whole contour is one curve.
            pts = [((pts[-1][0] + pts[0][0]) / 2, (pts[-1][1] + pts[0][1]) / 2,
                    True)] + pts
    out = [pts[0][:2]]
    i = 1
    n = len(pts)
    while i < n:
        x0, y0 = out[-1]
        cx, cy, on = pts[i]
        if on:
            out.append((cx, cy))
            i += 1
            continue
        # Quadratic segment to next on-curve point (or midpoint).
        if i + 1 < n and pts[i + 1][2]:
            x1, y1 = pts[i + 1][:2]
            i += 2
        elif i + 1 < n:
            x1 = (cx + pts[i + 1][0]) / 2
            y1 = (cy + pts[i + 1][1]) / 2
            i += 1
        else:
            x1, y1 = pts[0][:2]
            i += 1
        for t in range(1, 9):
            tt = t / 8.0
            mt = 1 - tt
            out.append((mt * mt * x0 + 2 * mt * tt * cx + tt * tt * x1,
                        mt * mt * y0 + 2 * mt * tt * cy + tt * tt * y1))
    if out[-1] != out[0]:
        out.append(out[0])
    return out


def rasterize(contours, scale, pad=1):
    """Rasterize contours (font units) to a grayscale bitmap at `scale`.

    Returns (bitmap bytes, width, height, dx, dy) where dx/dy place the
    bitmap relative to the pen position and the baseline.
    """
    polys = []
    xmin = ymin = 1e18
    xmax = ymax = -1e18
    for contour in contours:
        poly = flatten(contour)
        if len(poly) < 3:
            continue
        polys.append(poly)
        for (x, y) in poly:
            xmin = min(xmin, x)
            xmax = max(xmax, x)
            ymin = min(ymin, y)
            ymax = max(ymax, y)
    if not polys:
        return b"", 0, 0, 0, 0
    # Pixel grid in font units: bitmap covers [xmin-pad, xmax+pad] x
    # [ymin-pad, ymax+pad]; y is flipped (bitmap row 0 is the top).
    fx0 = xmin - pad / scale
    fx1 = xmax + pad / scale
    fy0 = ymin - pad / scale
    fy1 = ymax + pad / scale
    w = max(1, int((fx1 - fx0) * scale + 0.999))
    h = max(1, int((fy1 - fy0) * scale + 0.999))
    # Edges in pixel space (x right, y down).
    edges = []
    for poly in polys:
        for a, b in zip(poly, poly[1:]):
            x0 = (a[0] - fx0) * scale
            y0 = (fy1 - a[1]) * scale
            x1 = (b[0] - fx0) * scale
            y1 = (fy1 - b[1]) * scale
            if y0 == y1:
                continue
            edges.append((x0, y0, x1, y1))
    cov = bytearray(w * h)
    ss = 4  # vertical supersampling rows per pixel row
    for row in range(h):
        acc = [0] * w
        for s in range(ss):
            y = row + (s + 0.5) / ss
            xs = []
            for (x0, y0, x1, y1) in edges:
                if (y0 <= y < y1) or (y1 <= y < y0):
                    t = (y - y0) / (y1 - y0)
                    xs.append(x0 + t * (x1 - x0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                a = max(0, int(xs[i] + 0.5))
                b = min(w, int(xs[i + 1] + 0.5))
                for px in range(a, b):
                    acc[px] += 1
        for px in range(w):
            if acc[px]:
                cov[row * w + px] = min(255, acc[px] * 255 // ss)
    dx = int(fx0 * scale + 0.5) if fx0 >= 0 else -int(-fx0 * scale + 0.5)
    # Baseline: font y=0 maps to bitmap row (fy1 - 0) * scale.
    dy = int(fy1 * scale + 0.5)
    return bytes(cov), w, h, dx, dy


def build(out_path):
    fonts = {}
    glyphs = []  # (codepoint, run_idx, advance, dx, dy, w, h, bitmap)
    for ri, (name, path, px) in enumerate(RUNS):
        if path not in fonts:
            fonts[path] = Font(path)
        f = fonts[path]
        scale = px / f.units
        for ch in CHARSET:
            cp = ord(ch)
            gid = f.cmap.get(cp, 0)
            hmtx = f._table("hmtx")
            gi = min(gid, f.num_hmetrics - 1)
            adv = u16(hmtx, gi * 4)
            advance = int(round(adv * scale))
            if gid == 0:
                glyphs.append((cp, ri, advance, 0, 0, 0, 0, b""))
                continue
            contours = f.glyph_outline(gid)
            bmp, w, h, dx, dy = rasterize(contours, scale)
            glyphs.append((cp, ri, advance, dx, dy, w, h, bmp))
        print(f"run {name} {px}px: {len(CHARSET)} glyphs", file=sys.stderr)
    out = bytearray()
    out += b"FNT3"
    out += struct.pack("<II", len(glyphs), len(RUNS))
    for (name, path, px) in RUNS:
        f = fonts[path]
        scale = px / f.units
        nb = name.encode()
        out += struct.pack("<B", len(nb)) + nb
        out += struct.pack("<HhhH", px, int(round(f.ascent * scale)),
                           int(round(f.descent * scale)),
                           int(round((f.ascent - f.descent) * scale)))
    dir_off = len(out)
    out += b"\x00" * (20 * len(glyphs))
    blobs = bytearray()
    for i, (cp, ri, adv, dx, dy, w, h, bmp) in enumerate(glyphs):
        off = len(blobs)
        blobs += bmp
        struct.pack_into("<I H h h h H H I", out, dir_off + 20 * i,
                         cp, ri, adv, dx, dy, w, h, off)
    out += blobs
    with open(out_path, "wb") as fh:
        fh.write(out)
    print(f"wrote {out_path}: {len(out)} bytes, {len(glyphs)} glyphs",
          file=sys.stderr)


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "atlas.bin")
