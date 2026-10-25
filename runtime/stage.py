"""Stages what the Quiver runtime freezes in (manifest.py), under build/stage:

  quiver_boot/   the loader, with _build.py naming the runtime's version, board and chip
  quiver/        the app the board comes with, its fonts as quiver/_fontdata.py (frozen code
                 has no files next to it)
  main.py

  python runtime/stage.py --version 1.0.0 --board nano-esp32 --chip ESP32-S3
"""

import argparse
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.dirname(HERE)


def stage(version, board, chip, out):
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "fontdata")
    shutil.copytree(os.path.join(FIRMWARE, "boot", "quiver_boot"), os.path.join(out, "quiver_boot"), ignore=ignore)
    with open(os.path.join(out, "quiver_boot", "_build.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# Written by runtime/stage.py.\nRUNTIME = %r\nBOARD = %r\nCHIP = %r\n" % (version, board, chip))
    shutil.copytree(os.path.join(FIRMWARE, "device", "quiver"), os.path.join(out, "quiver"), ignore=ignore)
    fonts = os.path.join(FIRMWARE, "device", "quiver", "fontdata")
    with open(os.path.join(out, "quiver", "_fontdata.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# The bitmap fonts (fontdata/*.bin), for the frozen app. Written by runtime/stage.py.\nFONTS = {\n")
        for name in sorted(os.listdir(fonts)):
            with open(os.path.join(fonts, name), "rb") as g:
                f.write("    %r: %r,\n" % (name[:-4], g.read()))
        f.write("}\n")
    shutil.copy(os.path.join(FIRMWARE, "device", "main.py"), os.path.join(out, "main.py"))


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--version", required=True)
    p.add_argument("--board", required=True, help="the board's name in releases, e.g. nano-esp32")
    p.add_argument("--chip", required=True, help="ESP Web Tools' chip family, e.g. ESP32-S3")
    p.add_argument("--out", default=os.path.join(HERE, "build", "stage"))
    a = p.parse_args()
    stage(a.version, a.board, a.chip, a.out)
    print(a.out)
