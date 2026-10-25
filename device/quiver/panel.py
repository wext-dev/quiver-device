"""The e-paper, set up the first time something is drawn. A refresh started with wait=False carries
on in the background (a red refresh takes ~15 s); whatever uses the panel next waits for it."""

from .board import PANELS


class Panel:
    def __init__(self, cfg):
        spec = PANELS[cfg["panel"]]
        self.width, self.height, self.red = spec["w"], spec["h"], spec["colors"] == "bwr"
        self._pins = cfg["pins"]
        self._epd = None
        self._refreshing = False
        self.failed = False  # the panel didn't answer earlier in this wake-up

    def new_canvas(self):
        from .canvas import Canvas
        return Canvas(self.width, self.height, red=self.red)

    def _driver(self):
        if self._epd is None:
            from .epd import EPD
            self._epd = EPD(self._pins, red=self.red)
        return self._epd

    def show(self, black, red=None, wait=True, low=None):
        """Draw planes: a frame's (frame.black, frame.red, or frame.low for grey) or a canvas's buffers."""
        if self.failed:
            raise RuntimeError("the panel didn't answer earlier in this wake-up")
        try:
            self.finish()
            epd = self._driver()
            if low is not None and not self.red:
                epd.init_gray()
                epd.show_gray(black, low, wait=wait)
            else:
                epd.init()
                epd.show(black, red if self.red else None, wait=wait)
            self._refreshing = not wait
        except Exception:
            self.failed = True
            raise

    def show_frame(self, f, wait=True):
        self.show(f.black, f.red, wait=wait, low=f.low)

    @property
    def last_refresh_ms(self):
        return getattr(self._epd, "last_refresh_ms", None)

    def show_canvas(self, canvas, wait=True):
        self.show(canvas.black_buf, canvas.red_buf, wait=wait)

    def finish(self):
        if self._refreshing:
            self._refreshing = False
            self._epd.wait()

    def sleep(self):
        """Put the panel to sleep (the picture stays), if it was used."""
        if self._epd is not None and not self.failed:
            self.finish()
            self._epd.sleep()
