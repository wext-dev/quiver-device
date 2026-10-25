"""Packs and signs firmware releases (docs/ota.md#the-manifest).

A release is a folder: manifest.json (signed byte for byte), manifest.sig (base64 Ed25519
signature), key-id, payload (the .qpk archive of the app, or a runtime's app partition image) and,
for a runtime, flash.bin (the full image for first-time flashing).

  python tools/package.py app --version 3.1.0 [--mpy] [--out dist]
  python tools/package.py runtime --version 1.0.0 --board nano-esp32 --chip ESP32-S3 \\
      --image micropython.bin --flash firmware.bin [--out dist]
  python tools/package.py sign dist/app-3.1.0 --key my.key --key-id me
  python tools/package.py verify dist/app-3.1.0                 # as a board would, with keys.py
  python tools/package.py keygen --out my.key                    # prints the public key
  python tools/package.py add-key me <base64 public key>          # trust it in boot/quiver_boot/keys.py

Quiver's own releases are signed with keys that are not in this repository. To run your own API
and releases, make a key, add its public key to a board's board.json (ota_keys), and sign your
releases with it (docs/ota.md#diy). For local development, sign with the public dev key:

  python tools/package.py app --version 3.1.0-dev.1 --sign-dev

Only the standard library, plus `cryptography` for signing and keygen, and `mpy-cross` for --mpy.
"""

import argparse
import ast
import base64
import datetime
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIRMWARE = os.path.dirname(HERE)
APP = os.path.join(FIRMWARE, "device", "quiver")
KEYS_PY = os.path.join(FIRMWARE, "boot", "quiver_boot", "keys.py")
DEV_KEY = os.path.join(FIRMWARE, "keys", "dev.key")


# ---- the app archive ----------------------------------------------------------------------------

def _app_files(src=APP):
    for root, dirs, names in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(names):
            if name.endswith((".pyc", ".orig")):
                continue
            path = os.path.join(root, name)
            yield "quiver/" + os.path.relpath(path, src).replace(os.sep, "/"), path


def _read_constant(name, src=APP):
    with open(os.path.join(src, "__init__.py"), encoding="utf-8") as f:
        m = re.search(r"^%s = (.+)$" % name, f.read(), re.M)
    return ast.literal_eval(m.group(1)) if m else None


def qpk(version, src=APP, mpy=False):
    """The app as a QPK archive (b"QPK1", entries <u16 len><path><u32 size><data>, then u16 0),
    with FIRMWARE set to `version`. With mpy, .py files are compiled to .mpy. → (bytes, mpy version or None)."""
    out = [b"QPK1"]
    mpy_version = None
    with tempfile.TemporaryDirectory() as tmp:
        for rel, path in _app_files(src):
            with open(path, "rb") as f:
                data = f.read()
            if rel == "quiver/__init__.py":
                data, n = re.subn(rb'^FIRMWARE = "[^"]*"', b'FIRMWARE = "' + version.encode() + b'"', data, flags=re.M)
                if n != 1:
                    raise SystemExit("FIRMWARE = \"…\" not found in quiver/__init__.py")
            if mpy and rel.endswith(".py"):
                src_py = os.path.join(tmp, "m.py")
                dst = os.path.join(tmp, "m.mpy")
                with open(src_py, "wb") as f:
                    f.write(data)
                subprocess.run([sys.executable, "-m", "mpy_cross", "-s", rel, "-o", dst, src_py], check=True)
                with open(dst, "rb") as f:
                    data = f.read()
                # Bytecode .mpy files load wherever the major version matches (no native code here).
                mpy_version = str(data[1])
                rel = rel[:-3] + ".mpy"
            name = rel.encode()
            out.append(struct.pack("<H", len(name)) + name + struct.pack("<I", len(data)) + data)
    out.append(struct.pack("<H", 0))
    return b"".join(out), mpy_version


def manifest(kind, version, payload, board="*", min_runtime=None, mpy=None, security=None, chip=None):
    """The manifest as the exact bytes that are signed: compact JSON with sorted keys."""
    m = {
        "format": 1, "kind": kind, "version": version, "board": board, "min_runtime": min_runtime, "mpy": mpy,
        "security": _read_constant("SECURITY") if security is None else security,
        "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload),
        "git": _git(), "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if chip:
        m["chip"] = chip
    return json.dumps(m, sort_keys=True, separators=(",", ":")).encode()


def _git():
    try:
        return subprocess.run(["git", "-C", FIRMWARE, "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_release(folder, manifest_bytes, payload, flash=None):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "manifest.json"), "wb") as f:
        f.write(manifest_bytes)
    with open(os.path.join(folder, "payload"), "wb") as f:
        f.write(payload)
    if flash is not None:
        with open(os.path.join(folder, "flash.bin"), "wb") as f:
            f.write(flash)
    return folder


# ---- signing -------------------------------------------------------------------------------------

def _private_key(path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    with open(path) as f:
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(f.read().strip()))


def public_key(path):
    from cryptography.hazmat.primitives import serialization
    raw = _private_key(path).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def write_signature(folder, key_id, signature_b64):
    """Stores a signature made elsewhere (any Ed25519 signer) next to the manifest."""
    with open(os.path.join(folder, "manifest.sig"), "w") as f:
        f.write(signature_b64.strip() + "\n")
    with open(os.path.join(folder, "key-id"), "w") as f:
        f.write(key_id + "\n")


def sign(folder, key_id, key):
    """Signs the manifest with a private key file (base64 raw Ed25519)."""
    with open(os.path.join(folder, "manifest.json"), "rb") as f:
        data = f.read()
    write_signature(folder, key_id, base64.b64encode(_private_key(key).sign(data)).decode())


def verify(folder, public_b64=None):
    """True if the folder's signature checks out the way a board checks it: with the loader's own
    Ed25519 code and, unless a key is given, the key named by key-id in boot/quiver_boot/keys.py."""
    sys.path.insert(0, os.path.join(FIRMWARE, "boot"))
    from quiver_boot import ed25519, keys

    def read(name, mode="rb"):
        with open(os.path.join(folder, name), mode) as f:
            return f.read()
    key_id = read("key-id", "r").strip()
    public_b64 = public_b64 or keys.KEYS.get(key_id) or (keys.DEV if key_id == "dev" else None)
    if public_b64 is None:
        sys.exit("No key %s in boot/quiver_boot/keys.py." % key_id)
    return ed25519.verify(base64.b64decode(public_b64), read("manifest.json"), base64.b64decode(read("manifest.sig", "r")))


# ---- keys ----------------------------------------------------------------------------------------

def keygen(out):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    raw = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(base64.b64encode(raw).decode() + "\n")
    return public_key(out)


def add_key(key_id, public_b64):
    """Adds (or replaces) a trusted key in boot/quiver_boot/keys.py."""
    if len(base64.b64decode(public_b64)) != 32:
        sys.exit("Not a 32-byte Ed25519 public key.")
    with open(KEYS_PY, encoding="utf-8") as f:
        src = f.read()
    src = re.sub(r'\n    "%s": "[^"]*",' % re.escape(key_id), "", src)
    src = src.replace("KEYS = {\n", 'KEYS = {\n    "%s": "%s",\n' % (key_id, public_b64), 1)
    with open(KEYS_PY, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)


# ---- command line --------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("app", help="pack the app")
    a.add_argument("--version", required=True)
    a.add_argument("--mpy", action="store_true", help="compile to .mpy (pip install mpy-cross==<MicroPython version>)")
    a.add_argument("--min-runtime")
    a.add_argument("--out", default=os.path.join(FIRMWARE, "dist"))
    a.add_argument("--sign-dev", action="store_true", help="sign with the public development key")
    r = sub.add_parser("runtime", help="describe a runtime build as a release")
    r.add_argument("--version", required=True)
    r.add_argument("--board", required=True)
    r.add_argument("--chip", required=True)
    r.add_argument("--image", required=True, help="the app partition image (micropython.bin)")
    r.add_argument("--flash", help="the full flash image (firmware.bin)")
    r.add_argument("--out", default=os.path.join(FIRMWARE, "dist"))
    s = sub.add_parser("sign", help="sign with a private key file")
    s.add_argument("folder")
    s.add_argument("--key-id", required=True)
    s.add_argument("--key", required=True, help="a private key file (base64 raw Ed25519)")
    v = sub.add_parser("verify", help="check the signature as a board would")
    v.add_argument("folder")
    v.add_argument("--public-key", help="base64 public key (default: key-id's in keys.py)")
    k = sub.add_parser("keygen")
    k.add_argument("--out", required=True)
    ak = sub.add_parser("add-key")
    ak.add_argument("key_id")
    ak.add_argument("public_key")
    args = p.parse_args(argv)

    if args.cmd == "app":
        payload, mpy = qpk(args.version, mpy=args.mpy)
        folder = write_release(os.path.join(args.out, "app-" + args.version), manifest("app", args.version, payload, min_runtime=args.min_runtime, mpy=mpy), payload)
        if args.sign_dev:
            sign(folder, "dev", DEV_KEY)
        print(folder)
    elif args.cmd == "runtime":
        with open(args.image, "rb") as f:
            payload = f.read()
        flash = None
        if args.flash:
            with open(args.flash, "rb") as f:
                flash = f.read()
        print(write_release(os.path.join(args.out, "runtime-%s-%s" % (args.board, args.version)),
                            manifest("runtime", args.version, payload, board=args.board, chip=args.chip), payload, flash))
    elif args.cmd == "sign":
        sign(args.folder, args.key_id, args.key)
    elif args.cmd == "verify":
        if not verify(args.folder, args.public_key):
            sys.exit("The signature does not verify.")
        print("ok")
    elif args.cmd == "keygen":
        print(keygen(args.out))
    elif args.cmd == "add-key":
        add_key(args.key_id, args.public_key)


if __name__ == "__main__":
    main()
