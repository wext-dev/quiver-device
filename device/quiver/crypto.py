"""The setup secret behind the QR code on the setup screens (docs/device-protocol.md, "The setup
QR"). Mirrors setupKeys() and wifiPayload() in apps/mobile/src/pairing/protocol.ts.

Setup makes a new 16-byte secret every time it starts. It is never stored: the QR on the panel is
the only way to learn it, so only someone in front of the screen can set it up. Three keys are
derived from it with HMAC-SHA256:

- wifi-enc (16 bytes): AES-128-CBC key for the Wi-Fi credentials.
- wifi-mac (32 bytes): HMAC-SHA256 over iv ‖ ciphertext, checked before anything is decrypted.
- claim: the claim secret the app hands the API to add the screen to a home. The board only sends
  its SHA-256 (claimHash) when it registers, so neither the API nor anyone listening learns it.

The Wi-Fi message is base64(iv[16] ‖ AES-128-CBC(PKCS#7(json)) ‖ tag[32]).
"""

import binascii
import hashlib
import json

import cryptolib

_CBC = 2
_BLOCK = 64


class DecryptError(ValueError):
    pass


def hmac_sha256(key, msg):
    if len(key) > _BLOCK:
        key = hashlib.sha256(key).digest()
    key = key + bytes(_BLOCK - len(key))
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key) + msg).digest()
    return hashlib.sha256(bytes(b ^ 0x5C for b in key) + inner).digest()


def b64url(b):
    return binascii.b2a_base64(b).decode().strip().rstrip("=").replace("+", "-").replace("/", "_")


class SetupKeys:
    def __init__(self, secret):
        self.secret = secret
        self.enc = hmac_sha256(secret, b"quiver-v2 wifi-enc")[:16]
        self.mac = hmac_sha256(secret, b"quiver-v2 wifi-mac")
        self.claim_secret = b64url(hmac_sha256(secret, b"quiver-v2 claim"))
        self.claim_hash = binascii.hexlify(hashlib.sha256(self.claim_secret.encode()).digest()).decode()

    def qr_text(self, hw):
        return "quiver:v2:%s:%s" % (hw, b64url(self.secret))


def _same(a, b):
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= x ^ y
    return diff == 0


def decrypt_wifi(data, keys):
    """→ {"ssid", "pw"}. data is the reassembled message (base64 text)."""
    try:
        blob = binascii.a2b_base64(data)
    except ValueError:
        raise DecryptError("not base64")
    if len(blob) < 64 or (len(blob) - 48) % 16:
        raise DecryptError("bad length %d" % len(blob))
    body, tag = blob[:-32], blob[-32:]
    if not _same(hmac_sha256(keys.mac, body), tag):
        raise DecryptError("bad tag (wrong setup QR?)")
    plain = cryptolib.aes(keys.enc, _CBC, body[:16]).decrypt(body[16:])
    pad = plain[-1]
    if not 1 <= pad <= 16 or plain[-pad:] != bytes((pad,)) * pad:
        raise DecryptError("bad padding")
    try:
        return json.loads(plain[:-pad])
    except ValueError:
        raise DecryptError("not JSON")
