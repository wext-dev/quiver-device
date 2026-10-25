"""Driver for the Waveshare 4.2" e-paper (400x300, UC8176/IL0398 controller), in black/white or
black/white/red. Ported unchanged in its command sequences from the earlier shoestore firmware.

The black/white/red sequence is Waveshare's epd4in2bc. The black/white one loads the panel's
built-in full-refresh waveform instead of uploading lookup tables. If a black/white panel shows
nothing or an inverted image, that's the part to look at.

A black/white panel can also show four grey levels (init_gray and show_gray), which lets text
have smooth edges. That sequence and its lookup tables are Waveshare's epd4in2 (Init_4Gray,
Gray_SetLut, display_4Gray). The two RAM planes then hold one bit of the level each:

    level      0x10  0x13
    white       1     1
    light grey  1     0
    dark grey   0     1
    black       0     0

so a grey frame's high plane (dark or black) goes to 0x10 and its low plane to 0x13, each inverted
like an ink plane.

Frames (and the canvas used for the board's own screens) keep ink as set bits; the controller
wants ink as 0, so rows are inverted on the way out. Wiring is set in board.json ("pins").
"""

import time

from machine import SPI, Pin

WIDTH, HEIGHT = 400, 300
ROW = WIDTH // 8
BUSY_TIMEOUT_MS = 60000  # a red refresh takes about 15 s
MIN_RED_REFRESH_MS = 5000  # a tri-colour panel that "finishes" sooner didn't refresh at all
MIN_GRAY_REFRESH_MS = 500  # the grey waveform runs about 1 s; done sooner means it didn't run
# After the refresh command the controller takes a moment before BUSY goes low. Reading BUSY too
# early looks like "done", and the power-off that follows cancels the refresh: the old picture
# stays. Waveshare's drivers wait 200 ms.
REFRESH_SETTLE_MS = 200


class EPDError(Exception):
    pass


# Waveshare's 4-grey waveforms for this controller: VCOM, then white→white, black→white,
# white→black and black→black (42 bytes each).
_LUT_VCOM = bytes((
    0x00, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x60, 0x14, 0x14, 0x00, 0x00, 0x01, 0x00, 0x14, 0x00, 0x00, 0x00, 0x01,
    0x00, 0x13, 0x0A, 0x01, 0x00, 0x01) + (0,) * 18)
_LUT_WW = bytes((
    0x40, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x90, 0x14, 0x14, 0x00, 0x00, 0x01, 0x10, 0x14, 0x0A, 0x00, 0x00, 0x01,
    0xA0, 0x13, 0x01, 0x00, 0x00, 0x01) + (0,) * 18)
_LUT_BW = bytes((
    0x40, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x90, 0x14, 0x14, 0x00, 0x00, 0x01, 0x00, 0x14, 0x0A, 0x00, 0x00, 0x01,
    0x99, 0x0C, 0x01, 0x03, 0x04, 0x01) + (0,) * 18)
_LUT_WB = bytes((
    0x40, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x90, 0x14, 0x14, 0x00, 0x00, 0x01, 0x00, 0x14, 0x0A, 0x00, 0x00, 0x01,
    0x99, 0x0B, 0x04, 0x04, 0x01, 0x01) + (0,) * 18)
_LUT_BB = bytes((
    0x80, 0x0A, 0x00, 0x00, 0x00, 0x01, 0x90, 0x14, 0x14, 0x00, 0x00, 0x01, 0x20, 0x14, 0x0A, 0x00, 0x00, 0x01,
    0x50, 0x13, 0x01, 0x00, 0x00, 0x01) + (0,) * 18)


class EPD:
    def __init__(self, pins, red=True):
        self.red = red
        self._refresh_started = 0
        self._gray = False
        self.last_refresh_ms = None  # how long the last refresh took, for the log
        self.cs = Pin(pins["cs"], Pin.OUT, value=1)
        self.dc = Pin(pins["dc"], Pin.OUT, value=0)
        self.rst = Pin(pins["rst"], Pin.OUT, value=1)
        self.busy = Pin(pins["busy"], Pin.IN)
        self.power = Pin(pins["power"], Pin.OUT, value=0) if pins.get("power") is not None else None
        self.spi = SPI(pins.get("spi_id", 1), baudrate=4000000, polarity=0, phase=0,
                       sck=Pin(pins["sck"]), mosi=Pin(pins["mosi"]))

    def _command(self, cmd, data=None):
        self.dc(0)
        self.cs(0)
        self.spi.write(bytes((cmd,)))
        self.cs(1)
        if data is not None:
            self._data(data)

    def _data(self, data):
        self.dc(1)
        self.cs(0)
        self.spi.write(data)
        self.cs(1)

    def _plane(self, cmd, buf):
        """Send a 1-bit plane (bytes, bytearray or memoryview of HEIGHT rows of ROW bytes, as in
        an RHF1 frame), ink inverted to the controller's 0 = ink. None sends all white."""
        self.dc(0)
        self.cs(0)
        self.spi.write(bytes((cmd,)))
        self.dc(1)
        row = bytearray(ROW)
        for y in range(HEIGHT):
            off = y * ROW
            for i in range(ROW):
                row[i] = buf[off + i] ^ 0xFF if buf is not None else 0xFF
            self.spi.write(row)
        self.cs(1)

    def _wait(self):
        """BUSY is low while the controller works."""
        start = time.ticks_ms()
        while self.busy() == 0:
            if time.ticks_diff(time.ticks_ms(), start) > BUSY_TIMEOUT_MS:
                raise EPDError("e-paper stayed busy - check the BUSY wire and the panel setting")
            time.sleep_ms(50)

    def _refresh(self, gray=False):
        self._command(0x12)  # refresh
        self._refresh_started = time.ticks_ms()
        self._gray = gray
        time.sleep_ms(REFRESH_SETTLE_MS)

    def wait(self):
        """Wait for a refresh started with show(wait=False) (or by show())."""
        self._wait()
        took = self.last_refresh_ms = time.ticks_diff(time.ticks_ms(), self._refresh_started)
        if self.red and took < MIN_RED_REFRESH_MS:
            raise EPDError("the panel said it was done after %d ms, but a refresh takes about 15 s: "
                           "it isn't drawing. Check its power, wiring and controller type." % took)
        if self._gray and took < MIN_GRAY_REFRESH_MS:
            raise EPDError("the grey refresh was done after %d ms, so it didn't run: the old picture "
                           "stays. Set \"gray\": false in board.json to draw in black and white." % took)

    def _reset(self):
        self.rst(1)
        time.sleep_ms(200)
        self.rst(0)
        time.sleep_ms(5)
        self.rst(1)
        time.sleep_ms(200)

    def init(self):
        if self.power is not None:
            self.power(1)
            time.sleep_ms(50)
        self._reset()
        self._command(0x06, b"\x17\x17\x17")  # booster soft start
        self._command(0x04)  # power on
        self._wait()
        if self.red:
            self._command(0x00, b"\x0f")  # panel setting: 400x300, black/white/red, waveform from OTP
        else:
            self._command(0x00, b"\x1f")  # panel setting: 400x300, black/white, waveform from OTP
            self._command(0x50, b"\x97")  # border and data polarity, as Waveshare's epd4in2

    def init_gray(self):
        """Power up for show_gray: the waveform comes from the tables above, not the panel's OTP."""
        if self.red:
            raise EPDError("grey levels need a black/white panel")
        if self.power is not None:
            self.power(1)
            time.sleep_ms(50)
        self._reset()
        self._command(0x01, b"\x03\x00\x2b\x2b\x13")  # power setting: VGH/VGL ±20 V, VDH/VDL ±15 V
        self._command(0x06, b"\x17\x17\x17")  # booster soft start
        self._command(0x04)  # power on
        self._wait()
        self._command(0x00, b"\x3f")  # panel setting: 400x300, black/white, waveform from registers
        self._command(0x30, b"\x3c")  # PLL: 100 Hz
        self._command(0x61, b"\x01\x90\x01\x2c")  # resolution 400x300
        self._command(0x82, b"\x12")  # VCOM DC
        self._command(0x50, b"\x97")  # border and data polarity

    def show_gray(self, high, low, wait=True):
        """Draw a grey frame (its high and low planes, see above) after init_gray, and refresh."""
        self._command(0x92)  # partial mode off
        self._plane(0x10, high)
        self._plane(0x13, low)
        for cmd, lut in ((0x20, _LUT_VCOM), (0x21, _LUT_WW), (0x22, _LUT_BW), (0x23, _LUT_WB), (0x24, _LUT_BB), (0x25, _LUT_WW)):
            self._command(cmd, lut)
        self._refresh(gray=True)
        if wait:
            self.wait()

    def show(self, black, red=None, wait=True):
        """Draw the planes (400x300 bits each, set bit = ink) and
        refresh. Blocks until the panel is done, unless wait=False: then
        the refresh carries on by itself and wait() must be called before
        the panel is used again."""
        if self.red:
            self._plane(0x10, black)
            self._plane(0x13, red)
        else:
            self._plane(0x10, None)  # "old" image: all white
            self._plane(0x13, black)
        self._refresh()
        if wait:
            self.wait()

    def sleep(self):
        """Deep sleep: the picture stays, the panel draws next to nothing.
        Only a reset (init) wakes it."""
        self._command(0x02)  # power off
        self._wait()
        self._command(0x07, b"\xa5")
        if self.power is not None:
            time.sleep_ms(10)
            self.power(0)
