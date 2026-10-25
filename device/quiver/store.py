"""What the board remembers.

secrets.json on flash (survives power cuts): the Wi-Fi it was given during setup and its device
token. The token is the only credential the board ever holds. It's revocable and works for this one
screen only. The setup secret behind the QR on the setup screens is never stored (crypto.py).

RTC memory (survives deep sleep and RESET, not a power cut): the ETag of the frame on the panel,
failures in a row, how long the token has been refused, and what was last reported to the API. After a power cut the panel is simply
fetched and redrawn.
"""

import json

import machine

SECRETS = "secrets.json"


class Secrets:
    def __init__(self, path=SECRETS):
        self._path = path
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        self.wifi = data.get("wifi")  # {"ssid", "pw"}
        self.token = data.get("token")
        self.device_id = data.get("device_id")

    def save(self):
        data = {"wifi": self.wifi, "token": self.token, "device_id": self.device_id}
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        import os
        try:
            os.remove(self._path)
        except OSError:
            pass
        os.rename(tmp, self._path)

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
