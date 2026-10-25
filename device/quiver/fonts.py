"""Bitmap fonts made by tools/make_fonts.py from Geist and Geist Mono, the faces the API's frames
use (the file format is described there). A font is read into RAM the first time a screen asks for
it, and each glyph becomes a framebuf the canvas can blit."""

import framebuf
import struct

# Not "fonts/": MicroPython would import a folder of that name instead of this module.
_DIR = __file__.replace("\\", "/").rsplit("/", 1)[0] + "/fontdata/"
_HEADER = 7
_ENTRY = 12
_cache = {}


class Font:
    def __init__(self, data, name="font"):
        self.data = bytearray(data)  # framebuf needs a writable buffer
        magic, _version, self.ascent, self.descent, count = struct.unpack_from("<2sBBBH", self.data, 0)
        if magic != b"SF":
            raise ValueError(name + " is not a font file")
        self._index = {}
        for i in range(count):
            self._index[struct.unpack_from("<H", self.data, _HEADER + i * _ENTRY)[0]] = i
        self._bitmaps = _HEADER + count * _ENTRY
        self._glyphs = {}

    def glyph(self, cp):
        """(advance in 1/64 px, x offset, y offset, framebuf or None) for a
        codepoint; "?" stands in for characters the font doesn't have."""
        g = self._glyphs.get(cp)
        if g is None:
            i = self._index.get(cp)
            if i is None:
                return self.glyph(63) if cp != 63 else (0, 0, 0, None)
            _, adv, x0, y0, w, h, off = struct.unpack_from("<HHbbBBI", self.data, _HEADER + i * _ENTRY)
            fb = None
            if w and h:
                start = self._bitmaps + off
                buf = memoryview(self.data)[start:start + ((w + 7) // 8) * h]
                fb = framebuf.FrameBuffer(buf, w, h, framebuf.MONO_HLSB)
            g = self._glyphs[cp] = (adv, x0, y0, fb)
        return g

    def advance64(self, s):
        return sum(self.glyph(ord(ch))[0] for ch in s)


def _read(name):
    try:
        with open(_DIR + name + ".bin", "rb") as f:
            return f.read()
    except OSError:
        # Frozen into the Quiver runtime there are no files next to the code: runtime/build.sh
        # puts the fonts in a module instead.
        from . import _fontdata
        return _fontdata.FONTS[name]


def _load(name):
    f = _cache.get(name)
    if f is None:
        f = _cache[name] = Font(_read(name), name)
    return f


def font(size, bold=False, mono=False):
    """Geist regular or bold, or Geist Mono bold (mono=True); only the sizes make_fonts.py made."""
    return _load(("m" if mono else "b" if bold else "r") + str(size))
