"""One wake-up.

  1. A wake by a button: the button held 5 s means setup again; otherwise the press is sent with the
     request in step 4 (button.py). After a power cut, a start-up screen while it connects.
  2. Not set up (no Wi-Fi or token): setup mode (setup.py), then on to step 3 once claimed.
  3. Join Wi-Fi. Report status if something changed since the last report (firmware, panel,
     features, software), or after any start other than from deep sleep.
  4. GET /device/v1/frame with the ETag on screen, telemetry (X-Telemetry) and, after a press,
     X-Button (next, page-down or page-up). The API keeps the view and page the screen is on.
     The answer confirms an update on trial (the loader commits it), then the events queued since
     the last good wake are sent, and the log if the answer asks for it (X-Log-Request). Its
     X-Language (the owner's language, on 200 and 304) is kept for the board's own screens.
  5. Wi-Fi off. On 200, draw the frame; on 304 leave the panel alone. If the answer offered an
     update (X-Update), fetch and install it (update.py) and start it straight away: an app after
     a second's sleep, a runtime with a reset. Otherwise sleep X-Poll-Seconds.

A failure keeps the picture on the panel and retries sooner, doubling the wait each time. After
errors_after failures in a row an error screen says what's wrong; straight away after a power cut or
RESET, where the start-up screen would otherwise stay up and claim it's still connecting.

A 410 device_released means the screen was removed in the app or its account was deleted: the board
forgets its Wi-Fi and token and starts setup from scratch, over Bluetooth. A 401 (the API doesn't
know the token) is only a failure like the others, since a restored database or a bad deploy looks
the same. Once it has gone on for token_refused_hours, the token is dropped and setup starts with the
Wi-Fi kept, so a code shows on the panel straight away.
"""

import binascii
import gc
import json
import time

import machine

from . import FIRMWARE, board, ca as ca_roots, clock, frame, outbox, screens, setup, update as updater, wifi
from .api import Api, Released, Unauthorized
from .button import Buttons
from .http import HttpError
from .log import LOG, exception, log
from .panel import Panel
from .store import Secrets, State, load_language, save_language

DEFAULT_POLL = 900
# After this many "unchanged" answers in a row, ask for the frame anyway and draw it again. If a
# refresh ever failed without an error, the panel shows something other than the ETag says, and
# only a redraw puts that right.
REDRAW_AFTER = 12
# Returned as the seconds to sleep when a new runtime was installed: wake.py resets the board
# instead, since a wake from deep sleep starts the runtime that ran before (docs/ota.md).
RESTART = -1


def _ca(cfg):
    """The CA certificates TLS is checked against: board.json's ca_file, else Google's roots
    (ca.py). None for plain HTTP to a local API."""
    if cfg["ca_file"]:
        with open(cfg["ca_file"], "rb") as f:
            return [f.read()]
    return ca_roots.roots() if cfg["api_url"].startswith("https://") else None


def retry_after(cfg, failures):
    """retry_minutes, doubling with each failure in a row, up to max_retry_minutes."""
    minutes = cfg["retry_minutes"] * (2 ** min(failures - 1, 8))
    return int(min(minutes, cfg["max_retry_minutes"]) * 60)


def _reset():
    """Why the board started, as the API wants it."""
    names = {getattr(machine, n, None): w for n, w in (("DEEPSLEEP_RESET", "deepsleep"), ("PWRON_RESET", "power_on"),
                                                        ("HARD_RESET", "hard_reset"), ("SOFT_RESET", "soft_reset"), ("WDT_RESET", "watchdog"))}
    return names.get(machine.reset_cause(), "other")


def software(cfg):
    """The software part of the status report: model, runtime, loader, update mode and keys, and
    whether the app is a release ("official") or changed by hand ("custom")."""
    try:
        from quiver_boot import BOOT, RUNTIME, loader, ota
    except ImportError:  # stock MicroPython without the loader: no updates
        return {"model": board.model(cfg), "boot": "", "ota": {"mode": "off", "keys": []}, "build": "custom", "reset": _reset()}
    return {"model": board.model(cfg), "runtime": RUNTIME or "", "boot": BOOT, "build": loader.build(), "reset": _reset(),
            "ota": {"mode": ota.mode(cfg), "keys": sorted(ota.trusted_keys(cfg))}}


def telemetry(cfg, state, mv, rssi):
    """X-Telemetry: battery and signal, and what helps find trouble. The API keeps any keys."""
    t = {"bat_mv": mv, "rssi": rssi, "heap": gc.mem_free() if hasattr(gc, "mem_free") else None,
         "wakes": state.wakes, "fails": state.failures, "awake_ms": time.ticks_ms()}
    try:
        from quiver_boot import loader
        t["slot"] = loader.slot() or "base"
    except ImportError:
        pass
    return t


def _report_key(cfg):
    sw = software(cfg)
    sw.pop("reset")
    # The chip doesn't change without a new flash (which changes the firmware), and the heap may
    # differ a little from boot to boot, so only the wiring is part of the key.
    return binascii.crc32(json.dumps([FIRMWARE, board.panel_report(cfg), board.features(cfg), sw, board.wiring(cfg)]).encode())


def _commit():
    """The API answered: a new app or runtime on trial has proven itself."""
    try:
        from quiver_boot import loader
    except ImportError:
        return
    if loader.trial():
        loader.commit()
        log("update confirmed")


def _send_outbox(api):
    """Errors queued since the last good wake, and how updates went."""
    events = outbox.take()
    try:
        from quiver_boot import loader
        for r in loader.take_results():
            detail = ", " + r["detail"] if r.get("detail") else ""
            events.append({"type": "update", "msg": "%s %s: %s%s" % (r["kind"], r["to"], r["result"], detail),
                           "data": {"kind": r["kind"], "to": r["to"], "result": r["result"]}})
    except ImportError:
        pass
    if not events:
        return
    try:
        api.events(events)
    except Exception as e:  # try again next time
        outbox.put_back(events)
        exception("events", e)


def log_tail(limit=8 * 1024):
    """The end of the log (log.txt.old, then log.txt), for X-Log-Request."""
    text = ""
    for name in (LOG + ".old", LOG):
        try:
            with open(name) as f:
                text += f.read()
        except OSError:
            pass
    return text[-limit:]


def _tag(etag):
    """An ETag, short, for the log: its first eight characters, enough to match the API's log."""
    return etag.strip('"')[:8] if etag else "none"


def update(cfg, secrets, state, panel, ca, press):
    """Steps 3-5. `press` is the X-Button value, or None. Returns seconds until the next wake."""
    wlan = wifi.connect(secrets.wifi["ssid"], secrets.wifi["pw"], cfg["wifi_timeout_s"])
    try:
        if ca:
            clock.sync()
        api = Api(cfg["api_url"], secrets.token, ca)
        mv, rssi = board.battery_mv(cfg), wifi.rssi(wlan)
        key = _report_key(cfg)
        if state.reported != key:
            api.status(board.panel_report(cfg), board.features(cfg), mv, rssi, secrets.wifi["ssid"], software(cfg), board.hardware(cfg))
            state.reported = key
        etag = state.etag if state.unchanged < REDRAW_AFTER else None
        t = telemetry(cfg, state, mv, rssi)
        try:
            r = api.frame(etag, t, button=press, features=board.features(cfg))
        except HttpError:
            if not press:
                raise
            # The API may already have moved on: a missed press beats skipping a view.
            r = api.frame(etag, t, features=board.features(cfg))
        save_language(r.header("x-language"))  # the owner's, for the board's own screens
        _commit()
        _send_outbox(api)
        if r.header("x-log-request"):
            try:
                api.log(log_tail())
                log("log sent")
            except Exception as e:
                exception("log", e)
    finally:
        wifi.off()
    gc.collect()

    poll = int(r.header("x-poll-seconds") or DEFAULT_POLL)
    if r.status == 304:
        state.unchanged += 1
        log("frame unchanged (%s)" % _tag(etag))
    else:
        size = len(r.body)
        f = frame.parse(r.body, panel.width, panel.height)
        machine.freq(80000000)  # nothing left but waiting for the panel
        panel.show_frame(f)
        state.etag = r.header("etag")
        state.error = None
        state.unchanged = 0
        # Which ETag was sent says why it was drawn: none (forced or forgotten), or the API's changed.
        log("frame drawn (%d bytes%s, refresh %s ms, %s -> %s)" % (size, ", grey" if f.gray else "", panel.last_refresh_ms,
                                                                   _tag(etag), _tag(state.etag)))
    if r.header("x-update") == "1" and not press:
        r = None
        gc.collect()
        try:
            installed = updater.run(cfg, secrets, ca, mv)
            if installed:  # start the new version straight away
                return RESTART if installed == "runtime" else 1
        except Exception as e:  # the picture is up; try again another time
            exception("update", e)
    return poll


def show_error(cfg, state, panel, e, retry_seconds, now=False):
    """Only after errors_after failures in a row (or `now`), and not again for the same error."""
    if (state.failures < cfg["errors_after"] and not now) or panel.failed:
        return
    title, detail = screens.describe(e)
    key = binascii.crc32((title + detail).encode())
    if key == state.error:
        return
    try:
        canvas = panel.new_canvas()
        screens.error(canvas, title, detail, retry_seconds, state.failures)
        panel.show_canvas(canvas)
        state.error = key
        state.etag = None  # the next good frame must be drawn, even if it's unchanged
    except Exception as e2:
        exception("error screen", e2)


def failed(cfg, state, panel, e, started):
    """Keep the picture and try again sooner, doubling the wait each time. Returns that wait. The
    error goes in the outbox, so the API hears about it once the board gets through."""
    state.failures += 1
    seconds = retry_after(cfg, state.failures)
    exception("update failed (%d in a row)" % state.failures, e)
    try:
        title, detail = screens.describe(e)
        outbox.add("error", "%s: %s (%s: %s, %d in a row)" % (title, detail, type(e).__name__, str(e)[:80], state.failures))
    except Exception as e2:
        exception("outbox", e2)
    show_error(cfg, state, panel, e, seconds, now=started)
    return seconds


def starting(secrets, state, panel):
    """After a power cut: show that the board is alive while Wi-Fi and the first frame take their
    time. The refresh runs on while it connects; the frame waits for it."""
    try:
        canvas = panel.new_canvas()
        screens.starting(canvas, secrets.wifi["ssid"])
        panel.show_canvas(canvas, wait=False)
        state.etag = None  # the frame must be drawn over it, even if it's unchanged
    except Exception as e:
        exception("start-up screen", e)


def _cause():
    """Why the board is awake, in words for the log."""
    names = {getattr(machine, n, None): w for n, w in (("DEEPSLEEP_RESET", "timer or button"), ("PWRON_RESET", "power on or RESET"),
                                                        ("HARD_RESET", "RESET"), ("SOFT_RESET", "soft reset"), ("WDT_RESET", "watchdog"))}
    return names.get(machine.reset_cause(), "reset cause %s" % machine.reset_cause())


def run(early=None):
    """One wake-up; `early` is button.snapshot() from main.py. Returns (seconds to sleep, or 0 for
    "until the button", or RESTART; stay awake?)."""
    cfg = board.load()
    secrets, state, panel, buttons = Secrets(), State(), Panel(cfg), Buttons(cfg)
    load_language(cfg["lang"])
    ca = _ca(cfg)
    # The log's clock starts again at every wake, so number the wakes (RTC memory keeps the count).
    state.wakes += 1
    log("wake %d: %s, firmware %s" % (state.wakes, _cause(), FIRMWARE))
    press = buttons.action(early)
    reason = "new"
    seconds = DEFAULT_POLL
    started = False  # the start-up screen is on the panel
    try:
        if press == "setup":
            log("button held: setup again")
            secrets.forget()
            state.clear()
            reason, press = "button", None
        elif press:
            log("button: %s" % press)
        if machine.reset_cause() != machine.DEEPSLEEP_RESET:
            state.reported = None  # report again after any start but a wake, with why it started
        if machine.reset_cause() == machine.PWRON_RESET:
            if secrets.paired():
                starting(secrets, state, panel)
                started = True

        if not secrets.paired():
            if setup.run(cfg, secrets, state, panel, ca, reason) != "claimed":
                return 0, cfg["stay_awake"]
            press = None
        try:
            seconds = update(cfg, secrets, state, panel, ca, press)
            state.failures = state.refused = 0
        except Released as e:
            log("released (%s): forgetting Wi-Fi and token" % e.reason)
            secrets.forget()
            state.clear()
            if setup.run(cfg, secrets, state, panel, ca, e.reason) != "claimed":
                return 0, cfg["stay_awake"]
            seconds = update(cfg, secrets, state, panel, ca, None)
        except Unauthorized as e:
            if state.refused < cfg["token_refused_hours"] * 3600:
                seconds = failed(cfg, state, panel, e, started)
                state.refused += seconds
            else:
                log("token refused for %d h: setup again" % (state.refused // 3600))
                secrets.token = None
                secrets.save()
                state.clear()
                if setup.run(cfg, secrets, state, panel, ca, "revoked") != "claimed":
                    return 0, cfg["stay_awake"]
                seconds = update(cfg, secrets, state, panel, ca, None)
        except Exception as e:  # anything: keep the picture and try again sooner
            seconds = failed(cfg, state, panel, e, started)
    finally:
        try:
            panel.sleep()
        except Exception as e:
            exception("panel sleep", e)
        state.save()
        buttons.wait_release()
    log("restarting" if seconds == RESTART else "sleeping %d min" % (seconds // 60))
    return seconds, cfg["stay_awake"]
