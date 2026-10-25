"""The drawing surface for the screens the board draws itself (setup, pairing code, errors):
everything else arrives from the API as a finished frame. Carried over from the earlier shoestore
firmware, where the widgets drew on it.

The image is one 1-bit framebuf per ink colour, which is what the panel takes: a black plane and,
for a black/white/red panel, a red plane. In both a set bit means ink, as in RHF1 frames. On a
black/white panel there's no red plane, and red is drawn as black.

Boxes are (x0, y0, x1, y1) with both corners inside.
"""

import framebuf

from .fonts import font  # noqa: F401  (screens import font from here)

WHITE, BLACK, RED = 0, 1, 2

# Pixels cut from each corner row of a rounded rectangle with radius 4.
_CORNER_4 = (3, 1, 1)


def _palette(value):
    """Turns a glyph's ink into `value` and its background into the
    opposite, which blit() then skips as the key."""
    p = framebuf.FrameBuffer(bytearray(1), 2, 1, framebuf.MONO_HLSB)
    p.pixel(0, 0, 1 - value)
    p.pixel(1, 0, value)
    return p


_INK = (_palette(0), _palette(1))


class Canvas:
    def __init__(self, width=400, height=300, red=True):
        self.width, self.height = width, height
        size = (width + 7) // 8 * height
        self.black_buf = bytearray(size)
        self.black = framebuf.FrameBuffer(self.black_buf, width, height, framebuf.MONO_HLSB)
        self.red_buf = bytearray(size) if red else None
        self.red = framebuf.FrameBuffer(self.red_buf, width, height, framebuf.MONO_HLSB) if red else None

    def _planes(self, color):
        """(framebuf, bit) pairs that paint `color`: ink in its own plane,
        cleared in the other."""
        if self.red is None:
            return ((self.black, 0 if color == WHITE else 1),)
        return ((self.black, 1 if color == BLACK else 0), (self.red, 1 if color == RED else 0))

    # ---------- Shapes ----------

    def rect(self, box, color=BLACK):
        x0, y0, x1, y1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        for fb, bit in self._planes(color):
            fb.fill_rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, bit)

    def outline(self, box, color=BLACK):
        x0, y0, x1, y1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        for fb, bit in self._planes(color):
            fb.rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1, bit)

    def rounded_rect(self, box, color=BLACK):
        """A filled rectangle with corners rounded at radius 4."""
        self.rect(box, color)
        x0, y0, x1, y1 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        for fb, bit in self._planes(WHITE):
            for dy, cut in enumerate(_CORNER_4):
                fb.hline(x0, y0 + dy, cut, bit)
                fb.hline(x1 - cut + 1, y0 + dy, cut, bit)
                fb.hline(x0, y1 - dy, cut, bit)
                fb.hline(x1 - cut + 1, y1 - dy, cut, bit)

    def line(self, xy, color=BLACK, width=1):
        """A line between two points. Wider lines are only needed
        horizontal or vertical, and grow right/down as Pillow's do."""
        (x0, y0), (x1, y1) = xy
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        for fb, bit in self._planes(color):
            if width == 1:
                fb.line(x0, y0, x1, y1, bit)
            elif y0 == y1:
                fb.fill_rect(min(x0, x1), y0, abs(x1 - x0) + 1, width, bit)
            else:
                fb.fill_rect(x0, min(y0, y1), width, abs(y1 - y0) + 1, bit)

    def disc(self, cx, cy, r, color=BLACK):
        """A filled circle of radius r centred on (cx, cy)."""
        for dy in range(-r, r + 1):
            dx = 0
            while (dx + 1) * (dx + 1) + dy * dy <= r * r:
                dx += 1
            for fb, bit in self._planes(color):
                fb.hline(cx - dx, cy + dy, 2 * dx + 1, bit)

    def point(self, xy, color=BLACK):
        for fb, bit in self._planes(color):
            fb.pixel(int(xy[0]), int(xy[1]), bit)

    def glyph(self, g, x, y, color=BLACK):
        """Blit a glyph (from Font.glyph) with its origin at (x, y)."""
        _, x0, y0, fb = g
        if fb is None:
            return
        for plane, bit in self._planes(color):
            plane.blit(fb, x + x0, y + y0, 1 - bit, _INK[bit])

    def bitmap(self, data, w, h, x, y, scale=1, color=BLACK, dither=False):
        """A 1-bit bitmap (MSB = leftmost pixel, 1 = ink, rows padded to bytes), each pixel drawn
        as a scale × scale block: the pixel logo in logo.py. dither draws each block as a
        checkerboard of panel pixels instead (the mud planes)."""
        row = (w + 7) // 8
        for yy in range(h):
            for xx in range(w):
                if data[yy * row + (xx >> 3)] & (0x80 >> (xx & 7)):
                    x0, y0 = x + xx * scale, y + yy * scale
                    if not dither:
                        self.rect((x0, y0, x0 + scale - 1, y0 + scale - 1), color)
                        continue
                    for py in range(y0, y0 + scale):
                        for px in range(x0 + ((x0 + py) & 1), x0 + scale, 2):
                            self.point((px, py), color)

    def logo(self, ink, mud, w, h, x, y, scale=1):
        """A logo from logo.py: its ink plane solid, its mud plane dithered."""
        self.bitmap(ink, w, h, x, y, scale)
        self.bitmap(mud, w, h, x, y, scale, dither=True)

    # ---------- Text ----------

    def text(self, xy, s, font, color=BLACK, anchor="la"):
        """Draw s with Pillow-style anchors: l/m/r across, a (top of the
        ascender), m (middle), s (baseline) or d (descender) down."""
        x, y = xy
        h, v = anchor[0], anchor[1]
        # Rounded like Pillow: the anchor shift and each glyph's pen position
        # separately, to whole pixels. Pillow's output can still differ by a
        # pixel here and there; it clips glyphs to a bounding box that is
        # sometimes a pixel short.
        left = int(x)
        if h != "l":
            w = font.advance64(s)
            left -= ((w if h == "r" else w // 2) + 32) >> 6
        if v == "a":
            y += font.ascent
        elif v == "m":
            y += (font.ascent - font.descent + 1) // 2
        elif v == "d":
            y -= font.descent
        base = int(y)
        pen = 0
        for ch in s:
            g = font.glyph(ord(ch))
            self.glyph(g, left + ((pen + 32) >> 6), base, color)
            pen += g[0]

    def width_of(self, s, font):
        return font.advance64(s) / 64

    def wrap(self, text, font, max_width, max_lines):
        """Break text into at most max_lines lines no wider than max_width.
        Breaks at spaces where it can, mid-word where it must, and ends the
        last line with an ellipsis if there's more."""
        lines = []
        line = ""
        for word in text.split():
            while self.width_of(word, font) > max_width:  # a word too long for a line of its own
                if line:
                    lines.append(line)
                    line = ""
                cut = len(word)
                while cut > 1 and self.width_of(word[:cut], font) > max_width:
                    cut -= 1
                lines.append(word[:cut])
                word = word[cut:]
            candidate = word if not line else line + " " + word
            if self.width_of(candidate, font) <= max_width:
                line = candidate
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = self.fit(lines[-1] + "…", font, max_width)
        return lines

    def fit(self, text, font, max_width):
        """Shorten text with an ellipsis until it fits in max_width."""
        if self.width_of(text, font) <= max_width:
            return text
        while text and self.width_of(text + "…", font) > max_width:
            text = text[:-1]
        return text + "…"
