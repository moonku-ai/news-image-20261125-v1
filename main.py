#!/usr/bin/env python3
"""THE WORLD IN ONE DAY - Wednesday 25 November 2026.

A program that draws one newspaper front page summarising the day's world
news and writes it to /output/entry.png (1024x1024, at most 1 MiB).
Pure Python standard library, no network, fully deterministic: the same
bytes on every run.

Run:  MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import os
import struct
import sys
import zlib

W, H = 1024, 1024

PAPER = (244, 240, 228)
INK = (22, 19, 14)
RED = (180, 35, 24)
TEAL = (14, 107, 92)
STEEL = (47, 93, 124)
GOLD = (166, 124, 0)
GRAY = (96, 91, 78)
FAINT = (203, 196, 178)
PANEL = (22, 19, 14)
PANEL_SOFT = (43, 39, 32)
MAP_FILL = (196, 205, 196)
MAP_LINE = (120, 128, 120)

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- atlas ---

class Atlas:
    def __init__(self, path):
        raw = open(path, "rb").read()
        assert raw[:4] == b"FNT3", "bad atlas magic"
        n_glyphs, n_runs = struct.unpack_from("<II", raw, 4)
        p = 12
        self.runs = {}
        for _ in range(n_runs):
            ln = raw[p]
            p += 1
            name = raw[p:p + ln].decode()
            p += ln
            px, asc, desc, lh = struct.unpack_from("<HhhH", raw, p)
            p += 8
            self.runs[name] = {
                "px": px, "ascent": asc, "descent": desc, "line": lh,
                "glyphs": {},
            }
        dir_off = p
        for i in range(n_glyphs):
            cp, ri, adv, dx, dy, w, h, off = struct.unpack_from(
                "<IHhhhHHI", raw, dir_off + 20 * i)
            name = list(self.runs)[ri]
            self.runs[name]["glyphs"][cp] = (adv, dx, dy, w, h, off)
        self.bitmaps = raw[dir_off + 20 * n_glyphs:]

    def glyph(self, run, ch):
        return self.runs[run]["glyphs"].get(ord(ch))

    def width(self, run, text):
        total = 0
        for ch in text:
            g = self.glyph(run, ch)
            if g:
                total += g[0]
        return total

    def line_height(self, run):
        return self.runs[run]["line"]


# --------------------------------------------------------------- canvas ---

class Canvas:
    def __init__(self, w, h, bg):
        self.w, self.h = w, h
        self.px = bytearray(bg * (w * h))

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.px[i:i + 3] = bytes(color)

    def blend(self, x, y, color, alpha):
        """Alpha-blend a foreground color over the pixel (alpha 0..255)."""
        if alpha <= 0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        if alpha >= 255:
            self.set(x, y, color)
            return
        i = (y * self.w + x) * 3
        a = alpha / 255.0
        for k in range(3):
            self.px[i + k] = int(color[k] * a + self.px[i + k] * (1 - a) + 0.5)

    def fill_rect(self, x0, y0, x1, y1, color):
        x0 = max(0, x0)
        y0 = max(0, y0)
        x1 = min(self.w, x1)
        y1 = min(self.h, y1)
        row = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            i = (y * self.w + x0) * 3
            self.px[i:i + len(row)] = row

    def rect(self, x0, y0, x1, y1, color, t=1):
        self.fill_rect(x0, y0, x1, y0 + t, color)
        self.fill_rect(x0, y1 - t, x1, y1, color)
        self.fill_rect(x0, y0, x0 + t, y1, color)
        self.fill_rect(x1 - t, y0, x1, y1, color)

    def hline(self, x0, x1, y, color, t=1):
        self.fill_rect(x0, y, x1, y + t, color)

    def vline(self, x, y0, y1, color, t=1):
        self.fill_rect(x, y0, x + t, y1, color)

    def line(self, x0, y0, x1, y1, color, t=1):
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        x, y = x0, y0
        while True:
            self.fill_rect(x - t // 2, y - t // 2, x + t // 2 + 1,
                           y + t // 2 + 1, color)
            if x == x1 and y == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x += sx
            if e2 <= dx:
                err += dx
                y += sy

    def circle(self, cx, cy, r, color, filled=True, t=1):
        if filled:
            for y in range(cy - r, cy + r + 1):
                span = int((r * r - (y - cy) ** 2) ** 0.5) if abs(y - cy) <= r else 0
                self.fill_rect(cx - span, y, cx + span + 1, y + 1, color)
        else:
            import math
            for a in range(360):
                rad = math.radians(a)
                self.fill_rect(int(cx + r * math.cos(rad)) - t // 2,
                               int(cy + r * math.sin(rad)) - t // 2,
                               int(cx + r * math.cos(rad)) + t // 2 + 1,
                               int(cy + r * math.sin(rad)) + t // 2 + 1,
                               color)

    def poly(self, pts, color):
        """Fill a polygon (even-odd scanline)."""
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        for y in range(max(0, min(ys)), min(self.h, max(ys) + 1)):
            xs = []
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                if y0 == y1:
                    continue
                if (y0 <= y < y1) or (y1 <= y < y0):
                    xs.append(x0 + (y - y0) * (x1 - x0) / (y1 - y0))
            xs.sort()
            for i in range(0, len(xs) - 1, 2):
                a = max(0, int(xs[i] + 0.5))
                b = min(self.w, int(xs[i + 1] + 0.5))
                self.fill_rect(a, y, b, y + 1, color)

    def polyline(self, pts, color, t=1):
        for a, b in zip(pts, pts[1:]):
            self.line(a[0], a[1], b[0], b[1], color, t)

    def arc(self, cx, cy, r, a0, a1, color, t=1):
        import math
        pts = []
        steps = max(8, int(abs(a1 - a0) / 3))
        for i in range(steps + 1):
            a = math.radians(a0 + (a1 - a0) * i / steps)
            pts.append((int(cx + r * math.cos(a)), int(cy + r * math.sin(a))))
        self.polyline(pts, color, t)

    def round_rect(self, x0, y0, x1, y1, r, color, filled=True, t=2):
        if filled:
            self.fill_rect(x0 + r, y0, x1 - r, y1, color)
            self.fill_rect(x0, y0 + r, x1, y1 - r, color)
            for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r),
                           (x0 + r, y1 - r), (x1 - r, y1 - r)):
                self.circle(cx, cy, r, color)
        else:
            self.hline(x0 + r, x1 - r, y0, color, t)
            self.hline(x0 + r, x1 - r, y1 - t, color, t)
            self.vline(x0, y0 + r, y1 - r, color, t)
            self.vline(x1 - t, y0 + r, y1 - r, color, t)
            self.arc(x0 + r, y0 + r, r, 180, 270, color, t)
            self.arc(x1 - r, y0 + r, r, 270, 360, color, t)
            self.arc(x0 + r, y1 - r, r, 90, 180, color, t)
            self.arc(x1 - r, y1 - r, r, 0, 90, color, t)


# ----------------------------------------------------------------- text ---

class Typeset:
    def __init__(self, canvas, atlas):
        self.c = canvas
        self.a = atlas

    def draw(self, run, x, y_base, text, color):
        cx = x
        for ch in text:
            g = self.a.glyph(run, ch)
            if g is None:
                continue
            adv, dx, dy, w, h, off = g
            if w and h:
                for row in range(h):
                    base = off + row * w
                    for col in range(w):
                        alpha = self.a.bitmaps[base + col]
                        if alpha:
                            self.c.blend(cx + dx + col, y_base - dy + row,
                                         color, alpha)
            cx += adv
        return cx

    def width(self, run, text):
        return self.a.width(run, text)

    def wrap(self, run, text, max_w):
        lines = []
        for word in text.split(" "):
            if not lines:
                lines.append(word)
                continue
            trial = lines[-1] + " " + word
            if self.width(run, trial) <= max_w:
                lines[-1] = trial
            else:
                lines.append(word)
        return lines

    def para(self, run, x, y, text, max_w, color, leading=None):
        lh = leading or self.a.line_height(run)
        for line in self.wrap(run, text, max_w):
            self.draw(run, x, y, line, color)
            y += lh
        return y


# ----------------------------------------------------------- illustrations ---

def draw_lock(c, x0, y0, x1, y1):
    """A padlock with a broken shackle: the hero illustration."""
    import math
    cx = (x0 + x1) // 2
    body_y0 = y0 + 78
    body_y1 = y1 - 8
    # Signal arcs behind the lock.
    for r, col in ((150, PANEL_SOFT), (120, PANEL_SOFT), (90, PANEL_SOFT)):
        c.arc(cx, body_y0 + 40, r, 200, 340, col, 3)
    # Shackle: left upright + top arc, right side broken off.
    c.line(cx - 52, body_y0 + 6, cx - 52, y0 + 52, GOLD, 9)
    c.arc(cx, y0 + 52, 52, 180, 270, GOLD, 9)
    c.line(cx + 52, y0 + 52, cx + 52, y0 + 96, GOLD, 9)
    # Broken stub on the right, tilted away.
    c.line(cx + 52, y0 + 96, cx + 78, y0 + 118, GOLD, 9)
    c.line(cx + 66, y0 + 118, cx + 44, y0 + 96, RED, 5)
    # Body.
    c.round_rect(x0 + 18, body_y0, x1 - 18, body_y1, 14, GOLD, filled=False, t=5)
    c.round_rect(x0 + 26, body_y0 + 8, x1 - 26, body_y1 - 8, 10, TEAL,
                 filled=False, t=2)
    # Keyhole.
    c.circle(cx, body_y0 + 52, 13, PAPER)
    c.poly([(cx - 9, body_y0 + 60), (cx + 9, body_y0 + 60),
            (cx + 4, body_y0 + 92), (cx - 4, body_y0 + 92)], PAPER)


def draw_gavel(c, x0, y0, x1, y1):
    cx = (x0 + x1) // 2
    bh = y1 - y0
    # Head: two horizontal blocks near the top.
    c.fill_rect(cx - 48, y0 + 6, cx + 48, y0 + 6 + max(8, bh // 8), INK)
    c.fill_rect(cx - 48, y0 + 6 + max(8, bh // 8) + 3,
                cx + 48, y0 + 6 + max(8, bh // 8) + 9, RED)
    hy = y0 + 6 + max(8, bh // 8) + 10
    # Handle: vertical, slightly tapered.
    c.poly([(cx - 7, hy), (cx + 7, hy), (cx + 5, y1 - 18),
            (cx - 5, y1 - 18)], (122, 82, 40))
    # Sound block.
    c.fill_rect(cx - 52, y1 - 16, cx + 52, y1 - 6, INK)
    c.fill_rect(cx - 44, y1 - 22, cx + 44, y1 - 16, GRAY)


def draw_ship(c, x0, y0, x1, y1):
    # Hull, deep enough to read as a ship.
    c.poly([(x0 + 8, y0 + 56), (x1 - 8, y0 + 56), (x1 - 34, y1 - 30),
            (x0 + 34, y1 - 30)], STEEL)
    c.hline(x0 + 8, x1 - 8, y0 + 56, INK, 2)
    # Containers.
    cols = [RED, TEAL, GOLD, STEEL]
    for i, col in enumerate(cols):
        c.fill_rect(x0 + 22 + i * 34, y0 + 22, x0 + 52 + i * 34, y0 + 50, col)
        c.rect(x0 + 22 + i * 34, y0 + 22, x0 + 52 + i * 34, y0 + 50, INK, 1)
    # Bridge.
    c.fill_rect(x1 - 62, y0 + 8, x1 - 22, y0 + 50, PAPER)
    c.rect(x1 - 62, y0 + 8, x1 - 22, y0 + 50, INK, 2)
    c.fill_rect(x1 - 56, y0 + 14, x1 - 28, y0 + 26, STEEL)
    # Waves.
    for wy in (y1 - 24, y1 - 14):
        for wx in range(x0 + 4, x1 - 4, 26):
            c.arc(wx + 13, wy, 13, 180, 360, STEEL, 3)


def draw_tower(c, x0, y0, x1, y1):
    cx = (x0 + x1) // 2
    top = y0 + 14
    base = y1 - 8
    # Spire.
    c.line(cx, top, cx, y0 + 2, INK, 4)
    c.circle(cx, y0 + 2, 4, RED)
    # Tapered tower body.
    c.poly([(cx - 30, base), (cx - 12, top), (cx + 12, top),
            (cx + 30, base)], STEEL)
    # Windows, three rows inside the body.
    for row in range(3):
        wy = top + 10 + row * 13
        frac = (wy - top) / max(1, base - top)
        half = int(12 + 18 * frac) - 4
        for wx in range(cx - half, cx + half + 1, 11):
            c.fill_rect(wx, wy, wx + 5, wy + 7, GOLD)
    # Ground line + blast marks.
    c.hline(x0 + 6, x1 - 6, base + 1, INK, 3)
    c.line(cx - 46, top + 26, cx - 60, top + 12, RED, 3)
    c.line(cx + 46, top + 26, cx + 60, top + 12, RED, 3)


def draw_cross(c, x0, y0, x1, y1):
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    c.circle(cx, cy, 52, TEAL, filled=False, t=4)
    c.circle(cx, cy, 44, TEAL, filled=False, t=1)
    c.fill_rect(cx - 10, cy - 30, cx + 10, cy + 30, RED)
    c.fill_rect(cx - 30, cy - 10, cx + 30, cy + 10, RED)


# ------------------------------------------------------------------ map ---

# Rough continent outlines in (lon, lat); equirectangular, drawn from memory.
CONTINENTS = [
    [(-168, 66), (-140, 70), (-120, 72), (-95, 80), (-80, 73), (-60, 60),
     (-55, 47), (-70, 42), (-75, 35), (-80, 26), (-90, 21), (-97, 16),
     (-105, 20), (-115, 28), (-125, 40), (-125, 49), (-140, 60), (-165, 60)],
    [(-45, 60), (-30, 68), (-20, 70), (-30, 76), (-55, 76), (-58, 70)],
    [(-80, 10), (-70, 12), (-60, 8), (-50, 0), (-40, -5), (-35, -10),
     (-40, -20), (-48, -28), (-58, -35), (-65, -41), (-70, -50), (-73, -40),
     (-75, -25), (-80, -15), (-81, -5)],
    [(-17, 15), (-10, 30), (0, 36), (10, 37), (20, 32), (30, 31), (35, 28),
     (43, 12), (51, 11), (48, 0), (40, -10), (35, -20), (30, -30),
     (20, -35), (15, -30), (12, -18), (9, -1), (9, 4), (-8, 4), (-13, 8)],
    [(-10, 36), (-9, 43), (-5, 48), (-2, 50), (2, 51), (5, 53), (8, 57),
     (10, 59), (18, 56), (21, 55), (24, 58), (28, 60), (30, 62), (25, 65),
     (20, 70), (12, 70), (5, 60), (0, 55), (-5, 50), (-10, 44)],
    [(30, 62), (40, 68), (55, 68), (70, 72), (90, 75), (110, 76), (130, 72),
     (150, 70), (170, 66), (178, 65), (170, 60), (160, 55), (150, 45),
     (140, 40), (135, 35), (130, 33), (127, 35), (122, 30), (120, 25),
     (115, 20), (108, 10), (105, 5), (100, 8), (95, 15), (90, 22),
     (85, 20), (80, 10), (75, 8), (72, 20), (65, 25), (60, 25), (55, 27),
     (50, 30), (45, 30), (40, 35), (35, 36), (30, 40), (28, 45), (30, 55)],
    [(113, -22), (114, -30), (115, -35), (124, -33), (130, -32), (137, -35),
     (140, -38), (145, -38), (150, -37), (153, -32), (153, -27), (150, -22),
     (146, -19), (142, -15), (142, -11), (136, -12), (135, -15), (130, -13),
     (126, -14), (122, -17), (118, -20)],
]

# (number, lon, lat, label, (dx, dy) offset in px for the numbered pin)
PINS = [
    (1, -77, 39, "Washington", (-18, 12)),
    (2, -74, 41, "New York", (18, -10)),
    (3, 24, 60, "Gulf of Finland", (24, -16)),
    (4, 19, 52, "Warsaw", (18, 18)),
    (5, 47, 25, "Riyadh", (0, -18)),
    (6, 29, 2, "E. Congo", (0, 0)),
    (7, 77, 29, "New Delhi", (0, -16)),
    (8, 0, 51, "London", (-24, -10)),
]


def draw_map(c, t, x0, y0, x1, y1):
    c.fill_rect(x0, y0, x1, y1, (238, 234, 220))
    c.rect(x0, y0, x1, y1, INK, 1)
    mw, mh = x1 - x0, y1 - y0

    def proj(lon, lat):
        return (x0 + int((lon + 180) / 360 * mw),
                y0 + int((75 - lat) / 135 * mh))

    for cont in CONTINENTS:
        pts = [proj(lon, lat) for lon, lat in cont]
        c.poly(pts, MAP_FILL)
        c.polyline(pts + [pts[0]], MAP_LINE, 1)
    for num, lon, lat, label, (dx, dy) in PINS:
        px, py = proj(lon, lat)
        qx, qy = px + dx, py + dy
        c.circle(px, py, 3, RED)
        if dx or dy:
            c.line(px, py, qx, qy, RED, 1)
        c.circle(qx, qy, 11, RED)
        c.circle(qx, qy, 11, PAPER, filled=False, t=1)
        t.draw("kick", qx - 3 if num < 10 else qx - 6, qy + 4, str(num), PAPER)
    # Key in two short lines under the map.
    key1 = "  ".join("%d %s" % (n, lab) for n, _, _, lab, _ in PINS[:4])
    key2 = "  ".join("%d %s" % (n, lab) for n, _, _, lab, _ in PINS[4:])
    t.draw("small", x0 + 2, y1 + 16, key1, GRAY)
    t.draw("small", x0 + 2, y1 + 30, key2, GRAY)


# ----------------------------------------------------------------- png ---

def write_png(path, canvas):
    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        return out + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    raw = bytearray()
    stride = canvas.w * 3
    for y in range(canvas.h):
        raw.append(0)
        raw += canvas.px[y * stride:(y + 1) * stride]
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", canvas.w, canvas.h, 8, 2,
                                      0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)
    os.chmod(path, 0o644)
    return len(png)


# -------------------------------------------------------------- layout ---

def build():
    atlas = Atlas(os.path.join(HERE, "atlas.bin"))
    c = Canvas(W, H, PAPER)
    t = Typeset(c, atlas)
    MX = 36
    RX = W - MX

    # Masthead.
    title = "THE WORLD IN ONE DAY"
    tw = t.width("mast", title)
    t.draw("mast", (W - tw) // 2, 78, title, INK)
    t.draw("kick", MX, 104, "WEDNESDAY 25 NOVEMBER 2026", INK)
    right = "WORLD EDITION"
    t.draw("kick", RX - t.width("kick", right), 104, right, GRAY)
    c.hline(MX, RX, 114, INK, 3)
    c.hline(MX, RX, 119, INK, 1)

    # Hero panel: the breach.
    HY0, HY1 = 128, 384
    c.fill_rect(MX, HY0, RX, HY1, PANEL)
    c.hline(MX, RX, HY0, RED, 4)
    t.draw("kick", MX + 24, HY0 + 30, "CYBERATTACK", RED)
    t.draw("hero", MX + 24, HY0 + 104, "THE BREACH", PAPER)
    deck = ("Hackers broke into the CIA and the office of the director of "
            "national intelligence, reaching unclassified systems used for "
            "open-source analysis. Officials said no classified material "
            "was touched.")
    t.para("deck", MX + 24, HY0 + 140, deck, 560, (226, 221, 205), leading=26)
    t.draw("small", MX + 24, HY1 - 24,
           "FIRST REPORTED BY THE NEW YORK TIMES  ·  CIA DECLINED COMMENT",
           (170, 164, 148))
    draw_lock(c, 680, HY0 + 14, 964, HY1 - 40)
    t.draw("small", 680, HY1 - 24,
           "ALSO: FBI DATABASE HACKED FRIDAY",
           (170, 164, 148))

    # Four leads.
    LY0 = 396
    cols = [
        ("TECH · VERDICT", "Huawei guilty on 10 of 12 counts",
         "Brooklyn jury convicts Huawei of fraud and stealing US trade "
         "secrets.",
         draw_gavel),
        ("WAR AT SEA", "Kyiv owns strike on Russian ship",
         "Zelenskiy confirms the drone hit on the Sparta II, and a second "
         "strike off Libya.",
         draw_ship),
        ("MIDDLE EAST", "Houthis hit Riyadh's tallest tower",
         "Eleven wounded as the PIF Tower is struck for the second time in "
         "five days.",
         draw_tower),
        ("HEALTH", "WHO flees Ebola hospital in Congo",
         "WHO evacuates Akwe hospital after three days of fighting. Toll: "
         "5,775 dead.",
         draw_cross),
    ]
    cw = (RX - MX - 3 * 16) // 4
    for i, (kick, head, deck, draw) in enumerate(cols):
        x0 = MX + i * (cw + 16)
        x1 = x0 + cw
        c.fill_rect(x0, LY0, x1, LY0 + 84, (238, 234, 220))
        c.rect(x0, LY0, x1, LY0 + 84, FAINT, 1)
        draw(c, x0 + 6, LY0 + 4, x1 - 6, LY0 + 80)
        t.draw("kick", x0, LY0 + 104, kick, RED)
        y = LY0 + 132
        for line in t.wrap("h1", head, cw):
            t.draw("h1", x0, y, line, INK)
            y += 29
        t.para("body", x0, y + 4, deck, cw, (58, 54, 44), leading=17)

    # Markets strip (Tuesday close).
    MY0, MY1 = 654, 692
    c.hline(MX, RX, MY0, INK, 1)
    c.hline(MX, RX, MY1, INK, 1)
    t.draw("kick", MX, MY0 + 24, "TUESDAY CLOSE", GRAY)
    stats = [("NASDAQ", "+6.51%"), ("S&P 500", "+4.73%"),
             ("NIKKEI", "+3.91%"), ("BRENT", "$86.70 -2.99%"),
             ("GOLD", "$3,620.40 -0.85%"), ("SENSEX", "-1,484")]
    sx = MX + 150
    for label, val in stats:
        t.draw("small", sx, MY0 + 17, label, GRAY)
        t.draw("stat", sx, MY0 + 38, val, INK)
        sx += max(t.width("stat", val), t.width("small", label)) + 32

    # Map + briefs.
    BY0 = 704
    t.draw("h2", MX, BY0 + 22, "The world today", INK)
    c.hline(MX, 560, BY0 + 30, INK, 2)
    draw_map(c, t, MX, BY0 + 44, 560, BY0 + 216)
    t.draw("h2", 584, BY0 + 22, "The day in brief", INK)
    c.hline(584, RX, BY0 + 30, INK, 2)
    briefs = [
        ("NATO.", "AWACS jets head to Poland after a Russian drone and "
         "missiles crossed its border."),
        ("Medal.", "Trump awards the Presidential Medal of Freedom to "
         "Rudy Giuliani."),
        ("Cricket.", "England beat Australia by eight wickets in the "
         "second T20, chasing 185."),
        ("Quake.", "A magnitude 5.6 earthquake hits Nepal's Koshi "
         "province, near China."),
        ("Gaza.", "The UN flags deepening humanitarian strain as aid "
         "struggles to reach civilians."),
        ("Cyber.", "FBI database hacked Friday; the NSA network was down "
         "for days last week."),
    ]
    y = BY0 + 52
    for lead, rest in briefs:
        t.draw("bodyb", 584, y, lead, INK)
        lx = 584 + t.width("bodyb", lead) + 6
        y = t.para("body", lx, y, rest, RX - lx, (58, 54, 44), leading=16)
        y += 4

    # Footer.
    c.hline(MX, RX, 970, INK, 1)
    t.draw("small", MX, 992,
           "Sources: CNN, Reuters, AP, Bloomberg, The New York Times, "
           "US Dept. of Justice", GRAY)
    foot_r = "WEDNESDAY 25 NOVEMBER 2026"
    t.draw("small", RX - t.width("small", foot_r), 992, foot_r, GRAY)

    outdir = os.environ.get("MAKEFUN_OUTPUT_DIR", "/output")
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, "entry.png")
    size = write_png(path, c)
    print("wrote %s (%d bytes, %dx%d)" % (path, size, W, H))
    return path


if __name__ == "__main__":
    build()
