"""One start of the board, as main.py runs it after the loader: one wake-up (app.py), then deep
sleep until the next poll or the next button press.

After a power-on or RESET there's a 3-second pause: Ctrl-C then gives the REPL. A wake from deep
sleep skips it, so to reach a sleeping board, press RESET.

To run one wake-up by hand without sleeping afterwards:
  mpremote exec "from quiver import app; print(app.run())"
"""

import sys
import time

import machine

# app.run() returns this instead of seconds to sleep: reset now, to start a new runtime. A wake from
# deep sleep would start the old one again: MicroPython's bootloader (CONFIG_BOOTLOADER_SKIP_VALIDATE_IN_DEEP_SLEEP)
# boots the partition that ran last, not the one set to boot, and the new runtime is rolled back.
RESET = -1


def main():
    # Which button woke the board, read before anything else: with several buttons the wake-up
    # doesn't say, and a quick tap is over by the time the rest has loaded.
    try:
        from quiver.button import snapshot
        early = snapshot()
    except Exception:
        early = None

    if machine.reset_cause() != machine.DEEPSLEEP_RESET:
        print("quiver: starting in 3 s, Ctrl-C for the REPL")
        time.sleep(3)

    try:
        from quiver import app, board
        from quiver.button import Buttons
        seconds, stay_awake = app.run(early)
        button = Buttons(board.load())
    except Exception as e:  # a broken install or board.json: log it, try again later
        print("quiver: could not run")
        sys.print_exception(e)
        try:
            with open("log.txt", "a") as f:
                f.write("Could not run:\n")
                sys.print_exception(e, f)
        except OSError:
            pass
        # A new app on trial gets its next start soon, so a broken one is rolled back in minutes.
        seconds, stay_awake, button = (60 if _trial() else 3600), False, None

    if seconds == RESET:
        machine.reset()
    if stay_awake:
        print("stay_awake is set: would sleep %s now" % ("%d s" % seconds if seconds else "until the button"))
        return
    if button is not None:
        button.arm()
    if seconds:
        machine.deepsleep(seconds * 1000)
    machine.deepsleep()  # until the button (or RESET)


def _trial():
    try:
        from quiver_boot import loader
        return loader.trial()
    except ImportError:
        return False
