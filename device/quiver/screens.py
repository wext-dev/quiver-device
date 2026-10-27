"""The few screens the board draws itself, because the API can't (it isn't reachable yet, or at
all): starting up, setup, the pairing code, Wi-Fi trouble, "connected", "setup stopped" and errors.
Everything else is a frame from the API.

They share one layout: the Quiver mark and a title, a red rule, the body, and a black footer bar.
While setup waits for the app, the body is the setup QR on the left (crypto.py: the app can't send
Wi-Fi or add the screen without it) with the text beside it. "Connected" ticks off the row of
steps the app shows. The start-up screen also credits the maker: "Powered by", the Wext wordmark,
the copyright and the address; the setup screen has the wordmark and address in its footer instead.
Every other footer has the wordmark on the right.
Text is Norwegian, like the app. Drawn for 400 × 300.

A red refresh takes about 15 s, so a screen is drawn only where the board waits anyway (for Wi-Fi,
or for the user), and never in front of the pairing code.
"""

from . import FIRMWARE, logo, qr
from .canvas import BLACK, RED, WHITE, font

W, H = 400, 300
FOOT = 48
# The setup QR: 4 px modules, 116 px for the setup secret's version 3, with a quiet zone of at least
# 4 modules to the edge, the rule and the footer. The text column starts right of it.
QR_X, QR_Y, QR_SCALE = 16, 100, 4
COL = 152
STEPS = ("Oppsett", "Wi-Fi", "Verifisering", "Ferdig")
# Who makes the screen: the start-up screen's credit and the setup screen's footer.
MAKER = "© 2026 Wext AS · https://wext.as"
SITE = "wext.as"


def _head(c, title, sub):
    c.bitmap(logo.MARK, logo.MARK_W, logo.MARK_H, 16, 16)
    c.text((96, 44), title, font(24, True), anchor="ls")
    c.text((96, 66), c.fit(sub, font(15), W - 112), font(15), anchor="ls")
    c.line(((16, 84), (W - 16, 84)), RED, width=2)


def _foot(c, line1, line2=None):
    """The footer bar: what's happening on the left, the maker's wordmark on the right, in white."""
    c.rect((0, H - FOOT, W - 1, H - 1), BLACK)
    c.bitmap(logo.WEXT, logo.WEXT_W, logo.WEXT_H, W - 16 - logo.WEXT_W, H - FOOT + (FOOT - logo.WEXT_H) // 2, color=WHITE)
    room = W - 48 - logo.WEXT_W
    c.text((16, H - FOOT + (18 if line2 else 24)), c.fit(line1, font(15, True), room), font(15, True), WHITE, anchor="lm")
    if line2:
        c.text((16, H - 13), c.fit(line2, font(12), room), font(12), WHITE, anchor="lm")


def _foot_maker(c):
    """The footer bar with nothing to say: the maker's wordmark left and address right, in white."""
    c.rect((0, H - FOOT, W - 1, H - 1), BLACK)
    c.bitmap(logo.WEXT, logo.WEXT_W, logo.WEXT_H, 16, H - FOOT + (FOOT - logo.WEXT_H) // 2, color=WHITE)
    c.text((W - 16, H - FOOT // 2), SITE, font(15), WHITE, anchor="rm")


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


def _credit(c, x, y, centre=False):
    """"Powered by" and the Wext wordmark on baseline y, the copyright and address below it.
    Starts at x, or is centred on it."""
    f = font(12)
    lead = c.width_of("Powered by", f) + 5
    left = x - (lead + logo.WEXT_W) // 2 if centre else x
    c.text((left, y), "Powered by", f, anchor="ls")
    c.bitmap(logo.WEXT, logo.WEXT_W, logo.WEXT_H, int(left + lead), y - logo.WEXT_H + 1)
    c.text((x, y + 17), MAKER, f, anchor="ms" if centre else "ls")


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
    c.bitmap(logo.LOGO, logo.LOGO_W, logo.LOGO_H, (W - logo.LOGO_W) // 2, 18)
    c.line(((W // 2 - 24, 132), (W // 2 + 24, 132)), RED, width=2)
    c.text((W // 2, 162), "Starter", font(24, True), anchor="ms")
    sub = "Kobler til %s" % ssid if ssid else "Kobler til Wi-Fi"
    c.text((W // 2, 184), c.fit(sub, font(15), W - 32), font(15), anchor="ms")
    _credit(c, W // 2, 224, centre=True)
    _foot(c, "Henter bildet fra Quiver", "Firmware " + FIRMWARE)


def setup(c, qr_text):
    """The same whatever started setup (new, button, removed …): the QR and the steps."""
    _head(c, "Sett opp skjermen", "Skann QR-koden med Quiver-appen")
    _qr(c, qr_text)
    num, text = font(15, True), font(15)
    steps = ("Åpne Quiver og trykk «Legg til enhet»", "Skann QR-koden", "Velg Wi-Fi-nettverket ditt")
    y = 118
    for i, s in enumerate(steps):
        c.text((COL, y), str(i + 1), num, RED, anchor="ls")
        for line in c.wrap(s, text, W - 16 - COL - 20, 2):
            c.text((COL + 20, y), line, text, anchor="ls")
            y += 20
        y += 8
    _foot_maker(c)


def code(c, qr_text, user_code, name, minutes=15):
    """Registered and waiting to be added to a home: the QR again, and the code to type instead."""
    _head(c, "Legg til skjermen", "Skann QR-koden med Quiver-appen")
    _qr(c, qr_text)
    mid = (COL + W - 16) // 2
    c.text((mid, 124), "Eller skriv inn koden", font(12), anchor="ms")
    c.text((mid, 158), user_code, font(40, mono=True), anchor="mm")
    c.line(((mid - 60, 186), (mid + 60, 186)), RED, width=2)
    c.text((mid, 208), c.fit("Over Bluetooth trengs ingen kode.", font(12), W - 16 - COL), font(12), anchor="ms")
    _foot(c, "Gyldig i %d min" % minutes, name)


_WIFI = {
    "auth": "Feil passord. Skriv det inn på nytt i appen.",
    "no_ap": "Fant ikke nettverket. Skjermen må være innen rekkevidde, og bare 2,4 GHz virker.",
    "dhcp": "Nettverket ga ingen adresse. Prøv igjen, eller velg et annet nettverk.",
}


def wifi_failed(c, qr_text, name, ssid, err):
    """Joining the network the app sent failed; the app shows the same over Bluetooth. The QR stays,
    so the app can start over even if it was closed."""
    _head(c, "Fikk ikke koblet til", "Wi-Fi «%s»" % ssid)
    _qr(c, qr_text)
    _column(c, _WIFI.get(err, _WIFI["dhcp"]), 118, lines=5)
    _foot(c, "Velg nettverk på nytt i appen", name)


def connected(c, name):
    """Claimed in the app: the first frame is on its way."""
    _head(c, "Koblet til", "Skjermen er lagt til i hjemmet ditt")
    _body(c, "Henter det første bildet. Det kommer om et øyeblikk.", y=114, lines=2)
    _steps(c, len(STEPS))
    _foot(c, "Henter første bilde …", name)


def stopped(c, name, has_button=True):
    _head(c, "Oppsett stoppet", "For å spare strøm")
    _body(c, "Hold knappen inne i 5 sekunder for å starte oppsettet igjen." if has_button
          else "Trykk RESET på kortet for å starte oppsettet igjen.")
    _foot(c, "Skjermen sover til da", name)


def describe(e):
    """(title, detail) for an exception, in words for the person in front of the panel."""
    kind = type(e).__name__
    if kind == "WifiError":
        return "Ingen Wi-Fi", str(e) + ". Hold knappen inne i 5 sekunder for å velge et annet nettverk."
    if kind == "HttpError":
        return "Får ikke kontakt", "Skjermen er på Wi-Fi, men når ikke Quiver. Sjekk internett på nettverket."
    if kind == "Unauthorized":
        return "Skjermen ble avvist", "Quiver kjenner ikke igjen skjermen. Hold knappen inne i 5 sekunder for å sette den opp på nytt."
    if kind == "ApiError":
        return "Quiver svarer ikke", "Tjenesten svarte %s." % e
    if kind == "FrameError":
        return "Feil i bildet", str(e)
    if kind == "EPDError":
        return "Skjermfeil", str(e)
    if kind == "MemoryError":
        return "Tom for minne", "Kortet har for lite minne til bildet."
    return "Noe gikk galt", "%s: %s" % (kind, e)


def fmt_wait(seconds):
    minutes = max(1, (seconds + 30) // 60)
    if minutes < 90:
        return "%d min" % minutes
    return "%d t %d min" % (minutes // 60, minutes % 60) if minutes % 60 else "%d t" % (minutes // 60)


def error(c, title, detail, retry_seconds, failures):
    _head(c, title, "Bildet oppdateres ikke akkurat nå")
    _body(c, detail)
    _foot(c, "Prøver igjen om %s" % fmt_wait(retry_seconds), "Forsøk nr. %d på rad som feilet" % failures)
