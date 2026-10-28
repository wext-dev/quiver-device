"""log.txt on the board: one line per event, kept under 8 KB (the previous file is log.txt.old).
Read it with `mpremote cat :log.txt`.

A line starts with the time in UTC once the clock has been set (it keeps time through deep sleep,
not a power cut), then the seconds since this wake: `10-28 15:18:10 [12] frame unchanged`."""

import time

LOG = "log.txt"
LOG_MAX = 8 * 1024


def _stamp():
    up = time.ticks_ms() // 1000 if hasattr(time, "ticks_ms") else 0
    t = time.gmtime()
    if t[0] < 2025:  # not set since the last power cut
        return "[%d]" % up
    return "%02d-%02d %02d:%02d:%02d [%d]" % (t[1], t[2], t[3], t[4], t[5], up)


def log(msg):
    """To the file first, then the console: on a PC that holds the USB port open without reading
    it, printing can block, and the line should be on flash by then."""
    line = "%s %s" % (_stamp(), msg)
    try:
        import os
        if os.stat(LOG)[6] > LOG_MAX:
            os.rename(LOG, LOG + ".old")
    except OSError:
        pass
    try:
        with open(LOG, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line)


def exception(prefix, e):
    log("%s: %s: %s" % (prefix, type(e).__name__, e))
