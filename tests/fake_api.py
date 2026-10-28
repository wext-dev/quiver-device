"""A stand-in for the API's device endpoints (docs/device-protocol.md), for the firmware tests.
It follows the protocol's behaviour, so the firmware can be tested without .NET or Postgres; the
real API is covered by tools/simulate.py."""

import hashlib
import json
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def frame(width=400, height=300, red=True, fill=0x00, mark=0, gray=False):
    """An RHF1 frame: black plane filled with `fill`, `mark` in its first byte (view + 16 × page
    in the fake API), red plane empty. With gray, the second plane is the low grey bit instead."""
    stride = (width + 7) // 8
    red = red or gray
    header = struct.pack("<4sBBBBHHHH", b"RHF1", 1, 2 if red else 1, 1 if gray else 0, 0, width, height, stride, 0)
    black = bytearray([fill]) * (stride * height)
    black[0] = mark
    return header + bytes(black) + (bytes(stride * height) if red else b"")


class FakeApi:
    def __init__(self):
        self.requests = []  # (method, path, headers, body)
        self.codes = {}  # userCode → deviceCode
        self.claim_hashes = {}  # userCode → claimHash sent at registration
        self.claimed = set()
        self.token = None
        self.revoked = False  # the token is unknown: 401
        self.gone = None  # the JSON body of a 410 instead, e.g. {"error": "device_released", "reason": "removed"}
        self.down = False
        self.frames_down = False  # 503 for frames only: the API fails after the board has its token
        self.view = 1  # which "view" the screen shows; X-Button: next moves it on
        self.views = 2
        self.page = 0  # and its page; X-Button: page-down / page-up, wrapping; a new view starts at 0
        self.pages = 3
        self.statuses = []  # the bodies of status reports
        self.events = []  # events the board sent
        self.logs = []  # logs the board sent
        self.log_requested = False  # X-Log-Request on the next frame answer
        self.offer = None  # the update offer (GET /device/v1/firmware/offer); X-Update while set
        self.blobs = {}  # sha256 → payload, public at /device/v1/firmware/blobs/<sha256>
        self.downloads = 0
        self.language = None  # X-Language on frame answers (200 and 304): the owner's language
        api = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body=b"", headers=None, ctype="application/json"):
                if isinstance(body, (dict, list)):
                    body = json.dumps(body).encode()
                self.send_response(status)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if body:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if not raw:
                    return None
                return json.loads(raw) if "json" in (self.headers.get("Content-Type") or "") else raw.decode()

            def do_POST(self):
                body = self._body()
                api.requests.append(("POST", self.path, dict(self.headers), body))
                if api.down:
                    return self._send(503, {"error": "down"})
                if self.path == "/device/v1/register":
                    code = "K7MQ-%04d" % (len(api.codes) + 1)
                    api.codes[code] = "dc-" + code
                    api.claim_hashes[code] = body.get("claimHash")
                    return self._send(200, {"deviceCode": "dc-" + code, "userCode": code, "verificationUri": "x", "expiresIn": 900, "interval": 1})
                if self.path == "/device/v1/token":
                    dc = body["deviceCode"]
                    if dc in api.claimed:
                        api.claimed.discard(dc)
                        api.token = "tok-" + dc
                        api.revoked = False
                        api.gone = None
                        return self._send(200, {"deviceId": "dev-1", "deviceToken": api.token, "pollSeconds": 900})
                    return self._send(400, {"error": "authorization_pending" if dc in api.codes.values() else "invalid_grant"})
                if self.path == "/device/v1/status":
                    if not self._authed():
                        return self._refuse()
                    api.statuses.append(body)
                    return self._send(204)
                if self.path == "/device/v1/events":
                    if not self._authed():
                        return self._refuse()
                    api.events.extend(body["events"])
                    return self._send(200, {"accepted": len(body["events"])})
                if self.path == "/device/v1/log":
                    if not self._authed():
                        return self._refuse()
                    api.logs.append(body)
                    api.log_requested = False
                    return self._send(204)
                self._send(404)

            def _authed(self):
                return not api.revoked and api.gone is None and self.headers.get("Authorization") == "Bearer %s" % api.token

            def _refuse(self):
                return self._send(401) if api.gone is None else self._send(410, api.gone)

            def do_GET(self):
                api.requests.append(("GET", self.path, dict(self.headers), None))
                if api.down:
                    return self._send(503, {"error": "down"})
                if self.path.startswith("/device/v1/firmware/blobs/"):
                    blob = api.blobs.get(self.path.rsplit("/", 1)[1])
                    api.downloads += 1
                    return self._send(200, blob, ctype="application/octet-stream") if blob is not None else self._send(404)
                if self.path == "/device/v1/firmware/offer":
                    if not self._authed():
                        return self._refuse()
                    return self._send(200, api.offer) if api.offer else self._send(204)
                if self.path == "/device/v1/frame" and api.frames_down:
                    return self._send(503, {"error": "down"})
                if self.path != "/device/v1/frame":
                    return self._send(404)
                if not self._authed():
                    return self._refuse()
                press = self.headers.get("X-Button")
                if press == "next":
                    api.view, api.page = api.view % api.views + 1, 0
                elif press in ("page-down", "page-up"):
                    api.page = (api.page + (1 if press == "page-down" else -1)) % api.pages
                etag = '"view-%d"' % api.view if not api.page else '"view-%d-p%d"' % (api.view, api.page)
                headers = {"ETag": etag, "X-Poll-Seconds": "900"}
                if api.log_requested:
                    headers["X-Log-Request"] = "1"
                if api.offer:
                    headers["X-Update"] = "1"
                if api.language:
                    headers["X-Language"] = api.language
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304, headers=headers)
                self._send(200, frame(mark=api.view + 16 * api.page), headers, "application/vnd.quiver.frame")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def claim(self, user_code):
        self.claimed.add(self.codes[user_code])

    def claim_with_secret(self, claim_secret):
        """As the app does with the setup QR: the code whose registration carries the secret's hash."""
        h = hashlib.sha256(claim_secret.encode()).hexdigest()
        code = next(c for c, ch in reversed(list(self.claim_hashes.items())) if ch == h)
        self.claim(code)
        return code

    def frame_requests(self):
        return [r for r in self.requests if r[1] == "/device/v1/frame"]

    def close(self):
        self.server.shutdown()


    def release(self, offer_kind, version, manifest, signature, key_id, payload):
        """Offers a release: the manifest (bytes, as signed) and payload, as the API's offer does."""
        import base64
        sha = hashlib.sha256(payload).hexdigest()
        self.blobs[sha] = payload
        self.offer = {"id": "rel-1", "kind": offer_kind, "version": version, "manifest": base64.b64encode(manifest).decode(),
                      "signature": signature, "keyId": key_id, "url": "/device/v1/firmware/blobs/" + sha, "size": len(payload)}
