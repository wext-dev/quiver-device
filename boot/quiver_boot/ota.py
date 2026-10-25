"""Checks and installs signed releases (docs/ota.md). The app asks the API for an offer and hands
over the download; everything that decides whether code may run is here, in the loader.

A release comes as a manifest (JSON, signed with Ed25519 over its exact bytes) and a payload whose
SHA-256 and size the manifest names. check() refuses a manifest before a byte is downloaded unless
it is signed with a trusted key, for this board, newer than what runs, at least as secure, and fits
the runtime. install_app() and install_runtime() then hash the payload as it streams in and only
stage it (loader.stage) if it matches. Nothing is ever run from an unchecked payload.

An app payload is a QPK archive: b"QPK1", then entries <u16 path length><path><u32 size><data>,
then a zero path length. Every path is inside quiver/: a release can't touch board.json,
secrets.json or the loader.
"""

import binascii
import hashlib
import json
import struct
import sys

from . import BOARD, RUNTIME, ed25519, keys, loader

MAX_APP_BYTES = 2 * 1024 * 1024
MAX_FILES = 200
_PATH_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-./"


class OtaError(Exception):
    """Why a release was refused or didn't install, in a few words for the log and the API."""


def local(url):
    """True for an API on this network: localhost, a .local name, a private IPv4 address or a bare
    host name. Plain HTTP and the development key are allowed only there."""
    host = url.split("://", 1)[-1].split("/", 1)[0].rsplit(":", 1)[0].lower()
    if host in ("localhost",) or host.endswith(".local") or "." not in host:
        return True
    parts = host.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts):
        return False
    a, b = int(parts[0]), int(parts[1])
    return a in (10, 127) or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31) or (a == 169 and b == 254)


def trusted_keys(cfg):
    """Key id → public key: Quiver's release keys (frozen), plus ota_keys from board.json for a
    self-hosted API or development. None at all when board.json says "ota": "off"."""
    if cfg.get("ota") == "off":
        return {}
    out = {}
    for kid, b64 in list(keys.KEYS.items()) + list((cfg.get("ota_keys") or {}).items()):
        if b64 == keys.DEV and not local(cfg["api_url"]):
            continue
        try:
            key = binascii.a2b_base64(b64)
        except ValueError:
            continue
        if len(key) == 32:
            out[kid] = key
    return out


def mode(cfg):
    """As the status report says it: "off", "custom" (own keys too) or "official"."""
    if not trusted_keys(cfg):
        return "off"
    return "custom" if cfg.get("ota_keys") else "official"


def board(cfg):
    """The board name releases use: the runtime's own, else board.json's model."""
    return BOARD or cfg.get("model") or "esp32"


def mpy_version():
    """The .mpy version this MicroPython loads bytecode of, as "6"; None on CPython. (Native code
    would also need the sub-version and architecture; releases have none.)"""
    v = getattr(sys.implementation, "_mpy", None)
    return None if v is None else str(v & 0xFF)


def _version(v):
    core, _, pre = str(v).split("+")[0].partition("-")
    nums = core.split(".")
    if len(nums) != 3 or not all(n.isdigit() for n in nums):
        return None
    return [int(n) for n in nums], pre.split(".") if pre else []


def compare(a, b):
    """Semantic versions, as the API compares them: negative when a is older. Unparseable sorts first."""
    x, y = _version(a), _version(b)
    if x is None or y is None:
        return (x is not None) - (y is not None)
    if x[0] != y[0]:
        return -1 if x[0] < y[0] else 1
    p, q = x[1], y[1]
    if not p or not q:
        return (not p) - (not q)
    for i in range(min(len(p), len(q))):
        if p[i] == q[i]:
            continue
        if p[i].isdigit() and q[i].isdigit():
            return -1 if int(p[i]) < int(q[i]) else 1
        if p[i].isdigit() != q[i].isdigit():
            return -1 if p[i].isdigit() else 1
        return -1 if p[i] < q[i] else 1
    return (len(p) > len(q)) - (len(p) < len(q))


def check(manifest, signature, key_id, kind, current, cfg):
    """The manifest as a dict, if this board should install it; otherwise OtaError."""
    key = trusted_keys(cfg).get(key_id)
    if key is None:
        raise OtaError("untrusted key %s" % key_id)
    if not ed25519.verify(key, manifest, signature):
        raise OtaError("bad signature")
    try:
        m = json.loads(manifest)
    except ValueError:
        raise OtaError("manifest isn't JSON")
    if m.get("format") != 1 or m.get("kind") != kind:
        raise OtaError("not a %s manifest" % kind)
    if kind == "app" and m.get("board") != "*":
        raise OtaError("app for board %s" % m.get("board"))
    if kind == "runtime":
        if RUNTIME is None:
            raise OtaError("no Quiver runtime to replace")
        if m.get("board") != board(cfg):
            raise OtaError("runtime for %s, this is %s" % (m.get("board"), board(cfg)))
    if compare(m.get("version"), current) <= 0:
        raise OtaError("%s isn't newer than %s" % (m.get("version"), current))
    if m.get("security", 0) < loader.security():
        raise OtaError("security %s is below the installed %d" % (m.get("security"), loader.security()))
    if kind == "app" and m.get("min_runtime") and (RUNTIME is None or compare(RUNTIME, m["min_runtime"]) < 0):
        raise OtaError("needs runtime %s" % m["min_runtime"])
    if kind == "app" and m.get("mpy") and mpy_version() and m["mpy"] != mpy_version():
        raise OtaError("built for .mpy %s, this loads %s" % (m["mpy"], mpy_version()))
    size = m.get("size")
    if not isinstance(size, int) or size <= 0 or (kind == "app" and size > MAX_APP_BYTES):
        raise OtaError("size %s" % size)
    sha = m.get("sha256")
    if not isinstance(sha, str) or len(sha) != 64:
        raise OtaError("no sha256")
    return m


class _Payload:
    """Reads exactly the manifest's size from the download and hashes every byte."""

    def __init__(self, stream, size):
        self._stream, self.left, self._hash = stream, size, hashlib.sha256()

    def read(self, n):
        if n > self.left:
            raise OtaError("archive runs past its size")
        out = b""
        while len(out) < n:
            chunk = self._stream.read(n - len(out))
            if not chunk:
                raise OtaError("download ended early")
            out += chunk
        self.left -= n
        self._hash.update(out)
        return out

    def finish(self, expected):
        if self.left:
            raise OtaError("%d bytes after the archive" % self.left)
        if binascii.hexlify(self._hash.digest()).decode() != expected:
            raise OtaError("sha256 mismatch")


def _check_path(path):
    parts = path.split("/")
    if (len(path) > 120 or parts[0] != "quiver" or len(parts) < 2
            or any(p in ("", ".", "..") for p in parts) or any(c not in _PATH_CHARS for c in path)):
        raise OtaError("bad path in archive: %r" % path[:40])


def install_app(m, stream):
    """Streams a QPK archive into the free slot and stages it for the next start."""
    slot = loader.free_slot()
    root = loader.slot_dir(slot)
    loader.remove_tree(root)
    loader.make_dirs(root)
    p = _Payload(stream, m["size"])
    files = {}
    try:
        if p.read(4) != b"QPK1":
            raise OtaError("not a QPK archive")
        while True:
            n = struct.unpack("<H", p.read(2))[0]
            if n == 0:
                break
            path = p.read(n).decode()
            _check_path(path)
            if path in files or len(files) >= MAX_FILES:
                raise OtaError("too many or repeated files")
            size = struct.unpack("<I", p.read(4))[0]
            h = hashlib.sha256()
            loader.make_dirs(root + "/" + path.rsplit("/", 1)[0])
            with open(root + "/" + path, "wb") as f:
                while size:
                    chunk = p.read(min(size, 1024))
                    f.write(chunk)
                    h.update(chunk)
                    size -= len(chunk)
            files[path] = binascii.hexlify(h.digest()).decode()
        p.finish(m["sha256"])
        if "quiver/__init__.py" not in files and "quiver/__init__.mpy" not in files:
            raise OtaError("no app in the archive")
    except Exception:
        loader.remove_tree(root)
        raise
    with open(root + "/.files", "w") as f:
        json.dump(files, f)
    loader.stage(slot, m["version"], m.get("security", 0))


def install_runtime(m, stream):
    """Streams a runtime image into the next OTA partition and boots it next, on trial."""
    import esp32
    part = esp32.Partition(esp32.Partition.RUNNING).get_next_update()
    block = part.ioctl(5, 0)
    if m["size"] > part.ioctl(4, 0) * block:
        raise OtaError("runtime is larger than the partition")
    p = _Payload(stream, m["size"])
    buf = bytearray(block)
    n = 0
    while p.left:
        chunk = p.read(min(block, p.left))
        buf[:len(chunk)] = chunk
        for i in range(len(chunk), block):
            buf[i] = 0xFF
        part.writeblocks(n, buf)
        n += 1
    p.finish(m["sha256"])  # a mismatch leaves the partition written but never booted
    part.set_boot()
    loader.stage_runtime(m["version"])
