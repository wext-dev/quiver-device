"""Bluetooth message framing (docs/device-protocol.md, "Framing"): every JSON message is split
into chunks of `1 byte flags + data`. Mirrors apps/mobile/src/pairing/protocol.ts."""

FINAL = 0x01
ENCRYPTED = 0x02


def chunks(data, max_chunk, encrypted=False):
    """Split a message into chunks of at most max_chunk bytes, flags included."""
    size = max(1, max_chunk - 1)
    out = []
    i = 0
    while i < len(data) or not out:
        part = data[i:i + size]
        last = i + size >= len(data)
        out.append(bytes(((FINAL if last else 0) | (ENCRYPTED if encrypted else 0),)) + bytes(part))
        i += size
    return out


class Reassembler:
    """Collects written chunks until the final one; push() then returns (message, encrypted)."""

    def __init__(self):
        self._parts = []
        self._encrypted = False

    def push(self, chunk):
        if not chunk:
            return None
        self._parts.append(bytes(chunk[1:]))
        self._encrypted = self._encrypted or bool(chunk[0] & ENCRYPTED)
        if not chunk[0] & FINAL:
            return None
        msg, enc = b"".join(self._parts), self._encrypted
        self._parts, self._encrypted = [], False
        return msg, enc
