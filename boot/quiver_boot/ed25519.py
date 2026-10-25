"""Ed25519 signature verification (RFC 8032), in plain Python so it runs on stock MicroPython too.

Only verification, and only for release manifests (a few hundred bytes): a check takes about a
second on an ESP32-S3, once per update. SHA-512 comes from hashlib where the port has it (CPython,
some MicroPython builds) and from the small implementation below where it doesn't (the ESP32 port's
hashlib has SHA-256 only). Based on the reference code in RFC 8032, section 6.
"""

import hashlib

_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)  # sqrt(-1)


def _sha512(data):
    h = getattr(hashlib, "sha512", None)
    return h(data).digest() if h else _Sha512(data).digest()


class _Sha512:
    """FIPS 180-4 SHA-512, enough for short messages."""

    _K = [
        0x428A2F98D728AE22, 0x7137449123EF65CD, 0xB5C0FBCFEC4D3B2F, 0xE9B5DBA58189DBBC, 0x3956C25BF348B538,
        0x59F111F1B605D019, 0x923F82A4AF194F9B, 0xAB1C5ED5DA6D8118, 0xD807AA98A3030242, 0x12835B0145706FBE,
        0x243185BE4EE4B28C, 0x550C7DC3D5FFB4E2, 0x72BE5D74F27B896F, 0x80DEB1FE3B1696B1, 0x9BDC06A725C71235,
        0xC19BF174CF692694, 0xE49B69C19EF14AD2, 0xEFBE4786384F25E3, 0x0FC19DC68B8CD5B5, 0x240CA1CC77AC9C65,
        0x2DE92C6F592B0275, 0x4A7484AA6EA6E483, 0x5CB0A9DCBD41FBD4, 0x76F988DA831153B5, 0x983E5152EE66DFAB,
        0xA831C66D2DB43210, 0xB00327C898FB213F, 0xBF597FC7BEEF0EE4, 0xC6E00BF33DA88FC2, 0xD5A79147930AA725,
        0x06CA6351E003826F, 0x142929670A0E6E70, 0x27B70A8546D22FFC, 0x2E1B21385C26C926, 0x4D2C6DFC5AC42AED,
        0x53380D139D95B3DF, 0x650A73548BAF63DE, 0x766A0ABB3C77B2A8, 0x81C2C92E47EDAEE6, 0x92722C851482353B,
        0xA2BFE8A14CF10364, 0xA81A664BBC423001, 0xC24B8B70D0F89791, 0xC76C51A30654BE30, 0xD192E819D6EF5218,
        0xD69906245565A910, 0xF40E35855771202A, 0x106AA07032BBD1B8, 0x19A4C116B8D2D0C8, 0x1E376C085141AB53,
        0x2748774CDF8EEB99, 0x34B0BCB5E19B48A8, 0x391C0CB3C5C95A63, 0x4ED8AA4AE3418ACB, 0x5B9CCA4F7763E373,
        0x682E6FF3D6B2B8A3, 0x748F82EE5DEFB2FC, 0x78A5636F43172F60, 0x84C87814A1F0AB72, 0x8CC702081A6439EC,
        0x90BEFFFA23631E28, 0xA4506CEBDE82BDE9, 0xBEF9A3F7B2C67915, 0xC67178F2E372532B, 0xCA273ECEEA26619C,
        0xD186B8C721C0C207, 0xEADA7DD6CDE0EB1E, 0xF57D4F7FEE6ED178, 0x06F067AA72176FBA, 0x0A637DC5A2C898A6,
        0x113F9804BEF90DAE, 0x1B710B35131C471B, 0x28DB77F523047D84, 0x32CAAB7B40C72493, 0x3C9EBE0A15C9BEBC,
        0x431D67C49C100D4C, 0x4CC5D4BECB3E42B6, 0x597F299CFC657E2A, 0x5FCB6FAB3AD6FAEC, 0x6C44198C4A475817,
    ]

    def __init__(self, data):
        self._data = data

    def digest(self):
        m = 2**64 - 1

        def rot(x, n):
            return ((x >> n) | (x << (64 - n))) & m

        h = [0x6A09E667F3BCC908, 0xBB67AE8584CAA73B, 0x3C6EF372FE94F82B, 0xA54FF53A5F1D36F1,
             0x510E527FADE682D1, 0x9B05688C2B3E6C1F, 0x1F83D9ABFB41BD6B, 0x5BE0CD19137E2179]
        data = bytes(self._data)
        msg = data + b"\x80" + bytes((111 - len(data)) % 128) + (len(data) * 8).to_bytes(16, "big")
        for off in range(0, len(msg), 128):
            w = [int.from_bytes(msg[off + 8 * i:off + 8 * i + 8], "big") for i in range(16)]
            for i in range(16, 80):
                s0 = rot(w[i - 15], 1) ^ rot(w[i - 15], 8) ^ (w[i - 15] >> 7)
                s1 = rot(w[i - 2], 19) ^ rot(w[i - 2], 61) ^ (w[i - 2] >> 6)
                w.append((w[i - 16] + s0 + w[i - 7] + s1) & m)
            a, b, c, d, e, f, g, hh = h
            for i in range(80):
                t1 = (hh + (rot(e, 14) ^ rot(e, 18) ^ rot(e, 41)) + ((e & f) ^ ((e ^ m) & g)) + self._K[i] + w[i]) & m
                t2 = ((rot(a, 28) ^ rot(a, 34) ^ rot(a, 39)) + ((a & b) ^ (a & c) ^ (b & c))) & m
                hh, g, f, e, d, c, b, a = g, f, e, (d + t1) & m, c, b, a, (t1 + t2) & m
            h = [(x + y) & m for x, y in zip(h, (a, b, c, d, e, f, g, hh))]
        return b"".join(x.to_bytes(8, "big") for x in h)


def _inv(x):
    return pow(x, _P - 2, _P)


# Points in extended coordinates (X, Y, Z, T), x = X/Z, y = Y/Z, x*y = T/Z.

def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)  # the neutral element
    while s > 0:
        if s & 1:
            q = _add(q, p)
        p = _add(p, p)
        s >>= 1
    return q


def _equal(p, q):
    return (p[0] * q[2] - q[0] * p[2]) % _P == 0 and (p[1] * q[2] - q[1] * p[2]) % _P == 0


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _I % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


def _decompress(b):
    if len(b) != 32:
        return None
    y = int.from_bytes(b, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


_GY = 4 * _inv(5) % _P
_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def verify(public, msg, signature):
    """True if `signature` (64 bytes) over `msg` was made with the key `public` (32 bytes)."""
    if len(public) != 32 or len(signature) != 64:
        return False
    a = _decompress(public)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(_sha512(bytes(signature[:32]) + bytes(public) + bytes(msg)), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))
