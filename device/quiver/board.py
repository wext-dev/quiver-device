"""board.json: how this board is built (panel, wiring, button) and which API it talks to. No
secrets here; Wi-Fi and the device token are in secrets.json (store.py), written during setup.

Anything left out gets the default below. An unknown key is an error, so a typo doesn't go unnoticed.
"""

import binascii
import json

import machine

# Panels this firmware has a driver for. The API renders for the size and colours the device
# reports, so a new panel needs a driver here and nothing on the server.
PANELS = {
    "ws-4.2-bwr": {"w": 400, "h": 300, "colors": "bwr"},  # Waveshare 4.2" B (UC8176), black/white/red
    # Waveshare 4.2" (UC8176), black/white, and four grey levels (epd.py).
    "ws-4.2-bw": {"w": 400, "h": 300, "colors": "bw", "gray": True},
}

DEFAULTS = {
    "api_url": "https://quiver.wext.as/api",
    "panel": "ws-4.2-bwr",
    # GPIO numbers, not D labels. These are the Arduino Nano ESP32's D13 (SCK), D11 (MOSI), D10,
    # D9, D8 and D7 for the panel, and D2 for the button. page_down and page_up are optional
    # buttons for paging through a view's tables (D3 and D4 are GPIO 6 and 7).
    "pins": {"sck": 48, "mosi": 38, "cs": 21, "dc": 18, "rst": 17, "busy": 10, "power": None,
             "button": 5, "page_down": None, "page_up": None, "battery": None},
    # The buttons pull their pins to GND when pressed (with a pull-up resistor). False: to 3.3 V.
    "button_active_low": True,
    # "views": a press shows the next view. "pages", for a screen with one button and no page
    # buttons: a short press shows the next page, held 1 s the next view (button.py).
    "button_mode": "views",
    # Battery voltage through a divider on pins.battery: mV at the pin times this.
    "battery_divider": 2.0,
    # A DER or PEM file with the CA that signs the API's certificate, for a self-hosted API. Without
    # it, TLS is checked against Google's roots (ca.py), which sign Quiver's API. TLS is always
    # checked; plain http:// is allowed only for an API on the local network (development).
    "ca_file": None,
    # The board's name in releases ("nano-esp32"). The Quiver runtime knows its own; set this on
    # stock MicroPython or a board of your own.
    "model": None,
    # Over-the-air updates (docs/ota.md): "official" takes Quiver's signed releases; "off" never
    # updates (for a board running code you changed yourself).
    "ota": "official",
    # More signing keys to trust, {"id": "<base64 Ed25519 public key>"}, for a self-hosted API. The
    # development key (keys/dev.key) is honoured only while api_url is a local address.
    "ota_keys": None,
    "wifi_timeout_s": 20,
    # Setup mode advertises over Bluetooth this long without progress, then stops to save power.
    "setup_timeout_s": 600,
    # Keep the picture through short outages; draw an error screen after this many failures in a row.
    "errors_after": 3,
    "retry_minutes": 5,  # after a failure; doubles each time, up to max_retry_minutes
    "max_retry_minutes": 120,
    # A 401 (the API doesn't know the token) is retried like any failure. Only after this many hours
    # of them is the token dropped and a new code shown. A removed screen is told so (410) instead.
    "token_refused_hours": 24,
    "stay_awake": False,  # run once and drop to the REPL instead of deep sleep
    # On a panel that can show grey (ws-4.2-bw), ask for grey frames: smoother text. False: black
    # and white only.
    "gray": True,
}


class BoardError(Exception):
    pass


def hardware_id():
    """The chip's unique id (its MAC on an ESP32) as lowercase hex."""
    return binascii.hexlify(machine.unique_id()).decode()


def advertised_name(hw):
    """"a1b2c3d4e5f6" → "Quiver-E5F6", as the app expects."""
    return "Quiver-" + hw[-4:].upper()


def load(path="board.json"):
    try:
        with open(path) as f:
            given = json.load(f)
    except OSError:
        given = {}
    except ValueError as e:
        raise BoardError("%s is not valid JSON: %s" % (path, e))
    cfg = {}
    for key, default in DEFAULTS.items():
        value = given.get(key, default)
        if isinstance(default, dict):
            merged = dict(default)
            merged.update(value or {})
            value = merged
        cfg[key] = value
    for key in given:
        if key not in DEFAULTS:
            raise BoardError("Unknown key %r in %s" % (key, path))
    if cfg["button_mode"] not in ("views", "pages"):
        raise BoardError("%s: button_mode must be \"views\" or \"pages\"" % path)
    if cfg["panel"] not in PANELS:
        raise BoardError("%s: panel must be one of %s" % (path, ", ".join(sorted(PANELS))))
    if cfg["ota"] not in ("official", "off"):
        raise BoardError("%s: ota must be \"official\" or \"off\"" % path)
    if cfg["ota_keys"] is not None and not isinstance(cfg["ota_keys"], dict):
        raise BoardError("%s: ota_keys must be {\"id\": \"<base64 public key>\"}" % path)
    cfg["api_url"] = cfg["api_url"].rstrip("/")
    if not cfg["api_url"].startswith("https://") and not (cfg["api_url"].startswith("http://") and local(cfg["api_url"])):
        raise BoardError("%s: api_url must be https:// (http:// only for an API on the local network)" % path)
    return cfg


def local(url):
    """An API on this network: localhost, a .local name, a private IPv4 address or a bare host name."""
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].lower()
    if host == "localhost" or host.endswith(".local") or "." not in host:
        return True
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    return a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)


def model(cfg):
    """The board's name in releases: the runtime's own, else board.json's, else the default."""
    try:
        from quiver_boot import BOARD
    except ImportError:
        BOARD = None
    from . import MODEL
    return BOARD or cfg["model"] or MODEL


def panel_report(cfg):
    """The panel as the API wants it: {"id", "w", "h", "colors"}."""
    p = dict(PANELS[cfg["panel"]])
    p.pop("gray", None)  # reported as a feature instead
    p["id"] = cfg["panel"]
    return p


def features(cfg):
    """Hardware features to report: "button" when one is wired, "paging" when the screen can page
    (page buttons, or the one button in "pages" mode), "gray4" when it draws four grey levels."""
    pins = cfg["pins"]
    button = pins.get("button") is not None
    out = ["button"] if button else []
    if pins.get("page_down") is not None or pins.get("page_up") is not None or (button and cfg["button_mode"] == "pages"):
        out.append("paging")
    if cfg["gray"] and PANELS[cfg["panel"]].get("gray"):
        out.append("gray4")
    return out


def battery_mv(cfg):
    """Battery voltage in mV, or None without a battery divider wired."""
    pin = cfg["pins"].get("battery")
    if pin is None:
        return None
    try:
        adc = machine.ADC(machine.Pin(pin))
        adc.atten(machine.ADC.ATTN_11DB)
        mv = sum(adc.read_uv() for _ in range(8)) // 8 // 1000
        return int(mv * cfg["battery_divider"])
    except Exception:
        return None
