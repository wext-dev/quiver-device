"""CPython stand-in for MicroPython's machine module, enough for the firmware to run on a PC.
Tests and simulate.py set the knobs (reset cause, wake reason, button level) through BOARD."""

import os


class _Board:
    def __init__(self):
        self.reset_cause = 1  # PWRON_RESET
        self.wake_reason = 0
        self.unique_id = bytes.fromhex(os.environ.get("QUIVER_HW", "a1b2c3d4e5f6"))
        self.pins = {}  # pin number → level read by Pin.value()
        self.rtc_path = None  # a file standing in for RTC memory, or None for memory only
        self.rtc = b""
        self.slept = None
        self.watchdog = None  # the timeout of a started watchdog, in ms


BOARD = _Board()

PWRON_RESET, HARD_RESET, WDT_RESET, DEEPSLEEP_RESET, SOFT_RESET = 1, 2, 3, 4, 5
EXT0_WAKE, EXT1_WAKE, TIMER_WAKE = 2, 3, 4


def reset_cause():
    return BOARD.reset_cause


def wake_reason():
    return BOARD.wake_reason


def unique_id():
    return BOARD.unique_id


def freq(_hz=None):
    return 240000000


class Reset(Exception):
    pass


def reset():
    raise Reset()


class DeepSleep(Exception):
    def __init__(self, ms=None):
        super().__init__(ms)
        self.ms = ms


def deepsleep(ms=None):
    BOARD.slept = ms
    raise DeepSleep(ms)


class WDT:
    def __init__(self, id=0, timeout=5000):
        BOARD.watchdog = timeout

    def feed(self):
        pass


class Pin:
    IN, OUT = 1, 2
    PULL_UP, PULL_DOWN = 1, 2

    def __init__(self, n, mode=IN, pull=None, value=None):
        self.n = n
        if value is not None:
            BOARD.pins[n] = value
        elif n not in BOARD.pins:
            BOARD.pins[n] = 1 if pull == Pin.PULL_UP else 0

    def value(self, v=None):
        if v is None:
            return BOARD.pins.get(self.n, 0)
        BOARD.pins[self.n] = v

    __call__ = value


class SPI:
    def __init__(self, *args, **kwargs):
        self.written = 0

    def write(self, data):
        self.written += len(data)


class ADC:
    ATTN_11DB = 3

    def __init__(self, pin):
        pass

    def atten(self, _a):
        pass

    def read_uv(self):
        return 1_960_000


class RTC:
    def memory(self, data=None):
        if data is None:
            if BOARD.rtc_path and os.path.exists(BOARD.rtc_path):
                with open(BOARD.rtc_path, "rb") as f:
                    return f.read()
            return BOARD.rtc
        BOARD.rtc = bytes(data)
        if BOARD.rtc_path:
            with open(BOARD.rtc_path, "wb") as f:
                f.write(BOARD.rtc)
