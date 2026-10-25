"""Stand-ins for MicroPython's built-in modules, so the firmware in device/ runs unchanged on a PC.

install() puts them first on sys.path and adds MicroPython's time functions (sleep_ms, ticks_ms,
ticks_diff, ticks_add) and sys.print_exception to CPython's modules.
"""

import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
DEVICE = os.path.join(os.path.dirname(os.path.dirname(HERE)), "device")
BOOT = os.path.join(os.path.dirname(os.path.dirname(HERE)), "boot")


def install():
    for p in (BOOT, DEVICE, HERE):
        if p not in sys.path:
            sys.path.insert(0, p)
    sys.dont_write_bytecode = True  # no __pycache__ in device/, or it would be copied to the board
    if not hasattr(time, "sleep_ms"):
        time.sleep_ms = lambda ms: time.sleep(ms / 1000)
        time.ticks_ms = lambda: int(time.monotonic() * 1000)
        time.ticks_diff = lambda a, b: a - b
        time.ticks_add = lambda a, b: a + b
    if not hasattr(sys, "print_exception"):
        sys.print_exception = lambda e, f=sys.stdout: traceback.print_exception(type(e), e, e.__traceback__, file=f)
