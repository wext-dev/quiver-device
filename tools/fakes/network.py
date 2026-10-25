"""CPython stand-in for MicroPython's network module. NETWORKS is what a scan finds and which
passwords work; the HTTP traffic itself goes over the PC's real network."""

STA_IF = 0
STAT_IDLE, STAT_CONNECTING, STAT_GOT_IP = 1000, 1001, 1010
STAT_NO_AP_FOUND, STAT_WRONG_PASSWORD, STAT_HANDSHAKE_TIMEOUT = 201, 202, 204

# ssid → (password, rssi, security code as WLAN.scan() reports it)
NETWORKS = {
    "Hjemme-2G": ("hemmelig", -52, 3),
    "Hjemme-Gjest": ("", -61, 0),
}


class WLAN:
    _state = {"active": False, "ssid": None, "status": STAT_IDLE}

    def __init__(self, _iface=STA_IF):
        pass

    def active(self, on=None):
        if on is None:
            return self._state["active"]
        self._state["active"] = bool(on)
        if not on:
            self._state.update(ssid=None, status=STAT_IDLE)

    def connect(self, ssid, pw):
        net = NETWORKS.get(ssid)
        if net is None:
            self._state["status"] = STAT_NO_AP_FOUND
        elif net[0] != pw:
            self._state["status"] = STAT_WRONG_PASSWORD
        else:
            self._state.update(ssid=ssid, status=STAT_GOT_IP)

    def disconnect(self):
        self._state.update(ssid=None, status=STAT_IDLE)

    def isconnected(self):
        return self._state["status"] == STAT_GOT_IP

    def status(self, param=None):
        if param == "rssi":
            return NETWORKS[self._state["ssid"]][1] if self._state["ssid"] else -100
        return self._state["status"]

    def config(self, key):
        if key == "ssid":
            return self._state["ssid"] or ""
        raise ValueError(key)

    def scan(self):
        return [(ssid.encode(), b"\x00" * 6, 6, rssi, sec, False) for ssid, (_pw, rssi, sec) in NETWORKS.items()]
