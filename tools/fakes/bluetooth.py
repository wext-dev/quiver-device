"""CPython stand-in for MicroPython's bluetooth module, with a scriptable phone (Central) that
talks to the firmware's GATT service the way the app does (apps/mobile/src/pairing/ble.ts)."""

import threading

FLAG_READ, FLAG_WRITE, FLAG_NOTIFY = 0x0002, 0x0008, 0x0010


class UUID:
    def __init__(self, s):
        self.s = s.lower()

    def __bytes__(self):
        return bytes(reversed(bytes.fromhex(self.s.replace("-", ""))))

    def __eq__(self, other):
        return isinstance(other, UUID) and other.s == self.s

    def __hash__(self):
        return hash(self.s)


class BLE:
    instance = None

    def __init__(self):
        BLE.instance = self
        self.values = {}
        self.by_uuid = {}
        self.notified = []  # (handle, chunk)
        self.advertising = None
        self.on = False
        self._irq = None
        self.lock = threading.Lock()

    def active(self, on=None):
        if on is None:
            return self.on
        self.on = on

    def config(self, **kw):
        pass

    def gatts_register_services(self, services):
        out, h = [], 10
        for _uuid, chars in services:
            handles = []
            for u, _flags in chars:
                h += 1
                self.values[h] = b""
                self.by_uuid[u.s] = h
                handles.append(h)
            out.append(tuple(handles))
        return tuple(out)

    def gatts_set_buffer(self, handle, size, append=False):
        pass

    def gatts_write(self, handle, data):
        self.values[handle] = bytes(data)

    def gatts_read(self, handle):
        return self.values[handle]

    def gatts_notify(self, conn, handle, data):
        with self.lock:
            self.notified.append((handle, bytes(data)))

    def gap_advertise(self, interval, adv_data=None, resp_data=None):
        self.advertising = None if interval is None else (adv_data, resp_data)

    def gap_disconnect(self, conn):
        self._irq(2, (conn, 0, b""))

    def irq(self, handler):
        self._irq = handler


class Central:
    """The phone: connects, writes framed messages, reads and collects notifications."""

    def __init__(self, mtu=185):
        self.ble = BLE.instance
        self.mtu = mtu
        self.ble._irq(1, (1, 0, b""))
        self.ble._irq(21, (1, mtu))

    def handle(self, n):
        return self.ble.by_uuid["5f1e000%d-8a3c-4b7e-9d2a-7c1b5e9a0f10" % n]

    def write(self, n, chunks):
        h = self.handle(n)
        for c in chunks:
            self.ble.values[h] = c
            self.ble._irq(3, (1, h))

    def read(self, n):
        """A long read of the stored value: one framed chunk."""
        return self.ble.values[self.handle(n)]

    def notifications(self, n):
        """The messages notified on characteristic n so far, reassembled."""
        h = self.handle(n)
        msgs, parts = [], []
        with self.ble.lock:
            chunks = [c for hh, c in self.ble.notified if hh == h]
        for c in chunks:
            parts.append(c[1:])
            if c[0] & 1:
                msgs.append(b"".join(parts))
                parts = []
        return msgs
