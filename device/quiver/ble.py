"""The setup service over Bluetooth LE (docs/device-protocol.md, section 1).

Service 5f1e0001-…, with info (read), control (write), networks (read, notify), wifi (write) and
status (read, notify). Every message is framed (framing.py).

- Writes arrive chunk by chunk in the IRQ. Each chunk is appended to that characteristic's
  reassembler, and whole messages go on a queue that the setup loop works through, outside the IRQ.
- Notifications go out as consecutive chunks of MTU − 3 bytes.
- Readable values are stored as one final chunk of up to 512 bytes. The phone's stack fetches
  values longer than the MTU with long reads, and the app's reassembler sees the final flag on the
  first read. (MicroPython on the ESP32 doesn't report individual reads, so the value can't be
  advanced chunk by chunk.)
"""

import json

import bluetooth
from micropython import const

from .framing import Reassembler, chunks

_IRQ_CENTRAL_CONNECT = const(1)
_IRQ_CENTRAL_DISCONNECT = const(2)
_IRQ_GATTS_WRITE = const(3)
_IRQ_MTU_EXCHANGED = const(21)

_READ = const(0x0002)
_WRITE = const(0x0008)
_NOTIFY = const(0x0010)
_MAX_VALUE = const(512)


def _uuid(n):
    return bluetooth.UUID("5f1e000%d-8a3c-4b7e-9d2a-7c1b5e9a0f10" % n)


SERVICE = _uuid(1)
_CHARS = (
    ("info", _uuid(2), _READ),
    ("control", _uuid(3), _WRITE),
    ("networks", _uuid(4), _READ | _NOTIFY),
    ("wifi", _uuid(5), _WRITE),
    ("status", _uuid(6), _READ | _NOTIFY),
)


def _adv(kind, data):
    return bytes((len(data) + 1, kind)) + data


def advertising(name):
    """Advertising data carries the service UUID (the app filters its scan on it); the name goes in
    the scan response, since both don't fit in 31 bytes."""
    adv = _adv(0x01, b"\x06") + _adv(0x07, bytes(SERVICE))  # flags: general discoverable, no BR/EDR
    resp = _adv(0x09, name.encode())
    return adv, resp


class Provisioner:
    """Advertises as `name` and serves the setup service. The setup loop calls next_event() and
    answers with set_networks() / set_status()."""

    def __init__(self, name, info):
        self._ble = bluetooth.BLE()
        self._ble.active(True)
        try:
            self._ble.config(gap_name=name, mtu=256)
        except (OSError, ValueError):
            pass
        ((h_info, h_control, h_networks, h_wifi, h_status),) = self._ble.gatts_register_services(
            ((SERVICE, tuple((u, f) for _, u, f in _CHARS)),))
        self._h = {"info": h_info, "control": h_control, "networks": h_networks, "wifi": h_wifi, "status": h_status}
        for h in self._h.values():
            self._ble.gatts_set_buffer(h, _MAX_VALUE)
        self._writes = {self._h["control"]: Reassembler(), self._h["wifi"]: Reassembler()}
        self._conns = {}  # conn handle → MTU
        self._queue = []
        self._name = name
        self._stopped = False
        self._store("info", info)
        self._ble.irq(self._irq)
        self._advertise()

    # ---- IRQ (keep it short) -------------------------------------------------------------------

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            self._conns[data[0]] = 23
        elif event == _IRQ_CENTRAL_DISCONNECT:
            self._conns.pop(data[0], None)
            for r in self._writes.values():
                r.__init__()
            if not self._stopped:  # the phone left mid-setup: let it (or another) find us again
                self._advertise()
        elif event == _IRQ_MTU_EXCHANGED:
            self._conns[data[0]] = data[1]
        elif event == _IRQ_GATTS_WRITE:
            handle = data[1]
            r = self._writes.get(handle)
            if r is None:
                return
            done = r.push(self._ble.gatts_read(handle))
            if done is not None:
                self._queue.append((handle, done[0], done[1]))

    def _advertise(self):
        adv, resp = advertising(self._name)
        self._ble.gap_advertise(250_000, adv_data=adv, resp_data=resp)

    # ---- for the setup loop ---------------------------------------------------------------------

    @property
    def connected(self):
        return bool(self._conns)

    def next_event(self):
        """("scan",) · ("reboot",) · ("forget",) · ("wifi", raw bytes, encrypted) · None"""
        if not self._queue:
            return None
        handle, msg, encrypted = self._queue.pop(0)
        if handle == self._h["wifi"]:
            return ("wifi", msg, encrypted)
        try:
            cmd = json.loads(msg).get("cmd")
        except (ValueError, AttributeError):
            return None
        return (cmd,) if cmd in ("scan", "reboot", "forget") else None

    def set_networks(self, networks):
        # Keep the stored value within one attribute: drop the weakest networks until it fits.
        while len(json.dumps(networks)) > _MAX_VALUE - 1 and networks:
            networks = networks[:-1]
        self._publish("networks", networks)

    def set_status(self, status):
        self._publish("status", status)

    def stop(self):
        self._stopped = True
        self._ble.gap_advertise(None)
        for conn in list(self._conns):
            try:
                self._ble.gap_disconnect(conn)
            except OSError:
                pass
        self._ble.active(False)

    def _store(self, name, value):
        self._ble.gatts_write(self._h[name], chunks(json.dumps(value).encode(), _MAX_VALUE)[0])

    def _publish(self, name, value):
        data = json.dumps(value).encode()
        self._ble.gatts_write(self._h[name], chunks(data, _MAX_VALUE)[0])
        for conn, mtu in list(self._conns.items()):
            for c in chunks(data, mtu - 3):
                try:
                    self._ble.gatts_notify(conn, self._h[name], c)
                except OSError:
                    break
