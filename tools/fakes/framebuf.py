"""A CPython stand-in for the parts of MicroPython's framebuf module the
board code uses, so simulate.py can run the widgets on a PC.

Only MONO_HLSB is supported. Pixels outside the buffer are clipped, as on
the board. blit() compares `key` after the palette lookup, like
MicroPython's modframebuf.c.
"""

MONO_HLSB = 3


class FrameBuffer:
    def __init__(self, buf, width, height, fmt, stride=None):
        if fmt != MONO_HLSB:
            raise ValueError("only MONO_HLSB is simulated")
        self.buf, self.width, self.height = buf, width, height
        self.stride = stride or width
        self._row = (self.stride + 7) // 8

    def pixel(self, x, y, c=None):
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None if c is not None else 0
        i, bit = y * self._row + (x >> 3), 0x80 >> (x & 7)
        if c is None:
            return 1 if self.buf[i] & bit else 0
        if c:
            self.buf[i] |= bit
        else:
            self.buf[i] &= ~bit & 0xFF

    def fill(self, c):
        v = 0xFF if c else 0
        for i in range(self._row * self.height):
            self.buf[i] = v

    def fill_rect(self, x, y, w, h, c):
        for yy in range(max(0, y), min(self.height, y + h)):
            for xx in range(max(0, x), min(self.width, x + w)):
                self.pixel(xx, yy, c)

    def rect(self, x, y, w, h, c, f=False):
        if f:
            self.fill_rect(x, y, w, h, c)
        else:
            self.hline(x, y, w, c)
            self.hline(x, y + h - 1, w, c)
            self.vline(x, y, h, c)
            self.vline(x + w - 1, y, h, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def line(self, x1, y1, x2, y2, c):
        dx, dy = abs(x2 - x1), -abs(y2 - y1)
        sx, sy = (1 if x1 < x2 else -1), (1 if y1 < y2 else -1)
        err = dx + dy
        while True:
            self.pixel(x1, y1, c)
            if x1 == x2 and y1 == y2:
                return
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x1 += sx
            if e2 <= dx:
                err += dx
                y1 += sy

    def blit(self, src, x, y, key=-1, palette=None):
        for sy in range(src.height):
            for sx in range(src.width):
                col = src.pixel(sx, sy)
                if palette is not None:
                    col = palette.pixel(col, 0)
                if col != key:
                    self.pixel(x + sx, y + sy, col)
