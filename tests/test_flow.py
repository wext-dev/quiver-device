"""Whole wake-ups, as main.py runs them, against the protocol stand-in in fake_api.py: setup over
Bluetooth, pairing, frames, the buttons and paging, 304s, a removed screen, an unknown token and
outages.

The test plays the phone. app.run() runs in a thread, as the board would, while the test reads and
writes the GATT service the way the app does (apps/mobile/src/pairing/ble.ts).
"""

import binascii
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim import Sim, SimEPD  # noqa: E402

import bluetooth  # noqa: E402  (the stand-in)
import cryptolib  # noqa: E402
from fake_api import FakeApi  # noqa: E402
from quiver import crypto, framing  # noqa: E402


def until(cond, timeout=10, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    raise AssertionError("timed out waiting for " + what)


def keys_from(qr_text):
    """What the app gets from scanning the setup QR (parseSetupQr() in protocol.ts)."""
    s = qr_text.rsplit(":", 1)[1].replace("-", "+").replace("_", "/")
    return crypto.SetupKeys(binascii.a2b_base64(s + "=" * (-len(s) % 4)))


def encrypt_wifi(ssid, pw, qr_text, lang=None):
    """What the app sends once it has scanned the setup QR (wifiPayload() in protocol.ts); an app
    from before the language doesn't send "lang"."""
    keys = keys_from(qr_text)
    msg = {"ssid": ssid, "pw": pw}
    if lang is not None:
        msg["lang"] = lang
    data = json.dumps(msg).encode()
    pad = 16 - len(data) % 16
    iv = os.urandom(16)
    body = iv + cryptolib.aes(keys.enc, 2, iv).encrypt(data + bytes([pad]) * pad)
    return binascii.b2a_base64(body + crypto.hmac_sha256(keys.mac, body)).strip()


def send_wifi(phone, ssid, pw, qr_text, mtu=20, lang=None):
    phone.write(5, framing.chunks(encrypt_wifi(ssid, pw, qr_text, lang), mtu, encrypted=True))


class Flow(unittest.TestCase):
    def setUp(self):
        self.api = FakeApi()
        self.dir = tempfile.mkdtemp()
        self.sim = Sim(self.dir, api_url=self.api.url, setup_timeout_s=3, retry_minutes=5, pins={"battery": 1})
        self.threads = []
        SimEPD.drawn.clear()
        SimEPD.qrs.clear()
        bluetooth.BLE.instance = None

    def tearDown(self):
        # A wake still in setup (a failed test) must end before the next test: the phone leaves,
        # and setup stops after setup_timeout_s.
        ble = bluetooth.BLE.instance
        if ble is not None and ble._irq is not None:
            ble._irq(2, (1, 0, b""))
        for t in self.threads:
            t.join(15)
        self.api.close()
        shutil.rmtree(self.dir)

    def wake_in_thread(self, **kw):
        out = {}

        def run():
            try:
                out["seconds"] = self.sim.wake(**kw)
            except BaseException as e:  # noqa: BLE001
                out["error"] = e
        t = threading.Thread(target=run, daemon=True)
        t.start()
        self.threads.append(t)
        return t, out

    def phone(self, mtu=23):
        ble = until(lambda: bluetooth.BLE.instance if bluetooth.BLE.instance and bluetooth.BLE.instance.advertising else None, what="advertising")
        until(lambda: ble.by_uuid and ble.values[ble.by_uuid["5f1e0006-8a3c-4b7e-9d2a-7c1b5e9a0f10"]], what="the first status")
        return bluetooth.Central(mtu=mtu)  # the smallest MTU by default: everything is chunked

    def status(self, phone):
        return [json.loads(m) for m in phone.notifications(6)]

    def code(self, phone):
        return until(lambda: next((s["code"] for s in self.status(phone) if s["state"] == "awaiting_claim"), None), what="code")

    def board(self, **kw):
        """Rewrite board.json (before pairing, so the features are reported)."""
        from sim import Sim
        self.sim = Sim(self.dir, api_url=self.api.url, setup_timeout_s=3, retry_minutes=5, **kw)

    def pair(self, features=("button",)):
        """Power on a new board and set it up from the phone. Returns the phone."""
        t, out = self.wake_in_thread(reset="power")
        phone = self.phone()
        adv, resp = bluetooth.BLE.instance.advertising
        self.assertIn(bytes(bluetooth.UUID("5f1e0001-8a3c-4b7e-9d2a-7c1b5e9a0f10")), adv)
        self.assertIn(b"Quiver-E5F6", resp)

        self.assertEqual(json.loads(phone.read(6)[1:])["state"], "idle")
        info = json.loads(phone.read(2)[1:])
        self.assertEqual(phone.read(2)[0] & framing.FINAL, framing.FINAL)
        self.assertEqual(info["hw"], "a1b2c3d4e5f6")
        self.assertEqual(info["panel"], {"id": "ws-4.2-bwr", "w": 400, "h": 300, "colors": "bwr"})
        self.assertEqual(info["features"], list(features))
        qr = SimEPD.qrs[-1]  # the phone scans the QR on the setup screen
        self.assertTrue(qr.startswith("quiver:v2:a1b2c3d4e5f6:"))

        phone.write(3, framing.chunks(b'{"cmd":"scan"}', 20))
        nets = json.loads(until(lambda: phone.notifications(4), what="networks")[0])
        self.assertEqual(nets[0]["ssid"], "Hjemme-2G")

        send_wifi(phone, "Hjemme-2G", "hemmelig", qr)
        code = self.code(phone)
        # "idle" was set before the phone connected; the phone reads it, then gets the rest notified.
        states = [s["state"] for s in self.status(phone)]
        self.assertEqual(states[:4], ["wifi_connecting", "wifi_ok", "registering", "awaiting_claim"])

        # The QR and the code are on the panel too, for pairing without Bluetooth.
        until(lambda: len(SimEPD.drawn) >= 2, what="the code on the panel")  # the setup screen, then the code
        self.assertEqual(SimEPD.qrs[-1], qr)

        # The app adds the screen with the claim secret from the QR; the code isn't needed.
        self.assertEqual(self.api.claim_with_secret(keys_from(qr).claim_secret), code)
        t.join(15)
        self.assertNotIn("error", out)
        self.assertEqual(self.status(phone)[-1]["state"], "claimed")
        self.assertEqual(self.api.frame_requests()[-1][2].get("If-None-Match"), None)
        self.assertEqual(out["seconds"], 900)
        return phone

    def test_setup_pairs_and_draws_the_first_frame(self):
        self.pair()
        self.assertFalse(os.path.exists(os.path.join(self.dir, "lang.txt")))  # an old app sends no language: nothing written
        secrets = self.sim.read("secrets.json")
        self.assertEqual(secrets["wifi"], {"ssid": "Hjemme-2G", "pw": "hemmelig"})
        self.assertTrue(secrets["token"].startswith("tok-"))
        self.assertEqual(len(SimEPD.drawn), 4)  # setup screen, code, "connected", then the frame
        self.assertEqual(SimEPD.drawn[-1][0][0], 1)  # view 1's frame
        reg = next(r for r in self.api.requests if r[1] == "/device/v1/register")[3]
        self.assertEqual((reg["hardwareId"], reg["features"]), ("a1b2c3d4e5f6", ["button"]))
        status = next(r for r in self.api.requests if r[1] == "/device/v1/status")[3]
        self.assertEqual(status["wiring"], "custom")  # the simulated board has a battery divider, which the guide doesn't place
        self.assertIsInstance(status["chip"], dict)
        # Every frame request repeats the features, so the API can catch up on its own.
        frame_req = next(r for r in self.api.requests if r[1].startswith("/device/v1/frame"))
        self.assertEqual(frame_req[2].get("X-Features"), "button")
        self.assertIsNone(bluetooth.BLE.instance.advertising)  # Bluetooth is off again

    def test_button_asks_for_the_next_view_and_timer_wakes_get_304(self):
        self.pair()
        drawn = len(SimEPD.drawn)
        self.assertEqual(self.sim.wake(button=True), 900)
        req = self.api.frame_requests()[-1][2]
        self.assertEqual(req.get("X-Button"), "next")
        self.assertEqual(req.get("If-None-Match"), '"view-1"')
        self.assertEqual(SimEPD.drawn[-1][0][0], 2)

        self.sim.wake()
        self.assertNotIn("X-Button", self.api.frame_requests()[-1][2])
        self.assertEqual(len(SimEPD.drawn), drawn + 1)  # 304: the panel was left alone
        self.assertIn("bat_mv=3920", self.api.frame_requests()[-1][2]["X-Telemetry"])

    def test_a_long_run_of_unchanged_frames_ends_in_a_redraw(self):
        from quiver import app
        self.pair()
        drawn = len(SimEPD.drawn)
        for _ in range(app.REDRAW_AFTER):
            self.sim.wake()
        self.assertEqual(len(SimEPD.drawn), drawn)  # all 304s: the panel was left alone
        # Then the frame is asked for without the ETag and drawn again, in case the panel isn't
        # showing what the ETag says (a refresh that failed without an error).
        self.sim.wake()
        self.assertNotIn("If-None-Match", self.api.frame_requests()[-1][2])
        self.assertEqual(len(SimEPD.drawn), drawn + 1)
        self.sim.wake()
        self.assertIn("If-None-Match", self.api.frame_requests()[-1][2])  # and back to normal
        self.assertEqual(len(SimEPD.drawn), drawn + 1)

    def test_page_buttons_page_through_the_view_and_next_view_starts_over(self):
        self.board(pins={"battery": 1, "page_down": 6, "page_up": 7})
        self.pair(features=("button", "paging"))
        sent = []

        def press(**kw):
            self.sim.wake(**kw)
            sent.append(self.api.frame_requests()[-1][2].get("X-Button"))
            return SimEPD.drawn[-1][0][0]

        self.assertEqual(press(button="page_down"), 1 + 16)
        self.assertEqual(press(button="page_down"), 1 + 32)
        self.assertEqual(press(button="page_down"), 1)  # wrapped to the first page
        self.assertEqual(press(button="page_up"), 1 + 32)  # and back to the last
        self.assertEqual(press(button=True), 2)  # next view, first page
        self.assertEqual(sent, ["page-down"] * 3 + ["page-up", "next"])

        import esp32
        from quiver import board
        from quiver.button import Buttons
        Buttons(board.load(os.path.join(self.dir, "board.json"))).arm()
        self.assertEqual(esp32.armed, ("ext1", [5, 6, 7], esp32.WAKEUP_ALL_LOW))

    def test_one_button_in_pages_mode_short_press_pages_long_press_next_view(self):
        self.board(pins={"battery": 1}, button_mode="pages")
        self.pair(features=("button", "paging"))
        self.sim.wake(button=True)
        self.assertEqual(self.api.frame_requests()[-1][2].get("X-Button"), "page-down")
        self.sim.wake(button=True, hold_ms=1300)
        self.assertEqual(self.api.frame_requests()[-1][2].get("X-Button"), "next")
        self.assertEqual(SimEPD.drawn[-1][0][0], 2)

    def test_power_on_shows_a_start_screen_then_the_frame(self):
        self.pair()
        drawn = len(SimEPD.drawn)
        self.sim.wake(reset="power")
        self.assertEqual(len(SimEPD.drawn), drawn + 2)  # the start screen, then the frame over it
        self.assertEqual(SimEPD.drawn[-1][0][0], 1)
        self.assertIsNone(self.api.frame_requests()[-1][2].get("If-None-Match"))

    def test_after_a_power_cut_a_failure_shows_the_error_at_once(self):
        self.pair()
        self.api.down = True
        drawn = len(SimEPD.drawn)
        self.assertEqual(self.sim.wake(reset="power") // 60, 5)
        # The start-up screen, then the error over it: "connecting" would be wrong for the next 15 min.
        self.assertEqual(len(SimEPD.drawn), drawn + 2)
        self.sim.wake()
        self.assertEqual(len(SimEPD.drawn), drawn + 2)  # the same error isn't drawn again
        self.api.down = False
        self.sim.wake()
        self.assertEqual(SimEPD.drawn[-1][0][0], 1)  # back to the frame

    def released(self, reason):
        """The API says the screen was released: it forgets Wi-Fi and token, and only the app over
        Bluetooth sets it up again."""
        self.pair()
        registrations = len(self.api.codes)
        self.api.gone = {"error": "device_released", "reason": reason}
        bluetooth.BLE.instance = None
        t, out = self.wake_in_thread()
        phone = self.phone(mtu=185)
        secrets = self.sim.read("secrets.json")
        self.assertEqual(secrets, {"wifi": None, "token": None, "device_id": None})
        self.assertEqual(len(self.api.codes), registrations)  # no Wi-Fi, so no code until the app sends one
        # A new setup, a new secret: the QR from the first setup is no good any more.
        self.assertNotEqual(SimEPD.qrs[-1], SimEPD.qrs[0])
        send_wifi(phone, "Hjemme-Gjest", "", SimEPD.qrs[-1], 180)
        self.api.claim(self.code(phone))
        t.join(15)
        self.assertNotIn("error", out)
        self.assertEqual(self.sim.read("secrets.json")["wifi"]["ssid"], "Hjemme-Gjest")

    def test_removed_in_the_app_forgets_wifi_and_token(self):
        self.released("removed")

    def test_a_deleted_account_resets_its_screens_the_same_way(self):
        self.released("account_deleted")

    def test_a_first_frame_that_fails_after_setting_up_again_is_retried(self):
        # Released, set up again in the same wake, and then the API fails: the board keeps its new
        # Wi-Fi and token and tries again in minutes, as after any failure, not in an hour.
        self.pair()
        self.api.gone = {"error": "device_released", "reason": "account_deleted"}
        bluetooth.BLE.instance = None
        t, out = self.wake_in_thread()
        phone = self.phone(mtu=185)
        send_wifi(phone, "Hjemme-Gjest", "", SimEPD.qrs[-1], 180)
        self.api.frames_down = True
        self.api.claim(self.code(phone))
        t.join(15)
        self.assertNotIn("error", out)
        self.assertEqual(out["seconds"] // 60, 5)
        secrets = self.sim.read("secrets.json")
        self.assertEqual(secrets["wifi"]["ssid"], "Hjemme-Gjest")
        self.assertTrue(secrets["token"].startswith("tok-"))
        self.api.frames_down = False
        drawn = len(SimEPD.drawn)
        self.sim.wake()
        self.assertEqual(len(SimEPD.drawn), drawn + 1)  # the first picture, at the next wake

    def test_every_wake_runs_under_a_watchdog_unless_it_stays_awake(self):
        import machine
        from quiver import wake
        cwd = os.getcwd()
        os.chdir(self.dir)  # where board.json is, as on the board
        try:
            machine.BOARD.watchdog = None
            wake._watchdog()
            self.assertEqual(machine.BOARD.watchdog, 30 * 60 * 1000)  # a wake that hangs resets the board
            self.board(stay_awake=True)
            machine.BOARD.watchdog = None
            wake._watchdog()
            self.assertIsNone(machine.BOARD.watchdog)  # it would reset the REPL
        finally:
            os.chdir(cwd)

    def test_a_410_that_isnt_device_released_is_only_an_error(self):
        self.pair()
        self.api.gone = {"error": "gone"}
        self.assertEqual(self.sim.wake() // 60, 5)
        self.assertIsNotNone(self.sim.read("secrets.json")["wifi"])
        self.assertIsNotNone(self.sim.read("secrets.json")["token"])

    def test_unknown_token_is_retried_then_shows_a_code_with_the_wifi_kept(self):
        self.board(token_refused_hours=1)
        self.pair()
        token = self.sim.read("secrets.json")["token"]
        self.api.revoked = True
        drawn = len(SimEPD.drawn)
        # A restored database or a bad deploy looks the same, so nothing is dropped at first.
        waits = [self.sim.wake() for _ in range(4)]
        self.assertEqual([w // 60 for w in waits], [5, 10, 20, 40])
        self.assertEqual(self.sim.read("secrets.json")["token"], token)
        self.assertEqual(len(SimEPD.drawn), drawn + 1)  # the error screen, at the third
        # An hour of it: the token goes, the Wi-Fi stays, and the board registers straight away. The
        # app adds it with the QR on the panel, without Bluetooth.
        qrs = len(SimEPD.qrs)
        t, out = self.wake_in_thread()
        code = until(lambda: len(self.api.codes) > 1 and next(iter(reversed(self.api.codes))), what="new code")
        until(lambda: len(SimEPD.qrs) > qrs + 1, what="the QR with the code")  # setup, then the code screen
        self.assertEqual(self.api.claim_with_secret(keys_from(SimEPD.qrs[-1]).claim_secret), code)
        t.join(15)
        self.assertNotIn("error", out)
        secrets = self.sim.read("secrets.json")
        self.assertEqual((secrets["token"], secrets["wifi"]["ssid"]), ("tok-dc-" + code, "Hjemme-2G"))

    def test_an_unknown_token_that_comes_back_starts_the_count_again(self):
        self.board(token_refused_hours=1)
        self.pair()
        self.api.revoked = True
        self.sim.wake()
        self.sim.wake()
        self.api.revoked = False
        self.sim.wake()
        self.api.revoked = True
        self.assertEqual([self.sim.wake() // 60 for _ in range(4)], [5, 10, 20, 40])
        self.assertIsNotNone(self.sim.read("secrets.json")["token"])

    def test_outages_keep_the_picture_then_show_an_error(self):
        self.pair()
        self.api.down = True
        drawn = len(SimEPD.drawn)
        waits = [self.sim.wake() for _ in range(3)]
        self.assertEqual([w // 60 for w in waits], [5, 10, 20])
        self.assertEqual(len(SimEPD.drawn), drawn + 1)  # only the third failure drew the error screen
        self.api.down = False
        self.sim.wake()
        self.assertEqual(SimEPD.drawn[-1][0][0], 1)  # the frame is back, though its ETag didn't change

    def test_setup_sets_the_clock_before_registering(self):
        """A new board's clock is in 2000 and TLS checks certificate dates against it: setup, not
        just the first wake after it, sets it from NTP before the first HTTPS request."""
        synced = []  # the requests the API had seen when the clock was set
        ntptime = types.ModuleType("ntptime")
        ntptime.settime = lambda: synced.append(len(self.api.requests))
        real, year_2000 = time.gmtime, time.struct_time((2000, 1, 1, 0, 0, 0, 5, 1, 0))
        with open(os.path.join(self.dir, "ca.der"), "wb") as f:
            f.write(b"ca")  # with a CA, the board would speak TLS (plain HTTP to the stand-in here)
        self.board(ca_file="ca.der")
        with mock.patch.dict(sys.modules, ntptime=ntptime), \
                mock.patch("time.gmtime", side_effect=lambda *a: real(*a) if synced else year_2000):
            self.pair()
        self.assertEqual(synced[:1], [0])

    def test_holding_the_button_starts_setup_again(self):
        self.pair()
        bluetooth.BLE.instance = None
        t, out = self.wake_in_thread(button=True, held=True)
        until(lambda: bluetooth.BLE.instance and bluetooth.BLE.instance.advertising, timeout=15, what="advertising again")
        import machine
        machine.BOARD.pins[5] = 1  # released
        self.assertIsNone(self.sim.read("secrets.json")["token"])
        phone = self.phone(mtu=185)
        send_wifi(phone, "Hjemme-Gjest", "", SimEPD.qrs[-1], 180)
        self.api.claim(self.code(phone))
        t.join(15)
        self.assertEqual(self.sim.read("secrets.json")["wifi"]["ssid"], "Hjemme-Gjest")

    def test_wrong_password_is_reported_over_bluetooth(self):
        t, out = self.wake_in_thread(reset="power")
        phone = self.phone()
        qr = SimEPD.qrs[-1]
        send_wifi(phone, "Hjemme-2G", "feil", qr)
        failed = until(lambda: next((s for s in self.status(phone) if s["state"] == "wifi_failed"), None), what="wifi_failed")
        self.assertEqual(failed["err"], "auth")
        self.assertEqual(SimEPD.qrs[-1], qr)  # the Wi-Fi trouble screen keeps the same QR
        send_wifi(phone, "Hjemme-2G", "hemmelig", qr)
        self.api.claim(self.code(phone))  # the typed code still works
        t.join(15)
        self.assertNotIn("error", out)

    def test_wifi_without_the_setup_qr_is_refused(self):
        t, out = self.wake_in_thread(reset="power")
        phone = self.phone()
        qr = SimEPD.qrs[-1]
        # In plain text, as the app sent it before the QR was required.
        phone.write(5, framing.chunks(json.dumps({"ssid": "Hjemme-2G", "pw": "hemmelig"}).encode(), 20))
        failed = until(lambda: next((s for s in self.status(phone) if s["state"] == "wifi_failed"), None), what="wifi_failed")
        self.assertEqual(failed["err"], "key")
        # Encrypted with another setup's QR (one seen earlier, say): refused the same way.
        other = "quiver:v2:a1b2c3d4e5f6:" + crypto.b64url(os.urandom(16))
        send_wifi(phone, "Hjemme-2G", "hemmelig", other)
        failed = until(lambda: [s for s in self.status(phone) if s["state"] == "wifi_failed"][1:], what="a second wifi_failed")
        self.assertEqual(failed[0]["err"], "key")
        self.assertEqual(self.api.codes, {})  # it never joined, so it never registered
        send_wifi(phone, "Hjemme-2G", "hemmelig", qr)
        code = self.code(phone)
        self.assertEqual(self.api.claim_with_secret(keys_from(qr).claim_secret), code)
        t.join(15)
        self.assertNotIn("error", out)


    # ---- language -------------------------------------------------------------------------------

    def lang(self):
        p = os.path.join(self.dir, "lang.txt")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return f.read()

    def drawn_in(self, lang, screen, *args):
        """The planes `screen` draws in `lang`, from the copy of the app the last wake ran."""
        from quiver import text
        from quiver.canvas import Canvas
        was = text.lang()
        text.use(lang)
        try:
            c = Canvas(400, 300, red=True)
            screen(c, *args)
            return bytes(c.black_buf)
        finally:
            text.use(was)

    def test_a_new_board_is_in_english(self):
        t, out = self.wake_in_thread(reset="power")
        phone = self.phone()
        qr = SimEPD.qrs[-1]
        from quiver import screens
        self.assertEqual(SimEPD.drawn[0][0], self.drawn_in("en", screens.setup, qr))
        send_wifi(phone, "Hjemme-2G", "hemmelig", qr)  # an app from before the language
        code = self.code(phone)
        until(lambda: len(SimEPD.drawn) >= 2, what="the code")
        self.assertEqual(SimEPD.drawn[1][0], self.drawn_in("en", screens.code, qr, code, "Quiver-E5F6", 15))
        self.api.claim(code)
        t.join(15)
        self.assertNotIn("error", out)
        self.assertIsNone(self.lang())

    def test_the_apps_language_comes_with_the_wifi(self):
        """board.json's language is the start; the screens after the Wi-Fi (the code, Wi-Fi trouble,
        "connected") are in the app's language, which is kept on flash and wins from then on."""
        self.board(pins={"battery": 1}, lang="nb")
        t, out = self.wake_in_thread(reset="power")
        phone = self.phone()
        qr = SimEPD.qrs[-1]
        from quiver import screens
        self.assertEqual(SimEPD.drawn[0][0], self.drawn_in("nb", screens.setup, qr))
        send_wifi(phone, "Hjemme-2G", "feil", qr, lang="en")
        until(lambda: any(s["state"] == "wifi_failed" for s in self.status(phone)), what="wifi_failed")
        until(lambda: len(SimEPD.drawn) >= 2, what="the Wi-Fi trouble screen")
        from quiver import screens
        self.assertEqual(self.lang(), "en")
        self.assertEqual(SimEPD.drawn[1][0], self.drawn_in("en", screens.wifi_failed, qr, "Quiver-E5F6", "Hjemme-2G", "auth"))
        self.assertNotEqual(SimEPD.drawn[1][0], self.drawn_in("nb", screens.wifi_failed, qr, "Quiver-E5F6", "Hjemme-2G", "auth"))
        # Unknown, or left out: the language stays.
        send_wifi(phone, "Hjemme-2G", "hemmelig", qr, lang="xx")
        code = self.code(phone)
        until(lambda: len(SimEPD.drawn) >= 3, what="the code")
        self.assertEqual(SimEPD.drawn[2][0], self.drawn_in("en", screens.code, qr, code, "Quiver-E5F6", 15))
        self.api.claim(code)
        t.join(15)
        self.assertNotIn("error", out)
        self.assertEqual(self.lang(), "en")
        self.assertEqual(SimEPD.drawn[3][0], self.drawn_in("en", screens.connected, "Quiver-E5F6"))
        # After a power cut too, though board.json still says "nb".
        self.sim.wake(reset="power")
        self.assertEqual(SimEPD.drawn[-2][0], self.drawn_in("en", screens.starting, "Hjemme-2G"))

    def test_the_owners_language_comes_with_frames(self):
        self.pair()
        self.api.language = "en"
        self.sim.wake()  # a 304
        self.assertEqual(self.api.frame_requests()[-1][2].get("If-None-Match"), '"view-1"')
        self.assertEqual(self.lang(), "en")
        path = os.path.join(self.dir, "lang.txt")
        os.utime(path, (1000000000, 1000000000))
        self.sim.wake()  # the same language again: flash isn't written
        self.assertEqual(os.stat(path).st_mtime, 1000000000)
        for bad in ("xx", "", "english"):  # not a language the screens have: kept as it is
            self.api.language = bad
            self.sim.wake()
            self.assertEqual(self.lang(), "en")
        self.assertEqual(os.stat(path).st_mtime, 1000000000)
        self.api.language = "nb"
        self.sim.wake(button=True)  # a 200
        self.assertEqual(self.lang(), "nb")
        # The board's own screens follow: an outage after a Norwegian frame shows its error in Norwegian.
        self.api.language = "nb"
        self.sim.wake()
        self.api.down = True
        for _ in range(3):
            self.sim.wake()
        from quiver import api, screens

        def error(c):
            screens.error(c, *screens.describe(api.ApiError(503, '{"error": "down"}')), 20 * 60, 3)
        self.assertEqual(SimEPD.drawn[-1][0], self.drawn_in("nb", error))
        self.assertNotEqual(SimEPD.drawn[-1][0], self.drawn_in("en", error))


if __name__ == "__main__":
    unittest.main()
