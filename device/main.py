# Runs at power-on and at every wake from deep sleep. The loader (quiver_boot, frozen into the
# Quiver runtime) picks which copy of the app runs and counts the starts of a new one on trial;
# then the app does one wake-up and the board deep-sleeps (quiver/wake.py). See docs/ota.md.
#
# On the Quiver runtime this file is frozen in and the one on the filesystem is ignored. On stock
# MicroPython, copy it to the board with quiver/ (and quiver_boot/ for updates).

try:
    from quiver_boot import loader
except ImportError:  # stock MicroPython without the loader: the app as installed in /quiver
    loader = None
if loader is not None:
    try:
        loader.boot()
    except Exception as e:  # never let the loader keep the app from starting
        import sys
        sys.print_exception(e)

from quiver import wake

wake.main()
