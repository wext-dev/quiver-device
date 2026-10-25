"""RHF1 frames from GET /device/v1/frame (docs/device-protocol.md, "Frame format").

A 16-byte little-endian header, then the black plane and, on black/white/red panels, the red
plane. Rows top to bottom, MSB = leftmost pixel, 1 = ink: the same layout as framebuf.MONO_HLSB,
so planes go to the panel (or a framebuf) as they are.

A grey frame (flag GRAY, for screens that report "gray4") has two planes holding a 2-bit level
per pixel (0 white … 3 black): the high bit (dark grey or black) first, then the low bit. Its high
plane alone is the frame in black and white.
"""

import struct

HEADER = 16
GRAY = 0x01  # flags: two planes of grey levels instead of black and red


class FrameError(ValueError):
    pass


class Frame:
    def __init__(self, width, height, stride, black, red, low=None):
        self.width, self.height, self.stride = width, height, stride
        # Memoryviews into the response body. A grey frame has its high plane in black and its
        # low plane in low, and no red.
        self.black, self.red, self.low = black, red, low

    @property
    def gray(self):
        return self.low is not None


def parse(body, width=None, height=None):
    """Checks the header (and, if given, the panel size) and returns a Frame without copying."""
    if len(body) < HEADER:
        raise FrameError("frame too short: %d bytes" % len(body))
    magic, version, planes, flags, _r, w, h, stride, _r2 = struct.unpack_from("<4sBBBBHHHH", body, 0)
    if magic != b"RHF1":
        raise FrameError("not an RHF1 frame")
    if version != 1:
        raise FrameError("frame version %d not supported" % version)
    if planes not in (1, 2) or (flags & GRAY and planes != 2):
        raise FrameError("bad plane count %d" % planes)
    if stride != (w + 7) // 8:
        raise FrameError("bad stride %d for width %d" % (stride, w))
    if (width is not None and w != width) or (height is not None and h != height):
        raise FrameError("frame is %dx%d, panel is %dx%d" % (w, h, width, height))
    size = stride * h
    if len(body) != HEADER + planes * size:
        raise FrameError("frame is %d bytes, expected %d" % (len(body), HEADER + planes * size))
    view = memoryview(body)
    black = view[HEADER:HEADER + size]
    second = view[HEADER + size:HEADER + 2 * size] if planes == 2 else None
    if flags & GRAY:
        return Frame(w, h, stride, black, None, second)
    return Frame(w, h, stride, black, second)
