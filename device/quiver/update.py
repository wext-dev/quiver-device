"""Installing the update the API offered (X-Update on the frame answer), once the frame is drawn.

The app only fetches: the offer, then the payload as a stream. The loader (quiver_boot.ota)
checks the signed manifest before anything is downloaded, hashes the payload as it is written and
stages it for the next start, on trial (docs/ota.md). Without the loader (stock MicroPython, or
"ota": "off" in board.json) there are no updates.
"""

import binascii

from . import FIRMWARE, outbox, wifi
from .api import Api, ApiError
from .http import HttpError
from .log import exception, log

# Not on a nearly flat battery: a runtime update writes 2 MB of flash.
MIN_BATTERY_MV = 3550


def run(cfg, secrets, ca, battery_mv):
    """The kind installed ("app" or "runtime") when the board should start it now, else False."""
    try:
        from quiver_boot import RUNTIME, ota
    except ImportError:
        return False
    if ota.mode(cfg) == "off":
        return False
    if battery_mv is not None and battery_mv < MIN_BATTERY_MV:
        log("update waits: battery %d mV" % battery_mv)
        return False
    wifi.connect(secrets.wifi["ssid"], secrets.wifi["pw"], cfg["wifi_timeout_s"])
    try:
        api = Api(cfg["api_url"], secrets.token, ca)
        offer = api.offer()
        if not offer:
            return False
        kind, version = offer.get("kind"), offer.get("version")
        try:
            m = ota.check(binascii.a2b_base64(offer["manifest"]), binascii.a2b_base64(offer["signature"]),
                          offer["keyId"], kind, FIRMWARE if kind == "app" else RUNTIME, cfg)
            log("installing %s %s (%d bytes)" % (kind, m["version"], m["size"]))
            install = ota.install_app if kind == "app" else ota.install_runtime
            api.download(offer["url"], lambda stream, length: install(m, stream))
        except (ota.OtaError, HttpError, ApiError, OSError, KeyError, ValueError) as e:
            exception("update %s %s" % (kind, version), e)
            outbox.add("update", "%s %s: %s" % (kind, version, e), {"kind": kind, "to": version, "result": "failed"})
            return False
        log("installed %s %s; starting it" % (kind, version))
        return kind
    finally:
        wifi.off()
