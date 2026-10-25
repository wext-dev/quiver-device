"""Run the board's firmware on a PC against a real Quiver API, and save what the panel shows.

The firmware runs unchanged (with the stand-ins in fakes/). This script plays the phone: it
"scans" the setup QR off the panel, sends Wi-Fi encrypted with it over the fake Bluetooth, adds the
screen for a user through the app API with the QR's claim secret (like the app does), and lets the
board fetch its frames. Every image the panel gets is
written as a PNG.

  # an API running locally that accepts development tokens:
  python tools/simulate.py --api http://localhost:5068 --user-token "$(npm run -s dev-token -- KEY dev_user)"
  python tools/simulate.py ... --press 2        # then press the button twice
  python tools/simulate.py ... --pages 3        # then page down three times
  python tools/simulate.py ... --dir /tmp/board # keep the board's files between runs
  python tools/simulate.py ... --dir /tmp/board --dev-key --wakes 2
      # trust the development signing key and wake twice: installs a dev-signed update the
      # the API offers (tools/package.py app … --sign-dev), then starts it (docs/ota.md)

Needs Pillow and cryptography (pip install pillow cryptography).
"""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sim import Sim, SimEPD  # noqa: E402

import binascii  # noqa: E402
import os as _os  # noqa: E402

import bluetooth  # noqa: E402  (the stand-in)
import cryptolib  # noqa: E402
from quiver import crypto, framing  # noqa: E402
from quiver_boot.keys import DEV as DEV_KEY  # noqa: E402


def wait_for(cond, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    sys.exit("Timed out waiting for " + what)


def scan(qr_text):
    """The keys the app gets from the setup QR (parseSetupQr() in protocol.ts)."""
    hw, s = qr_text.split(":")[2:]
    s = s.replace("-", "+").replace("_", "/")
    return hw, crypto.SetupKeys(binascii.a2b_base64(s + "=" * (-len(s) % 4)))


def wifi_message(keys, ssid, pw):
    """wifiPayload() in protocol.ts."""
    data = json.dumps({"ssid": ssid, "pw": pw}).encode()
    pad = 16 - len(data) % 16
    iv = _os.urandom(16)
    body = iv + cryptolib.aes(keys.enc, 2, iv).encrypt(data + bytes([pad]) * pad)
    return binascii.b2a_base64(body + crypto.hmac_sha256(keys.mac, body)).strip()


def claim(api, token, hw, keys, name):
    body = {"hardwareId": hw, "claimSecret": keys.claim_secret, "name": name}
    req = urllib.request.Request(api + "/api/home/devices/claim", json.dumps(body).encode(),
                                 {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://localhost:5068")
    p.add_argument("--user-token", required=True, help="a session token for the user who claims the screen")
    p.add_argument("--name", default="Simulert skjerm")
    p.add_argument("--panel", default="ws-4.2-bwr")
    p.add_argument("--press", type=int, default=0, help="button presses (next view) after pairing")
    p.add_argument("--pages", type=int, default=0, help="page-down presses after that")
    p.add_argument("--dir", help="the board's filesystem (default: a new temporary folder)")
    p.add_argument("--out", default=".", help="where to write the PNGs")
    p.add_argument("--dev-key", action="store_true", help="trust the development signing key (local API only), to try updates")
    p.add_argument("--wakes", type=int, default=0, help="timer wakes after that (an update installs on one, starts on the next)")
    a = p.parse_args()

    os.makedirs(a.out, exist_ok=True)
    board = {"ota_keys": {"dev": DEV_KEY}} if a.dev_key else {}
    sim = Sim(a.dir or tempfile.mkdtemp(prefix="quiver-board-"), api_url=a.api, panel=a.panel, setup_timeout_s=60,
              pins={"page_down": 6, "page_up": 7}, **board)
    saved = 0

    def save(label):
        nonlocal saved
        while saved < len(SimEPD.drawn):
            saved += 1
            path = os.path.join(a.out, "panel-%02d-%s.png" % (saved, label))
            Sim.png(path, SimEPD.drawn[saved - 1])
            print("wrote", path)

    secrets = sim.read("secrets.json") or {}
    if not secrets.get("token"):
        out = {}
        t = threading.Thread(target=lambda: out.update(seconds=sim.wake(reset="power")), daemon=True)
        t.start()
        wait_for(lambda: bluetooth.BLE.instance and bluetooth.BLE.instance.advertising, 10, "advertising")
        phone = bluetooth.Central()
        info = json.loads(phone.read(2)[1:])
        print("board says:", info)
        hw, keys = scan(SimEPD.qrs[-1])
        print("setup QR:", SimEPD.qrs[-1])
        phone.write(5, framing.chunks(wifi_message(keys, "Hjemme-2G", "hemmelig"), 180, encrypted=True))
        code = wait_for(lambda: next((json.loads(m)["code"] for m in phone.notifications(6) if json.loads(m)["state"] == "awaiting_claim"), None), 30, "a pairing code")
        print("pairing code (not needed with the QR):", code)
        save("setup")
        device = claim(a.api, a.user_token, hw, keys, a.name)
        print("claimed as", device["id"], device["name"], device["features"])
        t.join(60)
        print("first wake done, next in %s s" % out.get("seconds"))
        save("first")
    else:
        print("next in %d s" % sim.wake())
        save("wake")
    for i in range(a.press):
        print("button press %d: next in %d s" % (i + 1, sim.wake(button=True)))
        save("press%d" % (i + 1))
    for i in range(a.pages):
        print("page down %d: next in %d s" % (i + 1, sim.wake(button="page_down")))
        save("page%d" % (i + 1))
    for i in range(a.wakes):
        print("wake %d: next in %d s" % (i + 1, sim.wake()))
        save("wake%d" % (i + 1))
    state = sim.read("boot.json")
    if state:
        print("loader:", json.dumps(state))


if __name__ == "__main__":
    main()
