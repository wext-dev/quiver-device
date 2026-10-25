"""Run the board's code on a PC: shared by simulate.py and the tests.

    from sim import Sim
    sim = Sim(workdir, api_url="http://localhost:5068")
    seconds = sim.wake()              # one wake-up, as main.py would run it
    sim.png("panel.png")              # what the panel shows now
    SimEPD.qrs[-1]                    # the setup QR last drawn, as the phone scans it

The firmware runs unchanged under CPython with the stand-ins in fakes/. The panel is SimEPD,
which keeps the planes it was sent instead of driving SPI.
"""

import json
import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fakes  # noqa: E402

fakes.install()

import machine  # noqa: E402  (the stand-in)


class SimEPD:
    """Keeps the last planes drawn. The panel class the firmware uses is replaced with this."""

    drawn = []  # every show(), oldest first: (black bytes, red bytes or None, low bytes or None)

    def __init__(self, pins, red=True):
        self.red = red

    def init(self):
        pass

    def init_gray(self):
        pass

    def show(self, black, red=None, wait=True):
        SimEPD.drawn.append((bytes(black), bytes(red) if red is not None else None, None))

    def show_gray(self, high, low, wait=True):
        SimEPD.drawn.append((bytes(high), None, bytes(low)))

    def wait(self):
        pass

    def sleep(self):
        pass


SimEPD.qrs = []  # the text of every QR drawn, oldest first


def _patch():
    """Puts SimEPD in place of the panel and records each setup QR, as the phone would read it."""
    from quiver import epd, qr
    epd.EPD = SimEPD
    if getattr(qr.encode, "__name__", "") != "_record_qr":
        encode = qr.encode

        def _record_qr(text, mask=None):
            SimEPD.qrs.append(text)
            return encode(text, mask)
        qr.encode = _record_qr


_patch()


def fresh(runtime=None):
    """A start as on the board: the app and loader loaded anew from the working directory (a new
    slot may be in place), the loader's boot() run as main.py does. `runtime` makes the loader
    believe it is frozen into that Quiver runtime: {"RUNTIME": "1.0.0", "BOARD": …, "CHIP": …}."""
    import types
    for name in list(sys.modules):
        if name in ("quiver", "quiver_boot") or name.startswith(("quiver.", "quiver_boot.")):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not p.startswith("slots/")]
    # CPython remembers a finder per sys.path entry; "slots/a" may name another test's board.
    for key in [k for k in sys.path_importer_cache if k.startswith("slots/")]:
        del sys.path_importer_cache[key]
    import importlib
    importlib.invalidate_caches()
    if runtime:
        build = types.ModuleType("quiver_boot._build")
        build.__dict__.update(runtime)
        sys.modules["quiver_boot._build"] = build
    from quiver_boot import loader
    loader.boot()
    _patch()


class Sim:
    def __init__(self, workdir, **board):
        self.dir = workdir
        self.runtime = None  # see fresh()
        os.makedirs(workdir, exist_ok=True)
        if board:
            with open(os.path.join(workdir, "board.json"), "w") as f:
                json.dump(board, f)
        machine.BOARD.rtc_path = os.path.join(workdir, ".rtc")

    def pins(self):
        """The buttons wired in board.json: {role: GPIO}."""
        from quiver import board, button
        pins = dict(board.DEFAULTS["pins"])
        pins.update((self.read("board.json") or {}).get("pins") or {})
        return {r: pins[r] for r in button.ROLES if pins.get(r) is not None}

    def wake(self, reset="deepsleep", button=False, held=False, hold_ms=0):
        """One wake-up, as main.py runs it. reset: "power" (power-on) or "deepsleep" (timer or
        button). button: True for the main button, or "page_down" / "page_up". A press is a quick
        tap that main.py catches, unless it's held: until the test lets go (held, by setting the
        pin back to 1) or for hold_ms."""
        wired = self.pins()
        role = "button" if button is True else button or None
        machine.BOARD.reset_cause = machine.PWRON_RESET if reset == "power" else machine.DEEPSLEEP_RESET
        machine.BOARD.wake_reason = machine.TIMER_WAKE if not role else machine.EXT0_WAKE if len(wired) == 1 else machine.EXT1_WAKE
        for n in wired.values():
            machine.BOARD.pins[n] = 1
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            fresh(self.runtime)
            from quiver import app
            from quiver.button import snapshot
            if role:
                machine.BOARD.pins[wired[role]] = 0
            early = snapshot()
            if role and hold_ms:
                threading.Timer(hold_ms / 1000, machine.BOARD.pins.__setitem__, (wired[role], 1)).start()
            elif role and not held:
                machine.BOARD.pins[wired[role]] = 1
            seconds, _stay = app.run(early)
            return seconds
        finally:
            os.chdir(cwd)

    def read(self, name):
        p = os.path.join(self.dir, name)
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    @staticmethod
    def last():
        return SimEPD.drawn[-1] if SimEPD.drawn else None

    @staticmethod
    def png(path, planes=None, width=400, height=300, scale=2):
        """Write planes (default: what the panel shows now) as a PNG, red ink in red, grey in grey."""
        from PIL import Image
        black, red, low = (tuple(planes or SimEPD.drawn[-1]) + (None,))[:3]
        greys = {0: (242, 240, 234), 1: (170, 168, 162), 2: (98, 97, 94), 3: (26, 26, 26)}
        img = Image.new("RGB", (width, height), (242, 240, 234))
        px = img.load()
        stride = (width + 7) // 8
        for y in range(height):
            for x in range(width):
                bit = 0x80 >> (x & 7)
                i = y * stride + (x >> 3)
                if red is not None and red[i] & bit:
                    px[x, y] = (200, 55, 45)
                elif low is not None:
                    px[x, y] = greys[(2 if black[i] & bit else 0) + (1 if low[i] & bit else 0)]
                elif black[i] & bit:
                    px[x, y] = (26, 26, 26)
        img.resize((width * scale, height * scale), Image.NEAREST).save(path)
