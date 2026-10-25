"""Which copy of the app runs, and whether a new one has proven itself (docs/ota.md#on-the-board).

The app lives in one of two slots, slots/a and slots/b, next to the copy that came with the board
(frozen into the Quiver runtime, or copied to /quiver by hand). An update is written to the slot
that isn't running and starts on trial: the next starts run it, and the first time the app reaches
the API it commits (commit()). If it hasn't after MAX_TRIES starts, the loader goes back to the
slot that worked. A runtime update is on trial the same way, with ESP-IDF's rollback underneath:
if the new runtime isn't confirmed in its first start, the chip starts the old one again.

boot.json (flash, survives power cuts) holds the state:

    {"slot": "a", "version": "3.1.0", "security": 1, "build": "official",
     "pending": {"slot": "b", "version": "3.2.0", "security": 1, "tries": 1},
     "runtime": {"version": "1.1.0", "from": "1.0.0"},
     "results": [{"kind": "app", "to": "3.2.0", "result": "ok", "detail": null}]}

It is written only when something changes, never on an ordinary wake.
"""

import json
import os
import sys

from . import RUNTIME

STATE = "boot.json"
SLOTS = "slots"
MAX_TRIES = 5
# A new app or runtime that hangs instead of crashing is reset by the watchdog. Longer than the
# longest thing a wake does (setup over Bluetooth gives up after 10 minutes).
WATCHDOG_MS = 30 * 60 * 1000

_app_trial = False
_runtime_trial = False
_slot = None
_watchdog = None


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    try:
        os.remove(STATE)
    except OSError:
        pass
    os.rename(tmp, STATE)


def slot_dir(name):
    return SLOTS + "/" + name


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


def is_dir(path):
    try:
        return os.stat(path)[0] & 0x4000 != 0
    except OSError:
        return False


def remove_tree(path):
    if not exists(path):
        return
    if is_dir(path):
        for name in os.listdir(path):
            remove_tree(path + "/" + name)
        os.rmdir(path)
    else:
        os.remove(path)


def make_dirs(path):
    part = ""
    for name in path.split("/"):
        part = name if not part else part + "/" + name
        if not is_dir(part):
            os.mkdir(part)


def _result(s, kind, version, result, detail):
    s.setdefault("results", []).append({"kind": kind, "to": version, "result": result, "detail": detail})
    del s["results"][:-4]


def _has_app(slot):
    return exists(slot_dir(slot) + "/quiver/__init__.py") or exists(slot_dir(slot) + "/quiver/__init__.mpy")


def boot():
    """At every start, before the app is imported: pick the slot, count a trial start, put the
    slot first on sys.path. Returns the slot, or None for the app the board came with."""
    global _app_trial, _runtime_trial, _slot, _watchdog
    s = load()
    changed = False
    _app_trial = _runtime_trial = False

    p = s.get("pending")
    if p:
        p["tries"] = p.get("tries", 0) + 1
        changed = True
        if p["tries"] > MAX_TRIES or not _has_app(p["slot"]):
            _result(s, "app", p["version"], "rolled_back", "the API wasn't reached in %d starts" % MAX_TRIES)
            remove_tree(slot_dir(p["slot"]))
            del s["pending"]
        else:
            _app_trial = True

    rt = s.get("runtime")
    if rt:
        if RUNTIME == rt["version"]:
            _runtime_trial = True
        else:  # ESP-IDF went back to the runtime that worked
            _result(s, "runtime", rt["version"], "rolled_back", "the new runtime wasn't confirmed in its first start")
            del s["runtime"]
            changed = True

    _slot = s["pending"]["slot"] if _app_trial else s.get("slot")
    if _slot and not _has_app(_slot):
        _slot = None
    if _slot:
        sys.path.insert(0, slot_dir(_slot))

    # Checked at power-on (files may have been changed over USB) and when another slot starts.
    if _power_on() or s.get("build_slot") != _slot:
        build = _build(_slot)
        if (build, _slot) != (s.get("build"), s.get("build_slot")):
            s["build"], s["build_slot"] = build, _slot
            changed = True
    if changed:
        save(s)
    if _app_trial or _runtime_trial:
        try:
            import machine
            _watchdog = machine.WDT(timeout=WATCHDOG_MS)
        except (ImportError, AttributeError, ValueError):
            pass
    return _slot


def trial():
    """True while a new app or runtime hasn't yet reached the API."""
    return _app_trial or _runtime_trial


def commit():
    """The app reached the API: keep what is on trial. Must happen in the same start as the
    runtime's first, or ESP-IDF rolls it back at the next one (which is what we want if it can't)."""
    global _app_trial, _runtime_trial
    if not trial():
        return
    s = load()
    if _app_trial and s.get("pending"):
        p = s.pop("pending")
        s["slot"], s["version"] = p["slot"], p["version"]
        s["security"] = max(s.get("security", 0), p.get("security", 0))
        _result(s, "app", p["version"], "ok", None)
    if _runtime_trial and s.get("runtime"):
        rt = s.pop("runtime")
        try:
            import esp32
            esp32.Partition.mark_app_valid_cancel_rollback()
        except (ImportError, OSError, AttributeError):
            pass
        _result(s, "runtime", rt["version"], "ok", None)
    save(s)
    _app_trial = _runtime_trial = False


def take_results():
    """Outcomes of updates since the last call, for the app to report: [{"kind", "to", "result", "detail"}]."""
    s = load()
    results = s.pop("results", None)
    if results:
        save(s)
    return results or []


def free_slot():
    """The slot an update goes into: not the one running."""
    return "b" if load().get("slot") == "a" else "a"


def stage(slot, version, security):
    s = load()
    s["pending"] = {"slot": slot, "version": version, "security": security, "tries": 0}
    save(s)


def stage_runtime(version):
    s = load()
    s["runtime"] = {"version": version, "from": RUNTIME}
    save(s)


def security():
    """The lowest security level a release may have: the highest one installed."""
    return load().get("security", 0)


def slot():
    return _slot


def build():
    """"official" when the app running is a release (or the frozen one), "custom" when it was
    installed or changed by hand. Checked at power-on."""
    return load().get("build") or _build(_slot)


def _power_on():
    try:
        import machine
        return machine.reset_cause() != machine.DEEPSLEEP_RESET
    except (ImportError, AttributeError):
        return True


def _build(slot):
    if slot:
        try:
            import binascii
            import hashlib
            with open(slot_dir(slot) + "/.files") as f:
                files = json.load(f)
            for path, digest in files.items():
                h = hashlib.sha256()
                with open(slot_dir(slot) + "/" + path, "rb") as f:
                    while True:
                        chunk = f.read(2048)
                        if not chunk:
                            break
                        h.update(chunk)
                if binascii.hexlify(h.digest()).decode() != digest:
                    return "custom"
            return "official"
        except (OSError, ValueError):
            return "custom"
    try:
        import quiver
        # Frozen modules have a __file__ ("quiver/__init__.py") that isn't on the filesystem.
        path = getattr(quiver, "__file__", "")
        return "official" if path.startswith(".frozen") or not exists(path) else "custom"
    except ImportError:
        return "custom"
