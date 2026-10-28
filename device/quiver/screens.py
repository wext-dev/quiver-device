"""The few screens the board draws itself, because the API can't (it isn't reachable yet, or at
all): starting up, setup, the pairing code, Wi-Fi trouble, "connected", "setup stopped" and errors.
Everything else is a frame from the API.

They share one layout: a title, a red rule, the body, and a black footer bar with the Quiver mark
and the status on the left and the maker's (Wext) wordmark on the right. While setup waits for the
app, the body is the setup QR on the left (crypto.py: the app can't send Wi-Fi or add the screen
without it) with the text beside it, and the footer has the address instead of a status.
"Connected" ticks off the row of steps the app shows. The start-up screen has the whole logo above
instead of a title, and no mark in its footer. The setup screen has the firmware and runtime
versions in small type above the footer, for support: it is the first thing a new board shows.
Text is English or Norwegian (text.py): the language of the app that set the screen up, then of
the user who owns it, as the API says with every frame (store.py keeps it); before either,
board.json's "lang", or English. Drawn for 400 × 300.

A red refresh takes about 15 s, so a screen is drawn only where the board waits anyway (for Wi-Fi,
or for the user), and never in front of the pairing code.
"""

from . import FIRMWARE, logo, qr
from .canvas import BLACK, RED, WHITE, font
from .text import t

W, H = 400, 300
FOOT = 48
# The setup QR: 4 px modules, 116 px for the setup secret's version 3, with a quiet zone of at least
# 4 modules to the edge, the rule and the footer. The text column starts right of it.
QR_X, QR_Y, QR_SCALE = 16, 100, 4
COL = 152
STEPS = ("step_setup", "step_wifi", "step_verify", "step_done")  # keys in text.py
# Who makes the screen: the address in the setup screen's footer.
SITE = "wext.as"


def _head(c, title, sub):
    c.text((16, 44), c.fit(title, font(24, True), W - 32), font(24, True), anchor="ls")
    c.text((16, 66), c.fit(sub, font(15), W - 32), font(15), anchor="ls")
    c.line(((16, 84), (W - 16, 84)), RED, width=2)


def _foot(c, line1, line2=None, mark=True):
    """The footer bar, in white: the Quiver mark and what's happening on the left, the maker's
    wordmark on the right. The start-up screen has the whole logo above, so no mark there."""
    c.rect((0, H - FOOT, W - 1, H - 1), BLACK)
    x = 16
    if mark:
        c.bitmap(logo.MARK, logo.MARK_W, logo.MARK_H, x, H - FOOT + (FOOT - logo.MARK_H) // 2, color=WHITE)
        x += logo.MARK_W + 12
    c.bitmap(logo.WEXT, logo.WEXT_W, logo.WEXT_H, W - 16 - logo.WEXT_W, H - FOOT + (FOOT - logo.WEXT_H) // 2, color=WHITE)
    room = W - 32 - x - logo.WEXT_W
    c.text((x, H - FOOT + (18 if line2 else 24)), c.fit(line1, font(15, True), room), font(15, True), WHITE, anchor="lm")
    if line2:
        c.text((x, H - 13), c.fit(line2, font(12), room), font(12), WHITE, anchor="lm")


def _foot_maker(c):
    """The footer bar with nothing to say: the Quiver mark and the maker's wordmark left, the
    maker's address right, in white."""
    c.rect((0, H - FOOT, W - 1, H - 1), BLACK)
    c.bitmap(logo.MARK, logo.MARK_W, logo.MARK_H, 16, H - FOOT + (FOOT - logo.MARK_H) // 2, color=WHITE)
    c.bitmap(logo.WEXT, logo.WEXT_W, logo.WEXT_H, 16 + logo.MARK_W + 12, H - FOOT + (FOOT - logo.WEXT_H) // 2, color=WHITE)
    c.text((W - 16, H - FOOT // 2), SITE, font(15), WHITE, anchor="rm")


def versions():
    """"Firmware 3.6.0 · runtime 1.0.3", or "runtime stock" on stock MicroPython."""
    try:
        from quiver_boot import RUNTIME
    except ImportError:
        RUNTIME = None
    return t("versions", FIRMWARE, RUNTIME or "stock")


def _body(c, text, y=116, lines=4):
    f = font(15)
    for line in c.wrap(text, f, W - 32, lines):
        c.text((16, y), line, f, anchor="ls")
        y += 23
    return y


def _qr(c, text):
    """The QR for text at the left of the body. Returns its size in px."""
    m = qr.encode(text)
    for row, bits in enumerate(m):
        y = QR_Y + row * QR_SCALE
        x = 0
        while x < len(bits):
            if not bits[x]:
                x += 1
                continue
            end = x
            while end < len(bits) and bits[end]:
                end += 1
            c.rect((QR_X + x * QR_SCALE, y, QR_X + end * QR_SCALE - 1, y + QR_SCALE - 1), BLACK)
            x = end
    return len(m) * QR_SCALE


def _column(c, text, y, f=None, lines=4):
    """Wrapped text in the column beside the QR. Returns the y below it."""
    f = f or font(15)
    for line in c.wrap(text, f, W - 16 - COL, lines):
        c.text((COL, y), line, f, anchor="ls")
        y += 20
    return y


def _check(c, x, y, color=WHITE):
    for d in (0, 1):
        c.line(((x - 4, y + d), (x - 1, y + 3 + d)), color)
        c.line(((x - 1, y + 3 + d), (x + 4, y - 2 + d)), color)


def _steps(c, at, y=206, failed=False):
    """The setup steps in a row: done ones ticked, step `at` in red (crossed if it failed), the
    rest open. at = len(STEPS) ticks them all."""
    r, xs = 9, [40 + i * 320 // (len(STEPS) - 1) for i in range(len(STEPS))]
    for i in range(len(xs) - 1):
        if i < at:
            c.line(((xs[i] + r, y), (xs[i + 1] - r, y)), BLACK, width=2)
        else:
            for x in range(xs[i] + r + 2, xs[i + 1] - r - 1, 6):
                c.line(((x, y), (x + 2, y)), BLACK, width=2)
    for i, (x, label) in enumerate(zip(xs, STEPS)):
        label = t(label)
        if i < at:
            c.disc(x, y, r, BLACK)
            _check(c, x, y - 1)
        elif i == at:
            c.disc(x, y, r, RED)
            if failed:
                c.line(((x - 3, y - 3), (x + 3, y + 3)), WHITE)
                c.line(((x - 3, y + 3), (x + 3, y - 3)), WHITE)
            else:
                c.disc(x, y, 3, WHITE)
        else:
            c.disc(x, y, r, BLACK)
            c.disc(x, y, r - 2, WHITE)
        f = font(12)
        c.text((x, y + 26), label, f, RED if i == at else BLACK, anchor="ms")


def starting(c, ssid=None):
    """After a power cut, while it connects and fetches the first frame."""
    c.bitmap(logo.LOGO, logo.LOGO_W, logo.LOGO_H, (W - logo.LOGO_W) // 2, 40)
    c.line(((W // 2 - 24, 152), (W // 2 + 24, 152)), RED, width=2)
    c.text((W // 2, 188), t("starting"), font(24, True), anchor="ms")
    sub = t("connecting_to", ssid) if ssid else t("connecting_wifi")
    c.text((W // 2, 212), c.fit(sub, font(15), W - 32), font(15), anchor="ms")
    _foot(c, t("fetching"), t("firmware", FIRMWARE), mark=False)


def setup(c, qr_text):
    """The same whatever started setup (new, button, removed …): the QR and the steps."""
    _head(c, t("setup_title"), t("scan_qr"))
    _qr(c, qr_text)
    num, text = font(15, True), font(15)
    y = 118
    for i, key in enumerate(("setup_1", "setup_2", "setup_3")):
        c.text((COL, y), str(i + 1), num, RED, anchor="ls")
        for line in c.wrap(t(key), text, W - 16 - COL - 20, 2):
            c.text((COL + 20, y), line, text, anchor="ls")
            y += 20
        y += 8
    # Under the QR's quiet zone (it ends at y 232), just above the footer.
    c.text((16, H - FOOT - 7), versions(), font(12), anchor="ls")
    _foot_maker(c)


def code(c, qr_text, user_code, name, minutes=15):
    """Registered and waiting to be added to a home: the QR again, and the code to type instead."""
    _head(c, t("code_title"), t("scan_qr"))
    _qr(c, qr_text)
    mid = (COL + W - 16) // 2
    c.text((mid, 124), t("code_or"), font(12), anchor="ms")
    c.text((mid, 158), user_code, font(40, mono=True), anchor="mm")
    c.line(((mid - 60, 186), (mid + 60, 186)), RED, width=2)
    c.text((mid, 208), c.fit(t("code_ble"), font(12), W - 16 - COL), font(12), anchor="ms")
    _foot(c, t("code_valid", minutes), name)


def wifi_failed(c, qr_text, name, ssid, err):
    """Joining the network the app sent failed; the app shows the same over Bluetooth. The QR stays,
    so the app can start over even if it was closed."""
    _head(c, t("wifi_title"), t("wifi_ssid", ssid))
    _qr(c, qr_text)
    _column(c, t("wifi_" + err if err in ("auth", "no_ap") else "wifi_dhcp"), 118, lines=5)
    _foot(c, t("wifi_again"), name)


def connected(c, name):
    """Claimed in the app: the first frame is on its way."""
    _head(c, t("connected_title"), t("connected_sub"))
    _body(c, t("connected_body"), y=114, lines=2)
    _steps(c, len(STEPS))
    _foot(c, t("connected_foot"), name)


def stopped(c, name, has_button=True):
    _head(c, t("stopped_title"), t("stopped_sub"))
    _body(c, t("stopped_button" if has_button else "stopped_reset"))
    _foot(c, t("stopped_foot"), name)


def describe(e):
    """(title, detail) for an exception, in words for the person in front of the panel."""
    kind = type(e).__name__
    if kind == "WifiError":
        reason = getattr(e, "reason", None)
        return t("err_wifi"), t("err_wifi_" + reason if reason in ("auth", "no_ap") else "err_wifi_dhcp", getattr(e, "ssid", ""))
    if kind == "HttpError":
        return t("err_http"), t("err_http_detail")
    if kind == "Unauthorized":
        return t("err_refused"), t("err_refused_detail")
    if kind == "ApiError":
        return t("err_api"), t("err_api_detail", str(e))
    if kind == "FrameError":
        return t("err_frame"), str(e)
    if kind == "EPDError":
        return t("err_panel"), str(e)
    if kind == "MemoryError":
        return t("err_memory"), t("err_memory_detail")
    return t("err_other"), "%s: %s" % (kind, e)


def fmt_wait(seconds):
    minutes = max(1, (seconds + 30) // 60)
    if minutes < 90:
        return t("wait_min", minutes)
    return t("wait_h_min", minutes // 60, minutes % 60) if minutes % 60 else t("wait_h", minutes // 60)


def error(c, title, detail, retry_seconds, failures):
    _head(c, title, t("error_sub"))
    _body(c, detail)
    _foot(c, t("error_retry", fmt_wait(retry_seconds)), t("error_count", failures))
