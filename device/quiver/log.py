"""log.txt on the board: one line per event, kept under 8 KB (the previous file is log.txt.old).
Read it with `mpremote cat :log.txt`."""

import time

LOG = "log.txt"
LOG_MAX = 8 * 1024


def log(msg):
    """To the file first, then the console: on a PC that holds the USB port open without reading
    it, printing can block, and the line should be on flash by then."""
    line = "[%d] %s" % (time.ticks_ms() // 1000 if hasattr(time, "ticks_ms") else 0, msg)
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
