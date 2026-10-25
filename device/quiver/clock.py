"""The board's clock. After a power cut it starts in the year 2000, and TLS checks certificate
dates against it, so it is set from NTP before the first HTTPS request. The RTC keeps it through
deep sleep, so this happens once per power-on."""

import time

from .log import exception, log


def sync():
    if time.gmtime()[0] >= 2025:
        return
    try:
        import ntptime
        ntptime.settime()
        log("clock set from NTP")
    except Exception as e:  # TLS will then say what's wrong
        exception("NTP", e)
