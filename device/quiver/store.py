"""What the board remembers.

secrets.json on flash (survives power cuts): the Wi-Fi it was given during setup and its device
token. The token is the only credential the board ever holds. It's revocable and works for this one
screen only. The setup secret behind the QR on the setup screens is never stored (crypto.py).

lang.txt on flash: the language of the board's own screens (text.py), as the app said during setup
or the API says with every frame. Until then there is none, and the board draws in board.json's
"lang", or English. Not a secret, and kept when the board forgets its Wi-Fi and token. Written only
when it changes, since it comes with every frame.

RTC memory (survives deep sleep and RESET, not a power cut): the ETag of the frame on the panel,
failures in a row, how long the token has been refused, and what was last reported to the API. After a power cut the panel is simply
fetched and redrawn.
"""

import json

import machine

from . import text
from .log import log

SECRETS = "secrets.json"
LANG = "lang.txt"


def _write(path, data):
    """Write a file whole, so a power cut leaves the old one or the new one. LittleFS renames over
    the old file in one step. Where a rename can't replace a file (FAT), the old one is removed
    first, and _read finds the new one in the .tmp file should the power go in between."""
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(data)
    import os
    try:
        os.rename(tmp, path)
    except OSError:
        try:
            os.remove(path)
        except OSError:
            pass
        os.rename(tmp, path)


def _read(path):
    """The file's text, or the one _write left in path.tmp when the power went before its rename.
    None if there's neither."""
    for p in (path, path + ".tmp"):
        try:
            with open(p) as f:
                return f.read()
        except OSError:
            pass
    return None


def _stored_language(path):
    try:
        with open(path) as f:
            return text.pick(f.read())
    except OSError:
        return None


def load_language(default=None, path=LANG):
    """Draw the screens in the stored language; with none stored (the app and the API haven't said),
    in `default` (board.json's "lang"), else English. Returns it."""
    return text.use(_stored_language(path) or text.pick(default) or text.DEFAULT)


def save_language(code, path=LANG):
    """The app or the API named the user's language: draw in it from now on, and keep it, if the
    screens have it. It then wins over board.json's. Missing or unknown keeps the language as it
    is. True if it was written."""
    code = text.pick(code)
    if code is None:
        return False
    text.use(code)
    if code == _stored_language(path):
        return False
    _write(path, code)
    log("language: %s" % code)
    return True


class Secrets:
    def __init__(self, path=SECRETS):
        self._path = path
        try:
            data = json.loads(_read(path) or "{}")
        except ValueError:
            data = {}
        self.wifi = data.get("wifi")  # {"ssid", "pw"}
        self.token = data.get("token")
        self.device_id = data.get("device_id")

    def save(self):
        _write(self._path, json.dumps({"wifi": self.wifi, "token": self.token, "device_id": self.device_id}))

    def paired(self):
        return bool(self.wifi and self.token)

    def forget(self):
        """Wi-Fi and token gone: the board needs setup again."""
        self.wifi = self.token = self.device_id = None
        self.save()



class State:
    """Kept in RTC memory across deep sleep."""

    def __init__(self):
        self._rtc = machine.RTC()
        try:
            data = json.loads(self._rtc.memory() or b"{}")
        except ValueError:
            data = {}
        self.etag = data.get("etag")  # of the frame on the panel
        self.failures = data.get("failures", 0)
        self.error = data.get("error")  # key of the error screen on the panel, if one is
        self.reported = data.get("reported")  # key of the last status report sent
        self.wakes = data.get("wakes", 0)  # wake-ups since the last power cut, for the log
        self.unchanged = data.get("unchanged", 0)  # "frame unchanged" answers in a row
        self.refused = data.get("refused", 0)  # seconds slept since the API began answering 401

    def save(self):
        self._rtc.memory(json.dumps({"etag": self.etag, "failures": self.failures, "error": self.error,
                                     "reported": self.reported, "wakes": self.wakes, "unchanged": self.unchanged,
                                     "refused": self.refused}).encode())

    def clear(self):
        self.etag = self.error = self.reported = None
        self.failures = self.unchanged = self.refused = 0
