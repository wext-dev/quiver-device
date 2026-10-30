# Quiver screen firmware (MicroPython)

The firmware for a [Quiver](https://quiver.app) screen: an ESP32 with MicroPython and a Waveshare
e-paper panel, the boot loader that installs signed updates, and the build of the Quiver runtime.
The screen talks to the Quiver API over the device protocol in [docs/device-protocol.md](docs/device-protocol.md);
updates are described in [docs/ota.md](docs/ota.md).

The firmware is generic. The
screen is generic. It knows how to get online, pair with a home and draw what it's sent, and
nothing about shoes, runs or views:

- **Setup** over Bluetooth: the app sends Wi-Fi, the screen registers with the API and shows a
  pairing code, and the user claims it in the app.
- **Running:** every wake it asks the API for a frame for its exact panel, draws it if it changed,
  and deep-sleeps until the next poll. The API decides how often (5 min to 24 hours, set in the app).
- **The buttons** wake it and say which was pressed: next view, page down or page up. The API keeps
  the view and page, and draws the frame for them. Holding the main button for 5 s starts setup again.
- **New Wi-Fi without setup:** a screen that can't join its network keeps trying, and shows No Wi-Fi
  with a QR. If the network has changed, the app sends the new one with that QR over Bluetooth
  ("Change Wi-Fi" on the screen in the app); the screen stays in its home with its views.
- **Progress on the panel** while nothing else can be shown yet: a start-up screen with the logo
  and the firmware and runtime versions after a power cut, then setup steps ticked off as they happen (app, Wi-Fi, code, home), the same
  steps the app shows. Every screen's footer carries the maker's wordmark, Wext AS.
- **Updates over the air:** signed releases the API offers, checked by a boot loader that keeps the
  last working version and goes back to it if a new one doesn't reach the API
  ([docs/ota.md](docs/ota.md)).
- **Reporting:** battery, signal and more with every frame request, errors once it gets through,
  and its log when Quiver support asks for it.

The contract with the app and the API is [docs/device-protocol.md](docs/device-protocol.md).
This replaces the earlier standalone `shoestore` firmware, which fetched from Strava and drew the
widgets on the board. The panel driver, the background refresh, RTC state, the log, retry back-off
and the bitmap-font canvas come from that firmware. Strava, the widgets and the clock are gone:
the API does that work.

```
device/                  the app (in a Quiver runtime, frozen in; on stock MicroPython, copied to the board)
  main.py                runs at every boot and wake: the loader picks the app, then one wake-up
  quiver/
    wake.py              one start: early button read, app.run(), deep sleep
    app.py               one wake-up: button, setup if needed, frame, reports, update, sleep
    update.py            installing an offered update (the checks are the loader's)
    outbox.py            events waiting to be sent: errors, update results
    clock.py             NTP after a power cut (TLS checks certificate dates)
    ca.py                the CA roots TLS to Quiver's API is checked against
    setup.py             setup mode: Bluetooth, Wi-Fi, registration, pairing code, token
    ble.py               the GATT service (info, control, networks, wifi, status)
    framing.py           1-byte-flag chunk framing for Bluetooth messages
    crypto.py            the setup secret behind the setup QR: Wi-Fi decryption (AES-128-CBC +
                         HMAC-SHA256) and the claim secret the app adds the screen with
    qr.py                QR codes for the setup screens
    api.py, http.py      the device endpoints, over a small HTTP/1.0 + TLS client
    frame.py             RHF1 frames (the API's bit planes)
    epd.py, panel.py     the 4.2" panel driver (UC8176), black/white or black/white/red
    button.py            which button woke it, how long it's held; arms the wake-up
    wifi.py              join and scan
    store.py             secrets.json and lang.txt on flash, wake-to-wake state in RTC memory
    screens.py           the screens the board draws itself: start-up, setup steps, code, errors
    text.py              their words, in English and Norwegian (the app's language, then the owner's)
    canvas.py, fonts.py  drawing for those, with Geist fonts in fontdata/ and logo.py
    board.py             board.json: panel, wiring, API URL, updates
boot/quiver_boot/        the boot loader, frozen into the Quiver runtime (docs/ota.md)
  loader.py              A/B app slots, trial starts, commit and rollback
  ota.py                 checks signed manifests, installs app archives and runtimes
  ed25519.py             signature checks in plain Python
  keys.py                the release keys boards trust
runtime/                 builds the Quiver runtime: MicroPython + loader + app, OTA partitions (build.sh)
keys/                    about the signing keys; dev.key, the public development key
tools/
  package.py             packs, signs (with a key file) and checks releases
  flash.py               flashes a runtime image over USB and copies board.json
  simulate.py            runs the firmware on a PC against a real API and saves the panel as PNGs
  sim.py, fakes/         the PC stand-ins for machine, network, bluetooth, esp32, cryptolib, framebuf
  make_fonts.py          rebuilds device/quiver/fontdata/ from the Geist fonts
tests/                   unit tests, whole wake-ups and updates against a protocol stand-in
board.example.json       copy to device/board.json and adjust
```

## Python environment

The tools, tests and `mpremote` run from a virtual environment in this folder (git-ignored):

```
cd firmware
python -m venv .venv
.venv\Scripts\Activate.ps1        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

In VS Code, pick `.venv` as the interpreter. The board itself runs MicroPython and uses none of this; `device/` is copied to it as is.

## What you need

- An ESP32-S3 board with Bluetooth LE. The defaults assume an **Arduino Nano ESP32**. The Quiver
  runtime is built for it and for ESP32-S3 boards with 8 MB flash or more; stock MicroPython 1.22 or
  newer works on any ESP32 with Bluetooth LE, without runtime updates.
- A Waveshare 4.2" e-paper module: black/white/red (4.2" B) or black/white.
- Optional: a push button, and a LiPo with a voltage divider for battery readings.

## Wiring

Any free GPIOs work for the panel, since the ESP32 routes SPI to any pin. These are the defaults
in `board.json`, given as GPIO numbers:

| Part | Nano ESP32 pin | GPIO |
| --- | --- | --- |
| Panel VCC | 3.3V | |
| Panel GND | GND | |
| Panel DIN | D11 | 38 |
| Panel CLK | D13 (may be labelled SCK) | 48 |
| Panel CS | D10 | 21 |
| Panel DC | D9 | 18 |
| Panel RST | D8 | 17 |
| Panel BUSY | D7 | 10 |
| Button (next view) | D2, to GND, with a 10 kΩ pull-up to 3.3V | 5 |
| Page down (optional) | D3, the same way | 6 |
| Page up (optional) | D4, the same way | 7 |

`board.json` takes GPIO numbers, not the D labels printed on the board. The page buttons are off
by default; set `pins.page_down` and `pins.page_up` to use them.

Buttons must be on RTC-capable GPIOs (0–21 on the ESP32-S3) to wake the board from deep sleep.
The internal pull-ups aren't reliable while asleep, so fit the external ones. Without a button, set
`pins.button` to `null`. The screen then holds one view at a time, and setup is restarted with
RESET instead.

| Press | Sends | |
| --- | --- | --- |
| Button | `X-Button: next` | next view, on its first page |
| Button, held 5 s | | setup again |
| Page down / page up | `X-Button: page-down` / `page-up` | next / previous page, wrapping |

With only one button, set `"button_mode": "pages"` to page with it too: a short press is the next
page, held for 1 s the next view, held for 5 s setup again.

One button wakes the board on ext0. Several wake it on ext1, which doesn't record which pin did it,
so `main.py` reads the pins before anything else is loaded. A very quick tap on a page button can
still be missed; the wake-up then just refreshes.

## Setup

**With the Quiver runtime** (recommended: updates of the app and the runtime, a frozen fallback):

1. **Flash it** from the browser at `https://quiver.wext.as/diy/flash/` (Chrome or Edge), or build it
   yourself with `runtime/build.sh QUIVER_NANO_ESP32 1.0.0` (Docker) and flash the full image with
   esptool: `esptool.py --chip esp32s3 write_flash 0x0 runtime/dist/QUIVER_NANO_ESP32/firmware.bin`.
   Or with `python tools/flash.py`: it finds the board in download mode, flashes
   `runtime/dist/<board>/firmware.bin` (or `--image`), and copies `device/board.json` over. For
   download mode on a Nano ESP32, connect B1 to GND, press RST and remove the wire.
2. **board.json**, only if your wiring or panel differ from the Nano ESP32 defaults:
   `mpremote cp board.json :board.json`. For local development it points at the API on your
   machine and trusts the development key, so updates published locally reach it:

   ```json
   { "api_url": "http://<your machine's LAN address>:5068",
     "ota_keys": { "dev": "OuFmKK2huk4vBe96SLOJWRr4jcqcVBrhH5zNhvADUQU=" } }
   ```
3. **Pair it** (below).

**On stock MicroPython** (any ESP32 with Bluetooth LE; app updates only):

1. **Flash MicroPython.** Get the firmware for your board from
   [micropython.org/download](https://micropython.org/download/). For the Nano ESP32 that's
   `ARDUINO_NANO_ESP32`.
2. **Write board.json.** Copy `board.example.json` to `device/board.json` and set `api_url` (and
   `pins` if your wiring differs, and `model`). No secrets go in it.
3. **Copy the files** with [mpremote](https://docs.micropython.org/en/latest/reference/mpremote.html).
   From the repository root:

   ```
   mpremote cp -r device/quiver device/main.py device/board.json boot/quiver_boot :
   ```

   Leave out `quiver_boot` for no updates at all.

Then:

4. **Pair it.** Press RESET. The panel shows "Set up the screen" with a QR code, and the board
   advertises over Bluetooth as `Quiver-XXXX` (the last four hex digits of its hardware id). In the
   app: *Enheter → Legg til enhet*, and scan the QR. The app finds that screen, sends Wi-Fi encrypted
   with the QR's secret, and adds it to the home with the same secret once it has registered. The
   panel then shows the QR again with a code under it, for adding a screen that's already on Wi-Fi
   by typing. Once claimed, the panel says "Connected" and the screen fetches its first frame.

   The QR is new each time setup starts and is never stored: there's no sticker and nothing to
   provision per board.

Bluetooth stays on for 10 minutes without progress, then the board shows "Setup stopped" and
sleeps until the button (or RESET).

## board.json

| Key | Default | Meaning |
| --- | --- | --- |
| `api_url` | `https://quiver.wext.as/api` | The Quiver API. Must be `https://`; `http://` only for an API on the local network (development). |
| `panel` | `ws-4.2-bwr` | `ws-4.2-bwr` (black/white/red) or `ws-4.2-bw` (black/white). Reported to the API, which renders for it. |
| `pins` | Nano ESP32 | GPIO numbers for `sck`, `mosi`, `cs`, `dc`, `rst`, `busy`, and optionally `power`, `button`, `page_down`, `page_up`, `battery` |
| `button_active_low` | `true` | The buttons pull their pins to GND. `false`: to 3.3 V |
| `button_mode` | `views` | `views`: the button shows the next view. `pages`: a short press pages, held 1 s the next view |
| `battery_divider` | `2.0` | The divider ratio on `pins.battery` |
| `ca_file` | none | A DER or PEM CA certificate on the board, for a self-hosted API. Without it, TLS is checked against Google's roots (`ca.py`), which Quiver's API uses. TLS is always checked. |
| `model` | the runtime's | The board's name in releases. Set it on stock MicroPython. |
| `ota` | `official` | `official`: take Quiver's signed releases. `off`: never update (for code you changed yourself). |
| `ota_keys` | none | More signing keys to trust, `{"id": "<base64 Ed25519 public key>"}`: your own, for a self-hosted API, or the dev key with a local API ([docs/ota.md](docs/ota.md#diy)). |
| `wifi_timeout_s` | `20` | How long to try joining Wi-Fi |
| `setup_timeout_s` | `600` | How long setup advertises without progress before stopping |
| `wifi_listen_s` | `300` | How long the No Wi-Fi screen listens over Bluetooth for new Wi-Fi from the app (when it's drawn, and after a press or RESET) |
| `errors_after` | `3` | Failures in a row before an error screen replaces the picture |
| `retry_minutes`, `max_retry_minutes` | `5`, `120` | Retry after a failure, doubling each time up to the max |
| `token_refused_hours` | `24` | How long `401`s are retried before the token is dropped and a new code shown |
| `stay_awake` | `false` | Run once and stay at the REPL instead of sleeping |
| `gray` | `true` | On a panel that can draw grey (`ws-4.2-bw`), ask for grey frames: smoother text. `false`: black and white only |
| `lang` | `en` | The language of the board's own screens (setup, code, errors) until the app sets it: `en` or `nb`. The app's language, sent during setup, then the owner's, sent with every frame, win over it ([docs/device-protocol.md](docs/device-protocol.md#language)). |

An unknown key is an error, written to `log.txt`.

## How a wake-up goes

1. **Woken by a button:** the main button held for 5 s starts setup again. Otherwise the press is
   sent in step 4. After a power cut, a start-up screen with the logo shows while it connects.
2. **Not set up:** setup mode (above). Once claimed, it carries on to step 3.
0. **The loader** (before the app) picks the app slot, and counts the start if a new version is on
   trial ([docs/ota.md](docs/ota.md#on-the-board)).
3. **Join Wi-Fi.** After any start but a wake from deep sleep (with why it started), or when the
   firmware, panel, features or software changed, the board sends `POST /device/v1/status`. After
   a power cut it sets the clock from NTP first.
4. **Fetch.** `GET /device/v1/frame` with the ETag on screen (`X-Frame-ETag`), `X-Telemetry` (battery, signal, free
   memory, wakes …), and `X-Button` after a press. If a press's request fails, it's retried without
   the header: a missed press is better than skipping a view or page. An answer from the API
   confirms a new version on trial. Then the errors queued since the last good wake go out, and the
   log if the answer asks for it.
5. **Wi-Fi off.** On `200` the frame is drawn (a red refresh takes ~15 s); on `204` (unchanged)
   the panel is left alone. If the answer offered an update, the board fetches and installs it and
   starts it right away. Otherwise deep sleep for `X-Poll-Seconds`, or until the button.

After 12 unchanged answers in a row the board asks without its ETag and draws the frame again. If a refresh
ever failed without an error, the panel would otherwise keep showing something other than what the
ETag says until a RESET. `log.txt` gives each wake a line (`wake 12: timer or button`) and each
drawn frame its refresh time; a grey refresh that ends too soon to have run is an error, so the
frame is tried again.

A failure keeps the picture and retries after 5, 10, 20 … up to 120 minutes, and never gives up.
Without Wi-Fi the waits stop at 15 minutes (`WIFI_MAX_RETRY_MINUTES` in `quiver/app.py`): the
network is often back soon, and a try costs only a few seconds awake.
After three in a row, an error screen says what's wrong ("No Wi-Fi", "Can’t reach Quiver" …) and
when it tries again. After a power cut or RESET it says so at the first failure, since the start-up
screen ("Connecting to …") would otherwise stay up.
It isn't redrawn for the same error, since a refresh costs battery. The first frame after setting
up again in the same wake (after a `410` or a refused token, below) fails the same way.

No Wi-Fi has a QR, made like the setup QR (`quiver:wifi:…` instead of `quiver:v2:…`). If the network
has changed, the user opens the screen in the app, taps "Change Wi-Fi", scans it, and the app sends
the new network over Bluetooth, encrypted with it. The board takes Wi-Fi and nothing else: it keeps
its token, home and views, keeps the old Wi-Fi until the new one works, and fetches its frame at
once. Bluetooth is on only for `wifi_listen_s` after the screen is drawn, and again after a press or
RESET wakes the board while it shows: a timer wake doesn't listen, since nobody is there.

Every wake runs under a 30-minute watchdog (`quiver/wake.py`), longer than setup over Bluetooth or
an update takes. A wake that hangs, say a TLS read that outlives its timeout, resets the board and
it starts again, instead of staying awake on its last screen until the battery runs out. With
`stay_awake` there's no watchdog, so the REPL isn't reset under you.

A `410` with `device_released` means the screen was removed in the app, or its owner deleted their
account. The board forgets its Wi-Fi and token and starts setup from scratch: only the app, over
Bluetooth, can set it up again. A `401` means the API doesn't
know the token. A restored database or a bad deploy looks the same, so it's retried like any other
failure ("Skjermen ble avvist"). Only after `token_refused_hours` of them is the token dropped and
setup started with the Wi-Fi kept: the board registers straight away, and the app adds it from the
QR on the panel (or the code under it) without Bluetooth. No other answer, and no outage however long, makes the board forget anything.

## Debugging

`log.txt` on the board records each wake-up: what it did and any error.

```
mpremote cat :log.txt
mpremote exec "from quiver import app; print(app.run())"     # one wake-up by hand, no sleep afterwards
mpremote rm :secrets.json + reset                            # forget Wi-Fi and token: setup again
mpremote cat :boot.json                                      # the loader: slot, version, an update on trial
mpremote cat :outbox.json                                    # events not sent yet
mpremote rm -r :slots + rm :boot.json + reset                # back to the app the board came with
```

Quiver support can also ask a screen for its log through the API; it arrives on the next wake.

To get to the REPL, press RESET and connect with `mpremote` within 3 seconds; it sends Ctrl-C. A
sleeping board doesn't answer on USB until RESET. Set `"stay_awake": true` while trying things.

## Running it on a PC

The firmware runs unchanged under CPython, with the stand-ins in `tools/fakes`. You need
`pip install -r requirements.txt`.

```
python -m unittest discover tests        # unit tests, whole wake-ups and updates (no API needed)
QUIVER_SCREENS_OUT=/tmp/screens python -m unittest discover tests -p test_units.py   # PNGs of the board's screens
```

`tools/simulate.py` runs the firmware against a real API. It plays the phone: it sends Wi-Fi over the
fake Bluetooth, claims the code for a user, and saves every image the panel gets:

```
python tools/simulate.py --api http://localhost:5068 --user-token "<dev token>" --dir /tmp/board --out /tmp/panel
python tools/simulate.py ... --dir /tmp/board --press 2    # wake again and press the button twice
python tools/simulate.py ... --dir /tmp/board --pages 3    # page down three times
```

Any API that implements the device protocol and accepts a development token works here.

## Fonts

MicroPython can't render TrueType, so `tools/make_fonts.py --fonts <folder>` pre-renders the sizes
and characters the board's screens use from Geist and Geist Mono (the faces the API draws frames
with; SIL Open Font License), into `device/quiver/fontdata/` (27 KB).
Rebuild them after adding a size or a character to `screens.py`. Characters outside ASCII and
Norwegian letters show as `?`. The logos in `logo.py` are generated from the Quiver design system's Logo folder by
`design/brand/build.py` in the runhistory repo.

## Battery life

Most of the energy goes to Wi-Fi and TLS while the board is awake. Rough figures; measure your own:

| | Time | Current |
| --- | --- | --- |
| Awake: Wi-Fi, TLS, one request | 3–8 s | 80–150 mA |
| Panel refresh (red), CPU at 80 MHz | ~15 s | 20–40 mA |
| Deep sleep: ESP32 chip | the rest | ~10 µA |

A wake with nothing new (`204`) never touches the panel. At a 30-minute poll that's a few mAh a
day, so a 2000 mAh LiPo could last months, but only if the board itself sleeps at microamps. The
Nano ESP32 doesn't: its power LED stays on and it has no charger. For battery use, pick a board built
for it (Seeed XIAO ESP32-S3, DFRobot FireBeetle 2, Adafruit Feather ESP32-S3) and set `pins` to
match. To cut the panel's own draw, switch its power through a MOSFET on `pins.power`. Setup mode
keeps Bluetooth on, so it's the most expensive state; it stops after `setup_timeout_s` (and the
No Wi-Fi screen's listening after `wifi_listen_s`).

## Security

- The board holds a device token, never user credentials. It's revocable in the app (remove the
  screen) and only fetches frames for this one screen. Removing the screen, or deleting the account,
  also makes the board forget its Wi-Fi password on its next wake.
- **Reset:** only an explicit `410 device_released` wipes the board.
- **TLS** is always verified: against Google's roots for Quiver's API, or `ca_file`. Plain HTTP only
  to an API on the local network. See [docs/ota.md](docs/ota.md#tls).
- **Updates** are installed only if signed with a release key frozen into the runtime (or listed in
  `ota_keys`), newer than what runs, and for this board; the payload's hash is checked as it is
  written, and a new version that doesn't reach the API is rolled back. Quiver's signing keys are
  not in this repository or on the API's servers. See [docs/ota.md](docs/ota.md#security).
- **Setup QR:** the board only takes Wi-Fi encrypted with the secret in the QR it shows, and the
  app only adds it to a home with a claim secret derived from it. So setting it up takes someone
  in front of the panel, and anyone listening to Bluetooth sees neither the password nor a way
  in. The secret is new for every setup and never stored; the API only gets a hash of the claim
  secret. The code under the QR is the one exception: anyone who reads it off the panel can add
  the screen, as before, until it expires (15 min).
- **Secrets on the board:** `secrets.json` (Wi-Fi password and device token) is plain text on the
  flash, readable by anyone with USB access to the board. That's a choice: flash encryption and
  secure boot burn eFuses for good and would lock owners out of their own hardware. The token only
  works for this one screen and is revoked by removing the screen in the app.

## Limits and to-dos

- **Not run on hardware yet.** It runs end to end on a PC, against a protocol stand-in in the
  tests and against the real API with `tools/simulate.py`. The panel driver is unchanged from the
  previous firmware, which ran on this board. The Bluetooth service, the button wake (ext1 for
  page buttons), the HTTP client and the setup QR (scanning it off the panel, and how long choosing
  its mask takes in MicroPython) still need trying on a board. So do the update path's
  hardware-specific parts: TLS with the bundled roots and NTP, streaming to flash, runtime OTA
  partitions and ESP-IDF rollback, and how long the plain-Python Ed25519 check takes.
- Each screen the board draws is a full refresh (~15 s with red), so progress screens are drawn
  only where the board waits anyway. The pairing code is never held up; "Connected" puts one
  refresh in front of the first frame, and the start-up screen one in front of the frame after a
  power cut.
- Readable Bluetooth values (`info`, `networks`, `status`) are served as one final chunk of up to
  512 bytes, fetched by the phone with a long read. MicroPython on the ESP32 doesn't report
  individual reads, so the value can't be advanced chunk by chunk. The app handles both.
- Only the 4.2" panels have a driver. A 7.5" panel needs one in `epd.py` and an entry in `board.PANELS`.
- **Grey on `ws-4.2-bw` is untried on hardware.** The sequence and lookup tables are Waveshare's
  own 4-grey mode for this controller (`epd4in2.py`). If the panel looks wrong or washed out, set
  `"gray": false` in `board.json` to go back to black and white. The board's own screens (setup,
  code, errors) stay black and white either way.

## License

[Apache License 2.0](LICENSE).
