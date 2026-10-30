# Firmware updates over the air

How a Quiver screen takes new firmware, safely, whether it is one of ours or one someone built
themselves. The firmware is open source; security rests on signatures, not on secrecy.

- **What is updated:** the **app** (`device/quiver`, MicroPython code, any board) often; the
  **runtime** (MicroPython itself with the boot loader frozen in, one image per board) rarely.
- **Who decides:** the API decides which release a screen gets and when. The screen only checks that
  what it is offered is signed with a key it trusts, newer than what runs and meant for it, and goes
  back to what worked if the new version doesn't reach the API.

```
 screen wakes ──▶ GET /device/v1/frame ──▶ answer carries X-Update: 1
   │  draws the frame first
   └▶ GET /device/v1/firmware/offer ─▶ signed manifest ─▶ loader checks it ─▶ download payload
        ─▶ hash checked while written to the free slot ─▶ next start on trial ─▶ API reached: kept
                                                                          └▶ not reached in 5: rolled back
```

## Versions

| | Format | Where it's set | Reported as |
| --- | --- | --- | --- |
| App | SemVer, `3.1.0` (pre-releases `3.1.0-beta.1`) | `tools/package.py app --version` writes it into `FIRMWARE` in `quiver/__init__.py` | `firmware` / `X-Firmware` |
| Runtime | SemVer, `1.0.0` | `runtime/build.sh <board> <version>`; frozen into `quiver_boot/_build.py` | `runtime` |
| Boot loader | SemVer | `BOOT` in `quiver_boot/__init__.py`, frozen with the runtime | `boot` |
| Device protocol | `/device/v1` | The URL | |
| Security level | Integer | `SECURITY` in `quiver/__init__.py` | (in the manifest) |

Versions compare as SemVer (`quiver_boot.ota.compare`). A board never installs a version that isn't
newer than what runs: a bad release is fixed with a newer one. Raising `SECURITY` in a release that
fixes a security hole makes the loader refuse anything with a lower level from then on, so an old
signed release with the hole in it can't be replayed.

An app release names the oldest runtime it runs on (`min_runtime`). App releases are `.mpy`
bytecode, which loads on any runtime with the same MicroPython `.mpy` version (the manifest says `mpy`).

## The protocol

On the existing device endpoints ([device-protocol.md](device-protocol.md)); one more request only
when there's something to do.

| Request | |
| --- | --- |
| `GET /device/v1/frame` | Sends `X-Telemetry`. The answer may carry `X-Update: 1` (an update is waiting) and `X-Log-Request: 1` (support asked for the log). |
| `GET /device/v1/firmware/offer` | Device token. `204`, or `{"id", "kind", "version", "manifest", "signature", "keyId", "url", "size"}`: the manifest as base64 of its exact signed bytes, the signature base64. |
| `GET /device/v1/firmware/blobs/{sha256}` | **Public.** The payload, immutable by name, ranges supported. |
| `POST /device/v1/events` | Errors since the last good wake, and update results (`type: "update"`, `data.result`: `ok`, `failed`, `rolled_back`). |
| `POST /device/v1/log` | `text/plain`, the end of `log.txt`, after `X-Log-Request`. |
| `POST /device/v1/status` | Includes `model`, `runtime`, `boot`, `ota: {"mode", "keys"}`, `build` and `reset`. The API offers only releases signed with one of `ota.keys`. |
| `GET /device/v1/firmware/flash/{board}/manifest.json` | ESP Web Tools' manifest for a browser flasher: the newest stable runtime's full image. |

## The manifest

```json
{"board":"*","created":"2026-10-25T12:00:00Z","format":1,"git":"abc123def456","kind":"app",
 "min_runtime":null,"mpy":"6","security":1,"sha256":"…","size":79813,"version":"3.1.0"}
```

Compact JSON with sorted keys, signed with Ed25519 over its exact bytes. Kind `app` (board `*`) or
`runtime` (board `nano-esp32`, `esp32s3`, …, plus `chip` for a flasher). `tools/package.py` makes
it, signs it with a key file, and checks a signature the way a board does (`verify`).

An **app payload** is a QPK archive: `QPK1`, then entries `<u16 path length><path><u32 size><data>`,
then a zero length. Every path is inside `quiver/`, so a release can't touch `board.json`,
`secrets.json` or the loader. A **runtime payload** is MicroPython's app partition image
(`micropython.bin`); a runtime release also carries the full image (`firmware.bin`) for first flashing.

## On the board

`boot/quiver_boot`, frozen into the Quiver runtime:

| | |
| --- | --- |
| `loader.py` | At every start, before the app: picks the slot, counts trial starts, rolls back, puts the slot first on `sys.path`. `commit()` when the app reaches the API. |
| `ota.py` | `check()` the manifest (signature, kind, board, newer, security, runtime, `.mpy`, size) before anything is downloaded; `install_app()` / `install_runtime()` hash the payload as it streams to flash. |
| `ed25519.py` | Ed25519 verification in plain Python (about a second on an ESP32-S3), with SHA-512 where hashlib lacks it. No C module, so stock MicroPython can check releases too. |
| `keys.py` | The public keys releases may be signed with. See [keys/README.md](../keys/README.md). |

Flash layout:

```
/boot.json        the loader's state (written only when something changes)
/slots/a/quiver   an app release      ─┐ A/B: an update goes to the slot that isn't running
/slots/b/quiver   the previous one    ─┘
/board.json, /secrets.json, /log.txt, /outbox.json   never touched by an update
(frozen)          quiver_boot, the app the board came with, main.py
```

**An app update** is written to the free slot and set pending. The next start runs it on trial:
the loader counts the start and arms a 30-minute watchdog (a hang resets the board). The first time
the app gets an answer from the API, it commits: the slot becomes the active one. If five starts pass
without that, the loader deletes the slot and goes back to the one that worked, and the board
reports `rolled_back` once it gets through. A crash on trial retries after a minute, not an hour, so
a broken release is undone quickly.

**A runtime update** is written to the other OTA partition and set to boot. ESP-IDF's rollback
(`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE`) does the rest: the new runtime must confirm itself in its
first start (the app reaching the API calls `mark_app_valid_cancel_rollback`), or the chip starts the
old one again at the next reset, deep sleep included. The partition table and the bootloader are
never updated over the air: they are fixed by the first flash.

The app draws the frame before anything else, installs after, and then starts the new version
straight away: a new app after a one-second sleep, a new runtime with a reset. A wake from deep sleep
can't start a runtime: MicroPython's bootloader (`CONFIG_BOOTLOADER_SKIP_VALIDATE_IN_DEEP_SLEEP`)
boots the partition that ran last on such a wake, not the one set to boot, so the old runtime would
start and the new one be rolled back without ever running.

`build` in the status report says whether the running app is a release (`official`: the slot's
files match what was installed, checked at power-on, or the frozen app) or was changed by hand
(`custom`).

## Security

| Threat | Answer |
| --- | --- |
| The API or its operators send out malicious firmware | Boards install only what is signed with a key in their frozen `keys.py` (or their own `ota_keys`). The API holds no private keys. |
| Downgrade to an old release with a known hole | Never older than what runs; never a lower `security` than installed. |
| Man in the middle on the Wi-Fi | The signature protects the code. TLS, always verified, protects the device token. |
| A hostile archive (`../secrets.json`, huge files) | The manifest is checked before download; paths must be inside `quiver/`; sizes are capped; nothing is staged unless the whole payload hashes right. |
| Power cut or bad release | A/B slots, trial starts, watchdog, rollback; ESP-IDF rollback for runtimes; the frozen app as the last resort. |
| A release key leaks | Two keys are trusted, one in use and one reserve. A runtime without the leaked one, signed with the other, retires it. |
| Physical access to a board | Flash is readable on purpose: Secure Boot and flash encryption burn eFuses for good and would lock owners out of their own hardware. What can be read is the Wi-Fi password and this one screen's token, which is revoked by removing the screen. |
| The public development key | Trusted only through `ota_keys` in board.json, and only while `api_url` is a local address. |

## TLS

The board checks the API's certificate on every HTTPS request; there is no way to turn that off.

- **Quiver's API** uses certificates from Google Trust Services. The app carries Google's roots
  (`quiver/ca.py`: GTS Root R1–R4 and GlobalSign Root CA, from pki.goog), so it works without
  configuration. The roots are part of the app, so a release can add a root before it is needed.
- **The clock.** Certificates are checked against the board's clock, which starts in the year 2000
  after a power cut. The board sets it from NTP (`quiver/clock.py`) before its first HTTPS request;
  the RTC keeps it through deep sleep.
- **A self-hosted API** with another CA: put the CA certificate (DER or PEM) on the board and name
  it in board.json (`"ca_file": "my-ca.der"`).
- **Local development:** plain `http://` is allowed, but only for an API on the local network
  (localhost, a `.local` name, a private IPv4 address), so a board can never be pointed at an
  unencrypted API on the internet. Signatures are still checked.

## DIY

Anyone can build a screen and keep it up to date safely:

1. **Flash the runtime** from the browser at `https://quiver.wext.as/diy/flash/` (Chrome or Edge), or build
   it with `runtime/build.sh` and flash it with esptool or `tools/flash.py`.
2. **board.json** for wiring other than the Arduino Nano ESP32 defaults (`mpremote cp board.json :`).
3. **Pair it** with the app. From then on it takes Quiver's signed releases like any screen.

Options in board.json:

| | |
| --- | --- |
| `"ota": "off"` | Never update: for a board running code you changed yourself. |
| `"ota_keys": {"me": "<base64 Ed25519 public key>"}` | Trust your own key too, for your own API (`api_url`) and releases you sign (`tools/package.py keygen`, `sign --key`; see [keys/README.md](../keys/README.md)). |
| `"model": "my-board"` | The board's name in releases, on stock MicroPython. |

**Stock MicroPython** (no Quiver runtime) works too: copy `device/main.py`, `device/quiver/` and
`boot/quiver_boot/` to the board. App updates work the same (signature checks in plain Python); there
are no runtime updates and no frozen fallback app. A power cut during an update leaves the old app
running.

**Development against a local API:** sign with the public dev key and trust it on the board:

```sh
python tools/package.py app --version 3.1.0-dev.1 --sign-dev
```

```json
{ "api_url": "http://192.168.1.20:5068", "ota_keys": { "dev": "OuFmKK2huk4vBe96SLOJWRr4jcqcVBrhH5zNhvADUQU=" } }
```

## Testing

- `python -m unittest discover -s tests` runs the loader, the checks, slots, trial starts,
  rollback and runtime updates against a stand-in API (`tests/test_ota.py`), with flash and OTA
  partitions faked on the PC.
- `python tools/package.py verify <release folder>` checks a signature with the loader's own code.
- On hardware: keep one board on USB power on a development channel, and try each release there first.
