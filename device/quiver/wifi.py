"""Joining Wi-Fi and scanning for networks (2.4 GHz only on the ESP32)."""

import os
import time

import network

from .log import log


class WifiError(Exception):
    """reason: "auth" (wrong password), "handshake" (the router didn't finish the handshake in time:
    often a wrong password too, else a weak signal or a network the ESP32 can't join, like WPA3
    only), "no_ap" (network not found) or "dhcp" (joined, no address in time), as status.err reports
    them over Bluetooth. status: what WLAN.status() said, for the log and the outbox."""

    def __init__(self, reason, ssid, status=None):
        self.reason, self.ssid, self.status = reason, ssid, status
        msg = {"auth": "Feil passord for %s", "handshake": "Tilkoblingen til %s ble ikke fullført",
               "no_ap": "Fant ikke nettverket %s", "dhcp": "Fikk ikke adresse fra %s"}[reason] % ssid
        super().__init__(msg if status is None else "%s (status %s)" % (msg, status))


def _sta():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    return wlan


def _status(name):
    return getattr(network, name, None)


def _failed(reason, ssid, st):
    """Radio off, and the raw status in the log: "auth" and "handshake" look alike to the user."""
    off()
    log("wifi: %s on %s (status %s)" % (reason, ssid, st))
    return WifiError(reason, ssid, st)


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
        if st == _status("STAT_WRONG_PASSWORD"):
            raise _failed("auth", ssid, st)
        if st == _status("STAT_HANDSHAKE_TIMEOUT"):
            raise _failed("handshake", ssid, st)
        if st == _status("STAT_NO_AP_FOUND"):
            raise _failed("no_ap", ssid, st)
        if time.ticks_diff(time.ticks_ms(), start) > timeout_s * 1000:
            raise _failed("dhcp" if st == _status("STAT_CONNECTING") else "no_ap", ssid, st)
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


def random_bytes(n):
    """os.urandom with the radio on: only then is the ESP32's RNG truly random."""
    _sta()
    try:
        return os.urandom(n)
    finally:
        off()


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
