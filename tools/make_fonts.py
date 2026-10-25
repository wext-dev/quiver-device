"""Pre-render the fonts the board's own screens draw with (setup, pairing code, errors).

MicroPython has no TrueType renderer, so every glyph is rendered here with Pillow, without
anti-aliasing, and packed into small bitmap files the board blits from:

  device/quiver/fontdata/r15.bin   Geist regular, 15 px (one file per size)
  device/quiver/fontdata/b24.bin   Geist bold, 24 px
  device/quiver/fontdata/m40.bin   Geist Mono bold, 40 px (the pairing code)

The faces are Geist and Geist Mono (SIL Open Font License, https://vercel.com/font), the ones the
API draws its frames with, so the board's screens match them. Run after adding a size or a character
to a screen, with the folder holding the .ttf files:

  python tools/make_fonts.py --fonts <folder with Geist_400Regular.ttf, Geist_700Bold.ttf, GeistMono_700Bold.ttf>

File format, little-endian:
  header  "SF", version (1), ascent, descent, glyph count (u16)
  table   per glyph: codepoint (u16), advance in 1/64 px (u16),
          x and y offset of the bitmap from the pen position on the
          baseline (i8, i8), width, height (u8, u8), bitmap offset (u32)
  bitmaps 1 bit per pixel, rows padded to whole bytes, MSB = leftmost
          pixel, bit set = ink (MicroPython's framebuf.MONO_HLSB)
"""

import argparse
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "device" / "quiver" / "fontdata"
FACES = {"r": "Geist_400Regular.ttf", "b": "Geist_700Bold.ttf", "m": "GeistMono_700Bold.ttf"}

# Every (face, size) the screens ask for: see device/quiver/screens.py.
SIZES = [("r", 12), ("r", 15), ("b", 15), ("b", 24), ("m", 14), ("m", 40)]
CHARSET = "".join(chr(c) for c in range(32, 127)) + "ÆØÅæøåÄÖÜäöüÉéÈèÁáÓóÑñßÇç…–—·°’‘“”«»"


def pack(img):
    """A mode "1" image as MONO_HLSB rows (Pillow's own "1" layout)."""
    return img.tobytes() if img.width and img.height else b""


def write_font(path, ascent, descent, glyphs):
    """glyphs: (codepoint, advance64, x0, y0, image)"""
    table, data = bytearray(), bytearray()
    for cp, adv64, x0, y0, img in glyphs:
        table += struct.pack("<HHbbBBI", cp, adv64, x0, y0, img.width, img.height, len(data))
        data += pack(img)
    path.write_bytes(struct.pack("<2sBBBH", b"SF", 1, ascent, descent, len(glyphs)) + table + data)


def crop(img, ox, oy):
    """Trim to the inked pixels; returns the offset from (ox, oy) and the bitmap."""
    box = img.getbbox()
    if box is None:
        return 0, 0, Image.new("1", (0, 0))
    return box[0] - ox, box[1] - oy, img.crop(box)


def render_font(ttf, size):
    font = ImageFont.truetype(str(ttf), size)
    ascent, descent = font.getmetrics()
    glyphs = []
    # Each glyph is drawn after a space and trimmed to its ink, so overhangs land where they do
    # inside a string.
    pad, space = size, round(font.getlength(" "))
    for ch in CHARSET:
        img = Image.new("1", (4 * size + 2 * pad, 2 * size + 2 * pad), 0)
        ImageDraw.Draw(img).text((pad, pad + size), " " + ch, font=font, fill=1, anchor="ls")
        x0, y0, img = crop(img, pad + space, pad + size)
        glyphs.append((ord(ch), round(font.getlength(ch) * 64), x0, y0, img))
    return ascent, descent, glyphs


def main():
    p = argparse.ArgumentParser(description="Pre-render the board's bitmap fonts from Geist.")
    p.add_argument("--fonts", required=True, type=Path, help="folder with " + ", ".join(FACES.values()))
    fonts = p.parse_args().fonts
    OUT.mkdir(parents=True, exist_ok=True)
    total = 0
    for face, size in SIZES:
        path = OUT / f"{face}{size}.bin"
        write_font(path, *render_font(fonts / FACES[face], size))
        total += path.stat().st_size
    print(f"Wrote {len(SIZES)} fonts to {OUT} ({total // 1024} KB)")


if __name__ == "__main__":
    main()
