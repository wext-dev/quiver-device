"""Setup mode: get Wi-Fi over Bluetooth, register with the API, show the pairing code, and wait
until the user claims it in the app (docs/device-protocol.md, sections 1 and 2).

    idle → wifi_connecting → wifi_ok → registering → awaiting_claim(code) → claimed
                    └→ wifi_failed (auth | no_ap | dhcp | key)    └→ error (api_unreachable | expired)

Every setup starts with a new setup secret (crypto.py), shown as a QR code on the panel and never
stored. The app scans it to encrypt the Wi-Fi it sends; a Wi-Fi message that isn't encrypted with
it is refused. The board registers with the hash of the claim secret derived from it, so the app
adds the screen to a home with the QR as well: the pairing code is only a fallback to type.

The panel follows along (screens.py): the setup screen with the QR, Wi-Fi trouble, the QR with the
code, then "connected" while the first frame is fetched. A board that still has Wi-Fi (the API
stopped knowing its token) registers straight away, and the app adds it from the QR without
Bluetooth.

Returns "claimed" (secrets now hold Wi-Fi and the token) or "stopped" (nothing happened for
setup_timeout_s; the board sleeps until the button or RESET).
"""

import os
import time

import machine

from . import FIRMWARE, board, screens, wifi
from .api import Api, ApiError
from .crypto import DecryptError, SetupKeys, decrypt_wifi
from .http import HttpError
from .log import exception, log


MAX_CODES = 3  # about 45 minutes of codes on screen


class _Setup:
    def __init__(self, cfg, secrets, state, panel, ca):
        self.cfg, self.secrets, self.state, self.panel = cfg, secrets, state, panel
        self.hw = board.hardware_id()
        self.name = board.advertised_name(self.hw)
        self.api = Api(cfg["api_url"], None, ca)
        self.prov = None
        self.keys = self.qr = None  # made once Bluetooth is on (run())
        self.status = {"state": "idle", "code": None, "err": None}
        self.reg = None  # the registration being claimed
        self.creds = None  # the Wi-Fi it was made over
        self.interval = 5
        self.next_poll = 0
        self.expires = 0
        self.codes = 0  # codes shown so far; an unclaimed one is renewed a few times, not forever

    def set(self, state, code=None, err=None):
        self.status = {"state": state, "code": code, "err": err}
        log("setup: %s%s" % (state, " (%s)" % err if err else ""))
        if self.prov is not None:
            self.prov.set_status(self.status)

    def info(self):
        return {"hw": self.hw, "model": board.model(self.cfg), "fw": FIRMWARE, "panel": board.panel_report(self.cfg),
                "bat_mv": board.battery_mv(self.cfg), "features": board.features(self.cfg)}

    def draw(self, screen, *args):
        """Start drawing a screen and carry on; the panel finishes the refresh in the background."""
        canvas = self.panel.new_canvas()
        screen(canvas, *args)
        self.panel.show_canvas(canvas, wait=False)

    # ---- steps ---------------------------------------------------------------------------------

    def join(self, ssid, pw):
        """Wi-Fi, then registration. Leaves Wi-Fi up for polling the token."""
        self.reg = None
        self.set("wifi_connecting")
        try:
            wifi.connect(ssid, pw, self.cfg["wifi_timeout_s"])
        except wifi.WifiError as e:
            self.set("wifi_failed", err=e.reason)
            self.draw(screens.wifi_failed, self.qr, self.name, ssid, e.reason)
            return
        self.set("wifi_ok")
        self.creds = {"ssid": ssid, "pw": pw}
        self.register()

    def register(self):
        self.codes += 1
        self.set("registering")
        try:
            self.reg = self.api.register(self.hw, board.panel_report(self.cfg), board.features(self.cfg), self.keys.claim_hash, board.model(self.cfg))
        except (HttpError, ApiError) as e:
            exception("register", e)
            self.set("error", err="api_unreachable")
            return
        now = time.ticks_ms()
        self.interval = self.reg.get("interval", 5)
        self.next_poll = time.ticks_add(now, self.interval * 1000)
        self.expires = time.ticks_add(now, self.reg.get("expiresIn", 900) * 1000)
        code = self.reg["userCode"]
        self.set("awaiting_claim", code=code)
        self.draw(screens.code, self.qr, code, self.name, self.reg.get("expiresIn", 900) // 60)

    def poll(self):
        """One token poll. True once claimed."""
        self.next_poll = time.ticks_add(time.ticks_ms(), self.interval * 1000)
        try:
            result, body = self.api.poll_token(self.reg["deviceCode"])
        except (HttpError, ApiError) as e:  # keep trying until the code expires
            exception("token", e)
            return False
        if result == "issued":
            self.secrets.wifi = self.creds
            self.secrets.token = body["deviceToken"]
            self.secrets.device_id = body.get("deviceId")
            self.secrets.save()
            self.set("claimed")
            self.draw(screens.connected, self.name)  # the first frame waits for this refresh
            return True
        if result == "slow_down":
            self.interval += 5
        elif result == "expired_token" or time.ticks_diff(time.ticks_ms(), self.expires) > 0:
            if self.codes >= MAX_CODES:
                self.set("error", err="expired")
                self.reg = None
            else:
                log("setup: code expired, registering again")
                self.register()
        elif result != "authorization_pending":
            self.set("error", err="expired")
            self.reg = None
        return False

    def wifi_message(self, raw, encrypted):
        """Only Wi-Fi encrypted with this setup's secret is taken: whoever sends it has seen the QR on
        the panel. Anything else is "key": the app should scan the QR again."""
        if not encrypted:
            log("setup: refused Wi-Fi sent without the setup QR")
            self.set("wifi_failed", err="key")
            return None
        try:
            return decrypt_wifi(raw, self.keys)
        except DecryptError as e:
            exception("wifi payload", e)
            self.set("wifi_failed", err="key")
            return None

    # ---- the loop ------------------------------------------------------------------------------

    def run(self, reason):
        from .ble import Provisioner
        self.prov = Provisioner(self.name, self.info())
        # The secret comes from the hardware RNG, which is only truly random with the radio on.
        self.keys = SetupKeys(os.urandom(16))
        self.qr = self.keys.qr_text(self.hw)
        log("setup: started (%s)" % reason)
        self.draw(screens.setup, self.qr)
        self.state.etag = None  # the panel no longer shows a frame
        self.set("idle")
        timeout = self.cfg["setup_timeout_s"] * 1000
        last_activity = time.ticks_ms()
        try:
            if self.secrets.wifi:  # known Wi-Fi: pairing can go ahead without Bluetooth
                self.join(self.secrets.wifi["ssid"], self.secrets.wifi["pw"])
            while True:
                ev = self.prov.next_event()
                if ev is not None:
                    last_activity = time.ticks_ms()
                    if ev[0] == "scan":
                        self.prov.set_networks(wifi.scan())
                    elif ev[0] == "wifi":
                        creds = self.wifi_message(ev[1], ev[2])
                        if creds and creds.get("ssid"):
                            self.join(creds["ssid"], creds.get("pw", ""))
                    elif ev[0] == "forget":
                        self.secrets.forget()
                        self.reg = None
                        wifi.off()
                        self.set("idle")
                    elif ev[0] == "reboot":
                        machine.reset()
                if self.reg is not None and time.ticks_diff(time.ticks_ms(), self.next_poll) >= 0:
                    if self.poll():
                        time.sleep_ms(1500)  # let the "claimed" notification reach the app
                        return "claimed"
                    last_activity = time.ticks_ms()  # a code on screen counts as progress
                if self.reg is None and not self.prov.connected and time.ticks_diff(time.ticks_ms(), last_activity) > timeout:
                    log("setup: stopped after %d s without progress" % (timeout // 1000))
                    wifi.off()
                    canvas = self.panel.new_canvas()
                    screens.stopped(canvas, self.name, has_button=self.cfg["pins"].get("button") is not None)
                    self.panel.show_canvas(canvas)
                    return "stopped"
                time.sleep_ms(100)
        finally:
            self.prov.stop()


def run(cfg, secrets, state, panel, ca, reason):
    """reason: "new", "button" (held 5 s), "removed" or "account_deleted" (the API said 410; Wi-Fi is
    gone too), "revoked" (401 for token_refused_hours) or "forgotten"."""
    return _Setup(cfg, secrets, state, panel, ca).run(reason)
