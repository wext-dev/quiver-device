"""Pure parts of the firmware, under CPython with the stand-ins in tools/fakes.

    python -m unittest discover tests
"""

import binascii
import hashlib
import hmac
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sim  # noqa: E402,F401  (installs the stand-ins)

from fake_api import frame as make_frame  # noqa: E402
from quiver import api, board, crypto, framing, frame, http, qr, screens, wifi  # noqa: E402
from quiver.app import retry_after  # noqa: E402
from quiver.canvas import Canvas  # noqa: E402

# Made as the app's wifiPayload() does it (compact JSON, AES-128-CBC, HMAC-SHA256 tag), secret
# 01..10, iv a0..af.
APP_VECTOR = b"oKGio6SlpqeoqaqrrK2ur889z9OpKsVWrKGSo67FM+Cj/H5Nk0ORGVTpCwwERKGNagV3irZdL4lW+lFHgISO/EP/UPwYdAuOchc5cUO50EP/XPSbetZARRSKGQhZKLWs"
SECRET = bytes(range(1, 17))
QR_TEXT = "quiver:v2:a1b2c3d4e5f6:AQIDBAUGBwgJCgsMDQ4PEA"  # SECRET's


class Framing(unittest.TestCase):
    def test_round_trip_in_small_chunks(self):
        msg = json.dumps({"ssid": "Hjemme-2G", "pw": "x" * 100}).encode()
        parts = framing.chunks(msg, 20)
        self.assertTrue(all(len(p) <= 20 for p in parts))
        self.assertEqual([p[0] for p in parts], [0] * (len(parts) - 1) + [framing.FINAL])
        r = framing.Reassembler()
        out = [r.push(p) for p in parts]
        self.assertEqual(out[-1], (msg, False))
        self.assertTrue(all(o is None for o in out[:-1]))

    def test_empty_message_is_one_final_chunk(self):
        self.assertEqual(framing.chunks(b"", 20), [bytes((framing.FINAL,))])

    def test_encrypted_flag_survives(self):
        r = framing.Reassembler()
        for p in framing.chunks(b"abc" * 30, 23, encrypted=True):
            done = r.push(p)
        self.assertEqual(done, (b"abc" * 30, True))


class Crypto(unittest.TestCase):
    def test_keys_match_the_app(self):
        k = crypto.SetupKeys(SECRET)
        self.assertEqual(k.enc.hex(), "0219e16e34faa99c820974a323459d29")
        self.assertEqual(k.claim_secret, "SYRtZnHddyhNBqhEoIKc1FVOaRbeK3S0rL1_kDxPJ4Y")
        self.assertEqual(k.claim_hash, hashlib.sha256(k.claim_secret.encode()).hexdigest())
        self.assertEqual(k.qr_text("a1b2c3d4e5f6"), QR_TEXT)

    def test_hmac_is_hmac(self):
        self.assertEqual(crypto.hmac_sha256(b"k" * 70, b"msg"), hmac.new(b"k" * 70, b"msg", hashlib.sha256).digest())

    def test_decrypts_what_the_app_encrypts(self):
        self.assertEqual(crypto.decrypt_wifi(APP_VECTOR, crypto.SetupKeys(SECRET)), {"ssid": "Hjemme-2G", "pw": "hemmelig æøå"})

    def test_another_setup_secret_or_a_changed_byte_is_an_error(self):
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_wifi(APP_VECTOR, crypto.SetupKeys(bytes(16)))
        blob = bytearray(binascii.a2b_base64(APP_VECTOR))
        blob[20] ^= 1
        with self.assertRaises(crypto.DecryptError):
            crypto.decrypt_wifi(binascii.b2a_base64(blob), crypto.SetupKeys(SECRET))


class Qr(unittest.TestCase):
    def test_matches_the_qrcode_package(self):
        import qrcode
        for text in (QR_TEXT, "x", "y" * 32, "z" * 53, "w" * 106):
            for mask in range(8):
                q = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=0, mask_pattern=mask)
                q.add_data(text.encode(), optimize=0)
                q.make(fit=True)
                self.assertEqual([list(r) for r in qr.encode(text, mask)], [[int(v) for v in r] for r in q.get_matrix()], (len(text), mask))

    def test_setup_qr_is_version_3(self):
        self.assertEqual(len(qr.encode(QR_TEXT)), 29)
        with self.assertRaises(ValueError):
            qr.encode("x" * 107)


class Frames(unittest.TestCase):
    def test_parses_without_copying(self):
        f = frame.parse(make_frame(mark=0x81), 400, 300)
        self.assertEqual((f.width, f.height, f.stride), (400, 300, 50))
        self.assertEqual(f.black[0], 0x81)
        self.assertEqual(len(f.black), 15000)
        self.assertEqual(len(f.red), 15000)

    def test_black_white_frame_has_no_red_plane(self):
        f = frame.parse(make_frame(red=False))
        self.assertIsNone(f.red)
        self.assertFalse(f.gray)

    def test_grey_frame_has_high_and_low_planes(self):
        f = frame.parse(make_frame(gray=True, mark=0x80), 400, 300)
        self.assertTrue(f.gray)
        self.assertIsNone(f.red)
        self.assertEqual((f.black[0], len(f.low)), (0x80, 15000))

    def test_rejects_wrong_size_and_garbage(self):
        with self.assertRaises(frame.FrameError):
            frame.parse(make_frame(width=800, height=480), 400, 300)
        with self.assertRaises(frame.FrameError):
            frame.parse(make_frame()[:-1])
        with self.assertRaises(frame.FrameError):
            frame.parse(b"PNG" + bytes(40))


class Board(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "board.json")

    def write(self, data):
        with open(self.path, "w") as f:
            json.dump(data, f)

    def test_defaults_and_features(self):
        cfg = board.load(self.path)
        self.assertEqual(board.panel_report(cfg), {"id": "ws-4.2-bwr", "w": 400, "h": 300, "colors": "bwr"})
        self.assertEqual(board.features(cfg), ["button"])
        self.write({"pins": {"button": None}})
        self.assertEqual(board.features(board.load(self.path)), [])
        self.write({"pins": {"page_down": 6, "page_up": 7}})
        self.assertEqual(board.features(board.load(self.path)), ["button", "paging"])
        self.write({"button_mode": "pages"})
        self.assertEqual(board.features(board.load(self.path)), ["button", "paging"])
        self.write({"button_mode": "pages", "pins": {"button": None}})
        self.assertEqual(board.features(board.load(self.path)), [])
        # A black/white 4.2" draws grey unless told not to; the panel report stays the same shape.
        self.write({"panel": "ws-4.2-bw"})
        cfg = board.load(self.path)
        self.assertEqual(board.features(cfg), ["button", "gray4"])
        self.assertEqual(board.panel_report(cfg), {"id": "ws-4.2-bw", "w": 400, "h": 300, "colors": "bw"})
        self.write({"panel": "ws-4.2-bw", "gray": False})
        self.assertEqual(board.features(board.load(self.path)), ["button"])

    def test_unknown_key_and_panel_are_errors(self):
        self.write({"pannel": "x"})
        with self.assertRaises(board.BoardError):
            board.load(self.path)
        self.write({"panel": "ws-9.9"})
        with self.assertRaises(board.BoardError):
            board.load(self.path)
        self.write({"button_mode": "page"})
        with self.assertRaises(board.BoardError):
            board.load(self.path)

    def test_wiring_is_default_only_where_the_guide_puts_things(self):
        self.assertEqual(board.wiring(board.load(self.path)), "default")
        # Leaving a part out, or adding page buttons where the guide puts them, is still the guide's wiring.
        self.write({"pins": {"button": None, "page_down": 6, "page_up": 7}})
        self.assertEqual(board.wiring(board.load(self.path)), "default")
        for given in ({"pins": {"cs": 4}}, {"pins": {"page_down": 8}}, {"pins": {"battery": 1}}, {"button_active_low": False}):
            self.write(given)
            self.assertEqual(board.wiring(board.load(self.path)), "custom", given)

    def test_hardware_report(self):
        report = board.hardware(board.load(self.path))
        self.assertEqual(report["wiring"], "default")
        self.assertTrue(set(report["chip"]) <= {"machine", "flash", "heap"})
        json.dumps(report)  # goes into the status body as it is

    def test_name_matches_the_app(self):
        self.assertEqual(board.advertised_name("a1b2c3d4e5f6"), "Quiver-E5F6")


class Http(unittest.TestCase):
    def test_a_certificate_tls_refuses_is_an_http_error(self):
        """MicroPython raises ValueError when it won't trust the certificate (a new board's clock in
        2000, say). Uncaught, it ended setup in the middle of "registering"."""
        import socket
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]

        def refuse(sock, host, ca):
            raise ValueError("The certificate validity starts in the future")
        tls, http._tls = http._tls, refuse
        try:
            with self.assertRaises(http.HttpError) as e:
                http.request("POST", "https://127.0.0.1:%d/device/v1/register" % port, {}, ca=b"ca")
            self.assertIn("validity", str(e.exception))
        finally:
            http._tls = tls
            server.close()


class Retry(unittest.TestCase):
    def test_doubles_up_to_the_cap(self):
        cfg = board.DEFAULTS
        self.assertEqual([retry_after(cfg, n) // 60 for n in (1, 2, 3, 4, 5, 6, 9)], [5, 10, 20, 40, 80, 120, 120])


class Wifi(unittest.TestCase):
    def test_scan_is_strongest_first_with_protocol_security(self):
        nets = wifi.scan()
        self.assertEqual(nets[0], {"ssid": "Hjemme-2G", "rssi": -52, "sec": "wpa2"})
        self.assertEqual(nets[1]["sec"], "open")

    def test_wrong_password_and_missing_network(self):
        with self.assertRaises(wifi.WifiError) as e:
            wifi.connect("Hjemme-2G", "feil", 1)
        self.assertEqual(e.exception.reason, "auth")
        with self.assertRaises(wifi.WifiError) as e:
            wifi.connect("Finnes-ikke", "x", 1)
        self.assertEqual(e.exception.reason, "no_ap")


class Screens(unittest.TestCase):
    """Every screen draws, in both panel types; set QUIVER_SCREENS_OUT to get PNGs for a look."""

    def draw(self, name, fn, red=True):
        c = Canvas(400, 300, red=red)
        fn(c)
        self.assertTrue(any(c.black_buf), name)
        out = os.environ.get("QUIVER_SCREENS_OUT")
        if out:
            os.makedirs(out, exist_ok=True)
            sim.Sim.png(os.path.join(out, "%s-%s.png" % (name, "bwr" if red else "bw")), (c.black_buf, c.red_buf))

    def test_setup_shows_the_versions(self):
        FIRMWARE = screens.FIRMWARE  # other tests load newer copies of the app; this is the one screens has
        self.assertEqual(screens.versions(), "Firmware %s · runtime stock" % FIRMWARE)
        import quiver_boot
        quiver_boot.RUNTIME, was = "1.0.3", quiver_boot.RUNTIME
        try:
            self.assertEqual(screens.versions(), "Firmware %s · runtime 1.0.3" % FIRMWARE)
        finally:
            quiver_boot.RUNTIME = was

    def test_all_screens(self):
        for red in (True, False):
            self.draw("setup", lambda c: screens.setup(c, QR_TEXT), red)
            self.draw("code", lambda c: screens.code(c, QR_TEXT, "K7MQ-4XRT", "Quiver-E5F6", 15), red)
            self.draw("stopped", lambda c: screens.stopped(c, "Quiver-E5F6"), red)
            self.draw("starting", lambda c: screens.starting(c, "Hjemme-2G"), red)
            self.draw("wifi-failed", lambda c: screens.wifi_failed(c, QR_TEXT, "Quiver-E5F6", "Hjemme-2G", "no_ap"), red)
            self.draw("connected", lambda c: screens.connected(c, "Quiver-E5F6"), red)
            title, detail = screens.describe(wifi.WifiError("auth", "Hjemme-2G"))
            self.draw("error", lambda c: screens.error(c, title, detail, 2400, 4), red)
            refused = screens.describe(api.Unauthorized())
            self.draw("error-unauthorized", lambda c: screens.error(c, refused[0], refused[1], 2400, 4), red)


if __name__ == "__main__":
    unittest.main()
