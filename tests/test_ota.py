"""Updates over the air, against the protocol stand-in (docs/ota.md): the loader's checks, A/B slots,
trial starts and rollback, runtime updates, and what the board reports (telemetry, events, logs).

The board here is already set up (secrets.json written), and trusts a signing key made for the
test through ota_keys, as a self-hosted or development board would.
"""

import base64
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim import Sim, SimEPD  # noqa: E402

import esp32  # noqa: E402  (the stand-in)
import machine  # noqa: E402
import package  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from fake_api import FakeApi  # noqa: E402

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()
STRANGER = Ed25519PrivateKey.generate()
BASE = package._read_constant("FIRMWARE")


def signed(kind, version, payload, key=KEY, **kw):
    manifest = package.manifest(kind, version, payload, **kw)
    return manifest, base64.b64encode(key.sign(manifest)).decode()


def qpk(entries):
    """A QPK archive from (path, bytes) pairs, for archives tools/package.py wouldn't make."""
    out = b"QPK1"
    for path, data in entries:
        out += struct.pack("<H", len(path)) + path.encode() + struct.pack("<I", len(data)) + data
    return out + struct.pack("<H", 0)


class Ota(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.api.token = "tok-ota"
        self.dir = tempfile.mkdtemp()
        self.sim = Sim(self.dir, api_url=self.api.url, retry_minutes=5, pins={"battery": 1}, ota_keys={"test": PUBLIC})
        with open(os.path.join(self.dir, "secrets.json"), "w") as f:
            json.dump({"wifi": {"ssid": "Hjemme-2G", "pw": "hemmelig"}, "token": "tok-ota", "device_id": "dev-1"}, f)
        SimEPD.drawn.clear()
        esp32.Partition.flashed.clear()
        esp32.Partition.boot, esp32.Partition.valid = None, False
        machine.BOARD.watchdog = None
        self.sim.wake(reset="power")

    def tearDown(self):
        self.api.close()
        shutil.rmtree(self.dir)

    def boot_state(self):
        return self.sim.read("boot.json") or {}

    def offer_app(self, version, payload=None, key=KEY, **kw):
        payload = payload if payload is not None else package.qpk(version)[0]
        manifest, sig = signed("app", version, payload, key=key, **kw)
        self.api.release("app", version, manifest, sig, "test", payload)
        return manifest

    def results(self, kind="app"):
        return [e["data"]["result"] for e in self.api.events if e["type"] == "update" and e["data"]["kind"] == kind]

    def test_an_offered_update_is_installed_started_on_trial_and_confirmed(self):
        first = self.api.statuses[-1]
        self.assertEqual((first["ota"]["mode"], first["build"], first["reset"]), ("custom", "custom", "power_on"))
        self.assertIn("test", first["ota"]["keys"])
        self.assertIn("slot=base", self.api.frame_requests()[-1][2]["X-Telemetry"])

        self.offer_app("3.9.0")
        self.assertEqual(self.sim.wake(), 1)  # the frame first, then the update, then straight to it
        paths = [r[1] for r in self.api.requests]
        self.assertLess(len(paths) - paths[::-1].index("/device/v1/frame"), len(paths) - paths[::-1].index("/device/v1/firmware/offer"))
        self.assertEqual(self.boot_state()["pending"], {"slot": "a", "version": "3.9.0", "security": 1, "tries": 0})
        self.api.offer = None

        self.sim.wake()
        self.assertEqual(machine.BOARD.watchdog, 30 * 60 * 1000)  # a hang on trial resets the board
        state = self.boot_state()
        self.assertNotIn("pending", state)
        self.assertEqual((state["slot"], state["version"], state["security"]), ("a", "3.9.0", 1))
        self.assertEqual(self.api.frame_requests()[-1][2]["X-Firmware"], "3.9.0")
        self.assertEqual(self.api.statuses[-1]["firmware"], "3.9.0")
        self.assertEqual(self.api.statuses[-1]["build"], "official")
        self.assertEqual(self.results(), ["ok"])
        self.assertIn("slot=a", self.api.frame_requests()[-1][2]["X-Telemetry"])

        # The next one goes into the other slot.
        self.offer_app("3.9.1")
        self.sim.wake()
        self.api.offer = None
        self.sim.wake()
        self.assertEqual((self.boot_state()["slot"], self.boot_state()["version"]), ("b", "3.9.1"))

    def test_unsigned_tampered_old_or_hostile_releases_are_refused(self):
        def refused(why):
            free = "b" if self.boot_state().get("slot") == "a" else "a"
            self.assertEqual(self.sim.wake(), 900)
            self.assertNotIn("pending", self.boot_state())
            self.assertFalse(os.path.exists(os.path.join(self.dir, "slots", free)))
            self.api.offer = None
            self.sim.wake()  # the outbox goes out with the next good wake
            failure = [e for e in self.api.events if e["type"] == "update"][-1]
            self.assertEqual(failure["data"]["result"], "failed")
            self.assertIn(why, failure["msg"])

        downloads = self.api.downloads
        self.offer_app("4.0.0", key=STRANGER)
        refused("bad signature")
        self.assertEqual(self.api.downloads, downloads)  # refused before downloading

        self.offer_app("4.0.0")
        sha = next(iter(self.api.blobs))
        tampered = bytearray(self.api.blobs[sha])
        tampered[tampered.rfind(b"def ") + 1] ^= 0x20  # inside a file's code: the archive still parses
        self.api.blobs[sha] = bytes(tampered)
        refused("sha256 mismatch")

        self.offer_app(BASE)
        refused("isn't newer")
        self.offer_app("4.0.0", security=0)
        self.offer_app("4.0.0")  # (fine, to raise the installed security to 1)
        self.sim.wake()
        self.api.offer = None
        self.sim.wake()
        self.offer_app("4.0.1", security=0)
        refused("security 0 is below the installed 1")

        self.offer_app("4.1.0", payload=qpk([("quiver/__init__.py", b"FIRMWARE = '4.1.0'"), ("quiver/../secrets.json", b"{}")]))
        refused("bad path")
        self.offer_app("4.1.0", payload=qpk([("board.json", b"{}")]))
        refused("bad path")
        self.assertEqual(self.sim.read("secrets.json")["token"], "tok-ota")
        self.offer_app("4.1.0", min_runtime="1.0.0")
        refused("needs runtime 1.0.0")

    def test_an_update_that_never_reaches_the_api_goes_back_to_the_last_good_app(self):
        self.offer_app("5.0.0")
        self.sim.wake()
        self.api.offer = None
        self.api.down = True
        def reported():
            return [r for r in self.api.requests if r[1] == "/device/v1/status"][-1][3]["firmware"]
        for _ in range(5):
            self.sim.wake()
            self.assertEqual(reported(), "5.0.0")
        self.sim.wake()  # the sixth start: rolled back, so the API's last word (the old app) holds again
        self.assertEqual(self.api.frame_requests()[-1][2]["X-Firmware"], BASE)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "slots", "a")))
        self.api.down = False
        self.sim.wake()
        self.assertEqual(self.results(), ["rolled_back"])
        self.assertTrue(any(e["type"] == "error" for e in self.api.events))  # the outage, reported once through

    def test_a_runtime_update_is_flashed_and_confirmed_or_rolled_back(self):
        self.sim.runtime = {"RUNTIME": "1.0.0", "BOARD": "nano-esp32", "CHIP": "ESP32-S3"}
        self.sim.wake(reset="power")
        self.assertEqual(self.api.statuses[-1]["runtime"], "1.0.0")

        image = os.urandom(10000)
        manifest, sig = signed("runtime", "1.1.0", image, board="nano-esp32", chip="ESP32-S3")
        self.api.release("runtime", "1.1.0", manifest, sig, "test", image)
        # Started with a reset (quiver.app.RESTART): a wake from deep sleep would start the old one.
        self.assertEqual(self.sim.wake(), -1)
        self.assertEqual(bytes(esp32.Partition.flashed["ota_1"][:len(image)]), image)
        self.assertEqual(esp32.Partition.boot, "ota_1")
        self.api.offer = None

        self.sim.runtime["RUNTIME"] = "1.1.0"  # the chip starts the new runtime
        self.sim.wake(reset="soft")
        self.assertTrue(esp32.Partition.valid)
        self.assertEqual(self.results("runtime"), ["ok"])

        # Another, which the chip rolls back (not confirmed in its first start).
        manifest, sig = signed("runtime", "1.2.0", image, board="nano-esp32", chip="ESP32-S3")
        self.api.release("runtime", "1.2.0", manifest, sig, "test", image)
        self.sim.wake()
        self.api.offer = None
        self.sim.wake(reset="power")  # still 1.1.0
        self.assertEqual(self.results("runtime"), ["ok", "rolled_back"])

        # One whose first start doesn't reach the API: the next wake from deep sleep would start it
        # again, so the loader resets the board, and the chip goes back to the runtime that worked.
        esp32.Partition.valid = False
        manifest, sig = signed("runtime", "1.2.1", image, board="nano-esp32", chip="ESP32-S3")
        self.api.release("runtime", "1.2.1", manifest, sig, "test", image)
        self.assertEqual(self.sim.wake(), -1)
        self.api.offer, self.api.down = None, True
        self.sim.runtime["RUNTIME"] = "1.2.1"
        self.sim.wake(reset="soft")
        with self.assertRaises(machine.Reset):
            self.sim.wake()
        self.sim.runtime["RUNTIME"] = "1.1.0"
        self.api.down = False
        self.sim.wake(reset="soft")
        self.assertFalse(esp32.Partition.valid)
        self.assertEqual(self.results("runtime"), ["ok", "rolled_back", "rolled_back"])

        # Should the chip start it again all the same, the loader resets only once: it stays on
        # trial, and is confirmed when it reaches the API.
        self.api.release("runtime", "1.2.1", manifest, sig, "test", image)
        self.assertEqual(self.sim.wake(), -1)
        self.api.offer, self.api.down = None, True
        self.sim.runtime["RUNTIME"] = "1.2.1"
        self.sim.wake(reset="soft")
        with self.assertRaises(machine.Reset):
            self.sim.wake()
        self.api.down = False
        self.sim.wake(reset="soft")
        self.assertTrue(esp32.Partition.valid)
        self.assertEqual(self.results("runtime"), ["ok", "rolled_back", "rolled_back", "ok"])

        # A runtime for another board is refused.
        manifest, sig = signed("runtime", "1.3.0", image, board="xiao-esp32s3")
        self.api.release("runtime", "1.3.0", manifest, sig, "test", image)
        self.sim.wake()
        self.api.offer = None
        self.sim.wake()
        self.assertIn("runtime for xiao-esp32s3", [e for e in self.api.events if e["type"] == "update"][-1]["msg"])

    def test_the_log_is_sent_when_asked(self):
        self.api.log_requested = True
        self.sim.wake()
        self.assertIn("frame", self.api.logs[-1])
        self.assertFalse(self.api.log_requested)


class Trust(unittest.TestCase):
    def test_the_dev_key_and_plain_http_only_for_a_local_api(self):
        from quiver import board
        from quiver_boot import keys, ota
        dev = {"dev": keys.DEV}
        self.assertNotIn("dev", ota.trusted_keys({"api_url": "https://quiver.wext.as/api", "ota_keys": dev}))
        self.assertIn("dev", ota.trusted_keys({"api_url": "http://192.168.1.20:5068", "ota_keys": dev}))
        self.assertEqual(ota.trusted_keys({"api_url": "http://192.168.1.20:5068", "ota_keys": dev, "ota": "off"}), {})
        for url, ok in (("http://localhost:5068", True), ("http://quiver.local", True), ("http://10.0.0.2", True),
                        ("http://172.20.1.1", True), ("http://172.32.1.1", False), ("http://quiver.wext.as/api", False), ("https://quiver.wext.as/api", True)):
            d = tempfile.mkdtemp()
            try:
                path = os.path.join(d, "board.json")
                with open(path, "w") as f:
                    json.dump({"api_url": url}, f)
                if ok:
                    board.load(path)
                else:
                    self.assertRaises(board.BoardError, board.load, path)
            finally:
                shutil.rmtree(d)

    def test_tls_is_checked_against_google_roots_unless_a_ca_file_is_given(self):
        from quiver import app, http
        cfg = {"api_url": "https://quiver.wext.as/api", "ca_file": None}
        self.assertEqual(len(app._ca(cfg)), 5)
        self.assertIsNone(app._ca({"api_url": "http://localhost:5068", "ca_file": None}))
        self.assertRaises(http.HttpError, http._tls, None, "quiver.wext.as", None)

    def test_ed25519_matches_the_reference(self):
        from quiver_boot import ed25519
        msg = b"manifest" * 50
        sig = KEY.sign(msg)
        public = base64.b64decode(PUBLIC)
        self.assertTrue(ed25519.verify(public, msg, sig))
        self.assertFalse(ed25519.verify(public, msg + b".", sig))
        self.assertFalse(ed25519.verify(public, msg, STRANGER.sign(msg)))
        self.assertFalse(ed25519.verify(public, msg, sig[:32] + bytes(32)))
        import hashlib
        for n in (0, 111, 112, 128, 300):
            data = os.urandom(n)
            self.assertEqual(ed25519._Sha512(data).digest(), hashlib.sha512(data).digest())

    def test_versions_compare_like_the_api(self):
        from quiver_boot.ota import compare
        for a, b in (("3.0.0", "3.0.1"), ("3.0.0-beta.1", "3.0.0"), ("3.0.0-beta.2", "3.0.0-beta.10"), ("2.9.9", "10.0.0"), ("", "0.0.1")):
            self.assertLess(compare(a, b), 0, (a, b))
            self.assertGreater(compare(b, a), 0, (a, b))
        self.assertEqual(compare("3.0.0+build.5", "3.0.0"), 0)


if __name__ == "__main__":
    unittest.main()
