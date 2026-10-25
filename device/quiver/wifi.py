"""Joining Wi-Fi and scanning for networks (2.4 GHz only on the ESP32)."""

import time

import network


class WifiError(Exception):
    """reason: "auth" (wrong password), "no_ap" (network not found) or "dhcp" (joined, no address
    in time), as status.err reports them over Bluetooth."""

    def __init__(self, reason, ssid):
        self.reason, self.ssid = reason, ssid
        super().__init__({"auth": "Feil passord for %s", "no_ap": "Fant ikke nettverket %s",
                          "dhcp": "Fikk ikke adresse fra %s"}[reason] % ssid)


def _sta():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    return wlan


def _status(name):
    return getattr(network, name, None)


def connect(ssid, pw, timeout_s=20):
    wlan = _sta()
    if wlan.isconnected():
        try:
            if wlan.config("ssid") == ssid:
                return wlan
        except (OSError, ValueError):
            pass
        wlan.disconnect()
    wlan.connect(ssid, pw)
    start = time.ticks_ms()
    while not wlan.isconnected():
        st = wlan.status()
        if st == _status("STAT_WRONG_PASSWORD") or st == _status("STAT_HANDSHAKE_TIMEOUT"):
            off()
            raise WifiError("auth", ssid)
        if st == _status("STAT_NO_AP_FOUND"):
            off()
            raise WifiError("no_ap", ssid)
        if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
            off()
            raise WifiError("dhcp" if st == _status("STAT_CONNECTING") else "no_ap", ssid)
        time.sleep_ms(100)
    return wlan


def rssi(wlan):
    try:
        return wlan.status("rssi")
    except (OSError, ValueError, TypeError):
        return None


def off():
    """The radio is most of the power budget: off as soon as it's not needed."""
    wlan = network.WLAN(network.STA_IF)
    try:
        wlan.disconnect()
    except OSError:
        pass
    wlan.active(False)


# network.WLAN.scan() security codes → the protocol's "sec".
_SEC = {0: "open", 6: "wpa3", 7: "wpa3"}


def scan(limit=12):
    """[{"ssid", "rssi", "sec"}], strongest first, one entry per name, hidden networks left out."""
    seen = {}
    for ssid, _bssid, _ch, level, sec, hidden in _sta().scan():
        name = ssid.decode("utf-8", "replace") if isinstance(ssid, bytes) else ssid
        if hidden or not name:
            continue
        if name not in seen or seen[name]["rssi"] < level:
            seen[name] = {"ssid": name, "rssi": level, "sec": _SEC.get(sec, "wpa2")}
    return sorted(seen.values(), key=lambda n: -n["rssi"])[:limit]
