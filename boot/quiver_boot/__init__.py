"""The Quiver boot loader: picks which copy of the app runs, and installs signed updates.

Frozen into the Quiver runtime (runtime) with the release keys, so the code that decides
what may run is itself only replaced by a signed runtime update, which ESP-IDF rolls back if it
doesn't start. On stock MicroPython it can be copied to the board as files; it then works the same,
only without runtime updates. See docs/ota.md.
"""

BOOT = "1.0.0"

try:
    # Written by runtime/build.sh into the frozen copy: the runtime's version, its board
    # (as releases name it) and chip.
    from ._build import BOARD, CHIP, RUNTIME
except ImportError:
    RUNTIME = None
    BOARD = None
    CHIP = None
