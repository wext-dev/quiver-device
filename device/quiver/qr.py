"""QR codes for the setup screens: byte mode, error correction level L, versions 1–5 (up to 106
bytes, one Reed–Solomon block each). That is all the setup QR needs (docs/device-protocol.md,
"The setup QR"). Checked module for module against the qrcode package in tests/test_units.py.

encode(text) → list of bytearray rows, 1 = dark module, without the quiet zone.
"""

# Data and error-correction codewords per version, level L. One block each up to version 5.
_BLOCKS = ((19, 7), (34, 10), (55, 15), (80, 20), (108, 26))

_EXP = bytearray(512)
_LOG = bytearray(256)
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    return _EXP[_LOG[a] + _LOG[b]] if a and b else 0


def _ec(data, n):
    """Reed–Solomon error-correction codewords for data."""
    gen = [1]
    for i in range(n):  # ∏ (x − αⁱ)
        g = gen + [0]
        for j in range(len(gen)):
            g[j + 1] ^= _mul(gen[j], _EXP[i])
        gen = g
    rem = [0] * n
    for d in data:
        f = d ^ rem[0]
        rem = rem[1:] + [0]
        if f:
            for j in range(n):
                rem[j] ^= _mul(gen[j + 1], f)
    return rem


def _codewords(data, version):
    cap, n_ec = _BLOCKS[version - 1]
    bits = []

    def put(value, n):
        for i in range(n - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)  # byte mode
    put(len(data), 8)
    for b in data:
        put(b, 8)
    put(0, min(4, cap * 8 - len(bits)))  # terminator
    while len(bits) % 8:
        bits.append(0)
    out = [int("".join(str(b) for b in bits[i:i + 8]), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(out) < cap:
        out.append(pad)
        pad ^= 0xEC ^ 0x11
    return out + _ec(out, n_ec)


_MASKS = (
    lambda y, x: (y + x) % 2 == 0,
    lambda y, x: y % 2 == 0,
    lambda y, x: x % 3 == 0,
    lambda y, x: (y + x) % 3 == 0,
    lambda y, x: (y // 2 + x // 3) % 2 == 0,
    lambda y, x: (y * x) % 2 + (y * x) % 3 == 0,
    lambda y, x: ((y * x) % 2 + (y * x) % 3) % 2 == 0,
    lambda y, x: ((y + x) % 2 + (y * x) % 3) % 2 == 0,
)


class _Matrix:
    def __init__(self, version):
        self.size = n = 17 + 4 * version
        self.dark = [bytearray(n) for _ in range(n)]
        self.fixed = [bytearray(n) for _ in range(n)]  # function patterns: no data, no mask
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            self._finder(cx, cy)
        for i in range(8, n - 8):  # timing
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        if version > 1:  # one alignment pattern up to version 6
            c = n - 7
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    self.set(c + dx, c + dy, max(abs(dx), abs(dy)) != 1)
        self.format(0)  # reserve the format areas; drawn for real per mask

    def set(self, x, y, dark):
        self.dark[y][x] = 1 if dark else 0
        self.fixed[y][x] = 1

    def _finder(self, cx, cy):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size:
                    d = max(abs(dx), abs(dy))
                    self.set(x, y, d not in (2, 4))

    def format(self, mask):
        data = (0b01 << 3) | mask  # level L
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = ((data << 10) | rem) ^ 0x5412
        bit = [(bits >> i) & 1 for i in range(15)]
        n = self.size
        for i in range(6):
            self.set(8, i, bit[i])
        self.set(8, 7, bit[6])
        self.set(8, 8, bit[7])
        self.set(7, 8, bit[8])
        for i in range(9, 15):
            self.set(14 - i, 8, bit[i])
        for i in range(8):
            self.set(n - 1 - i, 8, bit[i])
        for i in range(8, 15):
            self.set(8, n - 15 + i, bit[i])
        self.set(8, n - 8, 1)  # the dark module

    def place(self, codewords):
        n, i, total = self.size, 0, len(codewords) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            up = (right + 1) & 2 == 0
            for vert in range(n):
                y = n - 1 - vert if up else vert
                for x in (right, right - 1):
                    if not self.fixed[y][x]:
                        if i < total:
                            self.dark[y][x] = (codewords[i >> 3] >> (7 - (i & 7))) & 1
                        i += 1
            right -= 2

    def masked(self, mask):
        f = _MASKS[mask]
        self.format(mask)
        return [bytearray(self.dark[y][x] ^ (0 if self.fixed[y][x] or not f(y, x) else 1) for x in range(self.size))
                for y in range(self.size)]


def _penalty(m):
    n = len(m)
    score = 0
    cols = [bytearray(m[y][x] for y in range(n)) for x in range(n)]
    for line in m + cols:
        run, prev = 0, -1
        for v in line:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    score += run - 2
                run, prev = 1, v
        if run >= 5:
            score += run - 2
        s = bytes(line)
        for pat in (b"\x01\x00\x01\x01\x01\x00\x01\x00\x00\x00\x00", b"\x00\x00\x00\x00\x01\x00\x01\x01\x01\x00\x01"):
            i = s.find(pat)
            while i >= 0:
                score += 40
                i = s.find(pat, i + 1)
    for y in range(n - 1):
        for x in range(n - 1):
            v = m[y][x]
            if v == m[y][x + 1] == m[y + 1][x] == m[y + 1][x + 1]:
                score += 3
    dark = sum(sum(r) for r in m)
    score += abs(dark * 20 - n * n * 10) // (n * n) * 10
    return score


def encode(text, mask=None):
    """The modules for text (str or bytes). mask: 0–7, or None for the one with the lowest penalty."""
    data = text.encode() if isinstance(text, str) else bytes(text)
    for version in range(1, len(_BLOCKS) + 1):
        if len(data) + 2 <= _BLOCKS[version - 1][0]:
            break
    else:
        raise ValueError("%d bytes is too long for a setup QR" % len(data))
    m = _Matrix(version)
    m.place(_codewords(data, version))
    if mask is not None:
        return m.masked(mask)
    best = None
    for k in range(8):
        cand = m.masked(k)
        p = _penalty(cand)
        if best is None or p < best[0]:
            best = (p, cand)
    return best[1]
