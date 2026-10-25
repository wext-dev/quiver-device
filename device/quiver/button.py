"""The buttons. A press wakes the board from deep sleep and tells the API which button it was; the
API keeps the view and page the screen is on (docs/device-protocol.md), so the board holds no state.

  button      press: next view, from its first page. Held 5 s: setup again (new Wi-Fi or home).
              With button_mode "pages" (one button, no page buttons): a short press is the next
              page, held 1 s the next view, held 5 s setup again.
  page_down   next page of the view (after the last, the first)
  page_up     previous page (before the first, the last)

Wire each from its pin to GND (or to 3.3 V with button_active_low false). Deep sleep wakes on an
RTC-capable GPIO only (0-21 on the ESP32-S3), and the internal pull-ups aren't guaranteed while
asleep, so add an external 10 kΩ pull-up to each.

One button wakes the board on ext0. Several wake it on ext1, which doesn't say which pin did it, so
main.py reads the pins first thing (snapshot()), while the finger is still on the button.
"""

import time

import machine

HOLD_MS = 5000  # setup again
VIEW_MS = 1000  # "pages" mode: the next view rather than the next page
ROLES = ("button", "page_down", "page_up")
_SENDS = {"page_down": "page-down", "page_up": "page-up"}


def _wired(pins):
    return [r for r in ROLES if pins.get(r) is not None]


def snapshot(path="board.json"):
    """The buttons down right now, as early in the wake-up as main.py can ask. Reads board.json
    itself (only the pins) to stay quick; anything wrong is left for board.load() to report."""
    import json
    try:
        with open(path) as f:
            given = json.load(f)
    except Exception:
        given = {}
    from .board import DEFAULTS
    pins = dict(DEFAULTS["pins"])
    pins.update(given.get("pins") or {})
    low = given.get("button_active_low", DEFAULTS["button_active_low"])
    return [r for r in _wired(pins) if machine.Pin(pins[r], machine.Pin.IN).value() == (0 if low else 1)]


class Buttons:
    def __init__(self, cfg):
        self.active_low = cfg["button_active_low"]
        self.mode = cfg["button_mode"]
        pull = machine.Pin.PULL_UP if self.active_low else machine.Pin.PULL_DOWN
        self._pins = {r: machine.Pin(cfg["pins"][r], machine.Pin.IN, pull) for r in _wired(cfg["pins"])}
        self._since = time.ticks_ms()  # about when the press started, for how long it's held

    @property
    def present(self):
        return bool(self._pins)

    def _down(self, role):
        pin = self._pins.get(role)
        return pin is not None and pin.value() == (0 if self.active_low else 1)

    def down(self):
        return any(self._down(r) for r in self._pins)

    def woke_us(self):
        """True when this wake-up came from a press rather than the timer."""
        return bool(self._pins) and machine.wake_reason() in (getattr(machine, "EXT0_WAKE", -1), getattr(machine, "EXT1_WAKE", -1))

    def _held(self, role, ms):
        """Waits while `role` is down, up to `ms` after the press. → ms it was held (about)."""
        while self._down(role):
            held = time.ticks_diff(time.ticks_ms(), self._since)
            if held >= ms:
                return held
            time.sleep_ms(20)
        return time.ticks_diff(time.ticks_ms(), self._since)

    def action(self, early=None):
        """What this wake-up is for: None (the timer), "setup", or what to send as X-Button
        ("next", "page-down" or "page-up"). `early` is snapshot() from main.py."""
        role = next((r for r in ROLES if self._down(r)), None) or next((r for r in (early or ()) if r in self._pins), None)
        if role is None:
            if not self.woke_us():
                return None
            if len(self._pins) != 1:
                return None  # a tap too quick to tell which button: just refresh
            role = next(iter(self._pins))
        if role != "button":
            return _SENDS[role]
        held = self._held(role, HOLD_MS)
        if held >= HOLD_MS:
            return "setup"
        if self.mode == "pages":
            return "next" if held >= VIEW_MS else "page-down"
        return "next"

    def wait_release(self, timeout_ms=10000):
        """Before sleeping: a button still held would wake the board straight away."""
        start = time.ticks_ms()
        while self.down() and time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
            time.sleep_ms(20)

    def arm(self):
        """Wake from deep sleep on the next press of any button."""
        if not self._pins:
            return
        import esp32
        pins = list(self._pins.values())
        if len(pins) == 1:
            esp32.wake_on_ext0(pin=pins[0], level=esp32.WAKEUP_ALL_LOW if self.active_low else esp32.WAKEUP_ANY_HIGH)
        else:
            # On the ESP32-S3, WAKEUP_ALL_LOW wakes when any of the pins goes low (ESP-IDF's
            # ESP_EXT1_WAKEUP_ANY_LOW has the same value there); on the original ESP32 it needs all.
            esp32.wake_on_ext1(pins=pins, level=esp32.WAKEUP_ALL_LOW if self.active_low else esp32.WAKEUP_ANY_HIGH)
