"""The device endpoints of the Quiver API (docs/device-protocol.md, sections 2 and 3, and docs/ota.md)."""

from . import FIRMWARE, MODEL, http


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__("%d %s" % (status, message))
        self.status = status


class Released(Exception):
    """410 device_released: the screen was removed in the app, or its owner deleted their account.
    The board forgets its Wi-Fi and token and starts setup from scratch. `reason` is "removed" or
    "account_deleted"."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Unauthorized(Exception):
    """401: the API doesn't know the token. Not acted on at once (a restored database or a bad deploy
    looks the same); app.py drops the token only once it has gone on for token_refused_hours."""


def _refused(r):
    """Raise for the answers that say the token is no good. Only an explicit device_released is a
    reset: a 410 from anything else in the way is just an error."""
    if r.status == 401:
        raise Unauthorized()
    if r.status == 410:
        try:
            body = r.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and body.get("error") == "device_released":
            raise Released(body.get("reason") or "removed")


class Api:
    def __init__(self, base_url, token=None, ca=None):
        self.base, self.token, self.ca = base_url, token, ca

    def _call(self, method, path, body=None, headers=None, timeout=20):
        h = dict(headers or {})
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        return http.request(method, self.base + path, body, h, timeout=timeout, ca=self.ca)

    # ---- pairing -------------------------------------------------------------------------------

    def register(self, hw, panel, features, claim_hash, model=MODEL):
        """claim_hash: SHA-256 of the claim secret from the setup QR (crypto.py), so the app can add
        the screen with the QR instead of the code.
        → {"deviceCode", "userCode", "expiresIn", "interval", ...}"""
        r = self._call("POST", "/device/v1/register",
                       {"hardwareId": hw, "model": model, "firmware": FIRMWARE, "panel": panel, "features": features,
                        "claimHash": claim_hash})
        if r.status != 200:
            raise ApiError(r.status, (r.body or b"")[:120].decode())
        return r.json()

    def poll_token(self, device_code):
        """→ ("issued", {"deviceId", "deviceToken", "pollSeconds"}) or (error, None) where error is
        "authorization_pending", "slow_down", "expired_token" or "invalid_grant"."""
        r = self._call("POST", "/device/v1/token", {"deviceCode": device_code})
        if r.status == 200:
            return "issued", r.json()
        if r.status == 400:
            return (r.json() or {}).get("error", "invalid_grant"), None
        raise ApiError(r.status, (r.body or b"")[:120].decode())

    # ---- running -------------------------------------------------------------------------------

    def frame(self, etag, telemetry=None, button=None, features=None):
        """One wake's request; `button` is the press to report ("next", "page-down" or "page-up").
        `telemetry` ({"bat_mv": 3920, "rssi": -58, …}) goes as X-Telemetry, and `features` every
        time too, so the API catches up even if it missed a status report.
        → Response; status 200 (frame in body) or 304. Its X-Update and X-Log-Request headers ask
        for more once the frame is drawn."""
        h = {"X-Firmware": FIRMWARE}
        if features is not None:
            h["X-Features"] = ",".join(features) or "none"
        if etag:
            h["If-None-Match"] = etag
        if telemetry:
            h["X-Telemetry"] = ";".join("%s=%s" % (k, v) for k, v in sorted(telemetry.items()) if v is not None)
        if button:
            h["X-Button"] = button
        r = self._call("GET", "/device/v1/frame", headers=h, timeout=30)
        _refused(r)
        if r.status not in (200, 304):
            raise ApiError(r.status, (r.body or b"")[:120].decode())
        return r

    def status(self, panel, features, battery_mv=None, rssi=None, ssid=None, software=None):
        """The full self-report, sent after a start other than from deep sleep and when something
        changed. `software`: model, runtime, boot, ota {mode, keys}, build and reset (app.software())."""
        body = {"firmware": FIRMWARE, "panel": panel, "features": features}
        body.update(software or {})
        if battery_mv is not None:
            body["batteryMv"] = battery_mv
        if rssi is not None:
            body["rssi"] = rssi
        if ssid is not None:
            body["ssid"] = ssid
        r = self._call("POST", "/device/v1/status", body)
        _refused(r)
        if r.status >= 300:
            raise ApiError(r.status, (r.body or b"")[:120].decode())

    def events(self, events):
        """Events queued since the last good wake (outbox.py): errors, update results."""
        r = self._call("POST", "/device/v1/events", {"events": events})
        _refused(r)
        if r.status >= 300:
            raise ApiError(r.status, (r.body or b"")[:120].decode())

    def log(self, text):
        """The end of log.txt, after the frame answer asked for it (X-Log-Request)."""
        r = self._call("POST", "/device/v1/log", text.encode(), {"Content-Type": "text/plain; charset=utf-8"})
        _refused(r)
        if r.status >= 300:
            raise ApiError(r.status, (r.body or b"")[:120].decode())

    # ---- updates (docs/ota.md) -------------------------------------------------------------------

    def offer(self):
        """The release to install: {"id", "kind", "version", "manifest" (base64), "signature",
        "keyId", "url", "size"}, or None."""
        r = self._call("GET", "/device/v1/firmware/offer")
        _refused(r)
        if r.status == 204:
            return None
        if r.status != 200:
            raise ApiError(r.status, (r.body or b"")[:120].decode())
        return r.json()

    def download(self, path, sink):
        """Streams a release payload to sink(stream, length). Public, so no token is sent."""
        r = http.request("GET", self.base + path, timeout=60, ca=self.ca, sink=sink)
        if r.status != 200:
            raise ApiError(r.status, "download failed")
