"""The words on the screens the board draws itself (screens.py), in each language it has.

Every language has the same keys; t() looks a key up in the language in use, with %-formatting.
A key a language lacks falls back to English, then Norwegian; an unknown language is never put in
use. The language comes from the app (the Wi-Fi it sends during setup) and then from the API
(X-Language on frames), and is kept on flash by store.py. Until either has said anything, a board
draws in board.json's "lang", or English.

To add a language: a table here with every key (tests/test_units.py checks they match, placeholders
too), using only characters fontdata/ has (tools/make_fonts.py's CHARSET), and short enough for
400 × 300.
"""

DEFAULT = "en"  # a new board's, unless board.json says otherwise

TEXT = {
    "nb": {
        # starting()
        "starting": "Starter",
        "connecting_to": "Kobler til %s",
        "connecting_wifi": "Kobler til Wi-Fi",
        "fetching": "Henter bildet fra Quiver",
        "firmware": "Firmware %s",
        "versions": "Firmware %s · runtime %s",
        # setup() and code()
        "setup_title": "Sett opp skjermen",
        "scan_qr": "Skann QR-koden med Quiver-appen",
        "setup_1": "Åpne Quiver og trykk «Legg til enhet»",
        "setup_2": "Skann QR-koden",
        "setup_3": "Velg Wi-Fi-nettverket ditt",
        "code_title": "Legg til skjermen",
        "code_or": "Eller skriv inn koden",
        "code_ble": "Over Bluetooth trengs ingen kode.",
        "code_valid": "Gyldig i %d min",
        # wifi_failed()
        "wifi_title": "Fikk ikke koblet til",
        "wifi_ssid": "Wi-Fi «%s»",
        "wifi_auth": "Feil passord. Skriv det inn på nytt i appen.",
        "wifi_no_ap": "Fant ikke nettverket. Skjermen må være innen rekkevidde, og bare 2,4 GHz virker.",
        "wifi_dhcp": "Nettverket ga ingen adresse. Prøv igjen, eller velg et annet nettverk.",
        "wifi_again": "Velg nettverk på nytt i appen",
        # connected() and its steps
        "connected_title": "Koblet til",
        "connected_sub": "Skjermen er lagt til i hjemmet ditt",
        "connected_body": "Henter det første bildet. Det kommer om et øyeblikk.",
        "connected_foot": "Henter første bilde …",
        "step_setup": "Oppsett",
        "step_wifi": "Wi-Fi",
        "step_verify": "Verifisering",
        "step_done": "Ferdig",
        # stopped()
        "stopped_title": "Oppsett stoppet",
        "stopped_sub": "For å spare strøm",
        "stopped_button": "Hold knappen inne i 5 sekunder for å starte oppsettet igjen.",
        "stopped_reset": "Trykk RESET på kortet for å starte oppsettet igjen.",
        "stopped_foot": "Skjermen sover til da",
        # describe(): (title, detail) for each error
        "err_wifi": "Ingen Wi-Fi",
        "err_wifi_auth": "Feil passord for %s. Hold knappen inne i 5 sekunder for å velge et annet nettverk.",
        "err_wifi_no_ap": "Fant ikke nettverket %s. Hold knappen inne i 5 sekunder for å velge et annet nettverk.",
        "err_wifi_dhcp": "Fikk ikke adresse fra %s. Hold knappen inne i 5 sekunder for å velge et annet nettverk.",
        "err_http": "Får ikke kontakt",
        "err_http_detail": "Skjermen er på Wi-Fi, men når ikke Quiver. Sjekk internett på nettverket.",
        "err_refused": "Skjermen ble avvist",
        "err_refused_detail": "Quiver kjenner ikke igjen skjermen. Hold knappen inne i 5 sekunder for å sette den opp på nytt.",
        "err_api": "Quiver svarer ikke",
        "err_api_detail": "Tjenesten svarte %s.",
        "err_frame": "Feil i bildet",
        "err_panel": "Skjermfeil",
        "err_memory": "Tom for minne",
        "err_memory_detail": "Kortet har for lite minne til bildet.",
        "err_other": "Noe gikk galt",
        # error()
        "error_sub": "Bildet oppdateres ikke akkurat nå",
        "error_retry": "Prøver igjen om %s",
        "error_count": "Forsøk nr. %d på rad som feilet",
        # fmt_wait()
        "wait_min": "%d min",
        "wait_h": "%d t",
        "wait_h_min": "%d t %d min",
    },
    "en": {
        "starting": "Starting",
        "connecting_to": "Connecting to %s",
        "connecting_wifi": "Connecting to Wi-Fi",
        "fetching": "Getting the picture from Quiver",
        "firmware": "Firmware %s",
        "versions": "Firmware %s · runtime %s",
        "setup_title": "Set up the screen",
        "scan_qr": "Scan the QR code with the Quiver app",
        "setup_1": "In Quiver, tap “Add device”",
        "setup_2": "Scan the QR code",
        "setup_3": "Choose your Wi-Fi network",
        "code_title": "Add the screen",
        "code_or": "Or enter the code",
        "code_ble": "No code needed over Bluetooth.",
        "code_valid": "Valid for %d min",
        "wifi_title": "Couldn’t connect",
        "wifi_ssid": "Wi-Fi “%s”",
        "wifi_auth": "Wrong password. Enter it again in the app.",
        "wifi_no_ap": "Network not found. The screen must be within range, and only 2.4 GHz works.",
        "wifi_dhcp": "The network gave no address. Try again, or choose another network.",
        "wifi_again": "Choose Wi-Fi again in the app",
        "connected_title": "Connected",
        "connected_sub": "The screen was added to your home",
        "connected_body": "Getting the first picture. It will be here in a moment.",
        "connected_foot": "Getting the first picture…",
        "step_setup": "Setup",
        "step_wifi": "Wi-Fi",
        "step_verify": "Verify",
        "step_done": "Done",
        "stopped_title": "Setup stopped",
        "stopped_sub": "To save power",
        "stopped_button": "Hold the button for 5 seconds to start setup again.",
        "stopped_reset": "Press RESET on the board to start setup again.",
        "stopped_foot": "The screen sleeps until then",
        "err_wifi": "No Wi-Fi",
        "err_wifi_auth": "Wrong password for %s. Hold the button for 5 seconds to choose another network.",
        "err_wifi_no_ap": "Network %s not found. Hold the button for 5 seconds to choose another network.",
        "err_wifi_dhcp": "No address from %s. Hold the button for 5 seconds to choose another network.",
        "err_http": "Can’t reach Quiver",
        "err_http_detail": "The screen is on Wi-Fi but can’t reach Quiver. Check the network’s internet connection.",
        "err_refused": "Screen refused",
        "err_refused_detail": "Quiver doesn’t recognize the screen. Hold the button for 5 seconds to set it up again.",
        "err_api": "Quiver isn’t answering",
        "err_api_detail": "The service answered %s.",
        "err_frame": "Bad picture",
        "err_panel": "Display error",
        "err_memory": "Out of memory",
        "err_memory_detail": "The board doesn’t have enough memory for the picture.",
        "err_other": "Something went wrong",
        "error_sub": "The picture isn’t updating right now",
        "error_retry": "Trying again in %s",
        "error_count": "Failed attempts in a row: %d",
        "wait_min": "%d min",
        "wait_h": "%d h",
        "wait_h_min": "%d h %d min",
    },
}

_lang = DEFAULT


def pick(code):
    """The language `code` names, if the screens have it ("en", "EN", "en-GB" → "en"), else None."""
    if not isinstance(code, str):
        return None
    code = code.strip().lower()
    if code in TEXT:
        return code
    code = code.split("-")[0].split("_")[0]
    return code if code in TEXT else None


def use(code):
    """Draw in `code` from now on, if the screens have it. Returns the language in use."""
    global _lang
    _lang = pick(code) or _lang
    return _lang


def lang():
    return _lang


def t(key, *args):
    """The text for `key` in the language in use, formatted with args."""
    s = TEXT.get(_lang, TEXT[DEFAULT]).get(key) or TEXT[DEFAULT].get(key) or TEXT["nb"].get(key, key)
    return s % args if args else s
