# Device protocol

How a Quiver screen (Arduino Nano ESP32 + Waveshare e-paper, MicroPython) is set up, paired with a
home and kept up to date. The firmware implements the **device side**. The mobile app and the API
implement the rest.

The design goals:

- **The device is dumb and generic.** It never knows about shoes, runs or views. It downloads a
  ready-made bitmap for its exact panel, draws it and goes back to sleep. New views ship with the API,
  with no firmware update.
- **The device describes itself.** Panel model, resolution and colours, firmware version, battery and
  Wi-Fi come from the device. The user never picks a screen type in the app.
- **The device never holds user credentials.** It gets its own revocable device token, issued through
  a device-authorization flow modelled on [RFC 8628](https://datatracker.ietf.org/doc/rfc8628/).
  The *user* signs in to the app. The device is authorised by that user claiming it.
- **Setting a device up takes someone in front of it.** In setup mode the screen shows a QR code
  with a secret that is new every time. The device only takes Wi-Fi encrypted with it, and the app
  claims the device with a secret derived from it (see [The setup QR](#the-setup-qr)).

```
 ┌────────┐  0. camera: setup QR on the e-paper      ┌────────┐
 │  App   │◀───────────────────────────────────────── │ Device │
 │        │  1. BLE: info, Wi-Fi scan, encrypted      │        │
 │        │◀────────────────────────────────────────▶│        │
 │        │     credentials; 4. notify: registered    │        │
 └───┬────┘                                           └───┬────┘
     │ 5. POST /api/home/devices/claim                    │ 2. POST /device/v1/register {claimHash}
     │    {hardwareId, claimSecret} (user's session)           │ 3. POST /device/v1/token (poll)
     ▼                                                    ▼    → device token once claimed
 ┌───────────────────────────── API ─────────────────────────────┐
 │ 6. GET /device/v1/frame (device token, ETag) → bit planes      │
 └────────────────────────────────────────────────────────────────┘
```

## 1. Bluetooth LE provisioning

The device enters **setup mode** when:

- it has no Wi-Fi credentials or no device token;
- the user holds the button for 5 s;
- the API answers `410 device_released` (the screen was removed or its account deleted). Wi-Fi and
  token are wiped first, so setup starts from scratch (see [Removed screens](#removed-screens));
- the API has answered `401` for `token_refused_hours` (24 h by default). The token is dropped and
  the Wi-Fi kept, so the screen registers straight away and shows a code.

In setup mode it advertises over BLE as `Quiver-XXXX`. `XXXX` is the last 4 hex digits of the
hardware id. The advertisement carries the service UUID below, and the app filters its scan on that UUID.

### GATT service

Service UUID: `5f1e0001-8a3c-4b7e-9d2a-7c1b5e9a0f10`

| Characteristic | UUID | Properties | Payload (JSON, framed, see below) |
| --- | --- | --- | --- |
| `info` | `5f1e0002-…0f10` | read | `{"hw":"a1b2c3d4e5f6","model":"nano-esp32","fw":"1.2.0","panel":{"id":"ws-4.2-bwr","w":400,"h":300,"colors":"bwr"},"bat_mv":3920,"features":["button"]}` |
| `control` | `5f1e0003-…0f10` | write | `{"cmd":"scan"}` · `{"cmd":"reboot"}` · `{"cmd":"forget"}` (wipe Wi-Fi and token) |
| `networks` | `5f1e0004-…0f10` | read, notify | `[{"ssid":"Hjemme-2G","rssi":-52,"sec":"wpa2"}, …]` (`sec`: `open`, `wpa2`, `wpa3`). Sent after `scan`. 2.4 GHz only. |
| `wifi` | `5f1e0005-…0f10` | write | `{"ssid":"Hjemme-2G","pw":"…"}`, encrypted with the setup QR (below). Anything else is refused. |
| `status` | `5f1e0006-…0f10` | read, notify | `{"state":"…","code":"K7MQ-4XRT","err":null}` |

The full 128-bit UUIDs differ only in the second group: `5f1e000N-8a3c-4b7e-9d2a-7c1b5e9a0f10`.

`status.state` moves through these states:

```
idle → wifi_connecting → wifi_ok → registering → awaiting_claim(code) → claimed
                 └→ wifi_failed (err: "auth" | "no_ap" | "dhcp" | "key")   └→ error (err: "api_unreachable" | "expired")
```

- `wifi_failed` with `key`: the Wi-Fi wasn't encrypted with this setup's QR (sent in plain text,
  with the QR of an earlier setup, or changed on the way). The app should scan the QR again.

- At `awaiting_claim` the device draws the setup QR again with the code under it, so pairing works
  without BLE too: the app claims it from the QR, or the user types the code.
- When it reaches `claimed`, the device leaves setup mode, stops advertising and fetches its first frame.

### Framing

BLE writes and notifications are small (the ATT MTU is often 23–185 bytes), so every JSON payload
is split into chunks. Each chunk is `1 byte flags + data`:

- `flags & 0x01`: this is the last chunk of the message.
- `flags & 0x02`: the message is encrypted (only used on `wifi`).

The receiver appends `data` until it sees the last chunk, then parses the concatenated UTF-8 JSON.
The app requests MTU 185 where the platform allows it. On iOS the stack negotiates it on its own.

Readable characteristics (`info`, `networks`, `status`) are framed the same way. The app reads
repeatedly until it gets a chunk with the final flag. A device may serve the whole message as one
final chunk of up to 512 bytes: the phone's stack fetches values longer than the MTU with long
reads. The MicroPython firmware does this, because it can't see individual reads on the ESP32. A
device that does chunk its reads starts over from the first chunk after the final one.
Notifications are sent as consecutive chunks of MTU − 3 bytes.

### The setup QR

Every time setup starts, the device makes a new 16-byte secret `S` (`os.urandom`, with the radio on
so the hardware RNG is truly random) and shows it as a QR code on the panel:

```
quiver:v2:<hw>:<S, base64url without padding>
```

`S` is never stored or sent. Seeing the panel is the only way to learn it. Three keys are derived
from it with HMAC-SHA256:

| Key | Derivation | Used for |
| --- | --- | --- |
| `enc` | `HMAC(S, "quiver-v2 wifi-enc")[0:16]` | AES-128-CBC key for the Wi-Fi credentials |
| `mac` | `HMAC(S, "quiver-v2 wifi-mac")` | HMAC-SHA256 tag over the encrypted credentials |
| `claimSecret` | `base64url(HMAC(S, "quiver-v2 claim"))` | given to the API to claim the device |

- The `wifi` payload is `base64(iv[16] ‖ AES-128-CBC(enc, iv, PKCS#7(json)) ‖ HMAC(mac, iv ‖ ciphertext))`,
  sent with the encrypted flag. The device checks the tag before it decrypts anything, and refuses
  any other `wifi` message with `wifi_failed`/`key`.
- At `register` the device sends `claimHash` = hex SHA-256 of `claimSecret`. The app, having scanned
  the QR, claims with `hardwareId` and `claimSecret`. The API never learns `S` or the Wi-Fi keys.
- A new setup (button held, screen released, token refused, or a power cycle in setup mode) makes a
  new `S`, so a QR seen earlier is worthless.
- The QR is byte mode, error correction L, version 3 for a 12-digit hardware id (29 × 29 modules,
  drawn at 4 px per module on the 4.2" panel).

## 2. Registration and claiming (device authorization)

All device endpoints are under `/device/v1`, take and return JSON unless noted, and are rate limited
per hardware id.

### `POST /device/v1/register`

Unauthenticated. The device calls it as soon as it is on Wi-Fi.

```json
{ "hardwareId": "a1b2c3d4e5f6", "model": "nano-esp32", "firmware": "3.0.0",
  "panel": { "id": "ws-4.2-bwr", "w": 400, "h": 300, "colors": "bwr" },
  "features": ["button"],
  "claimHash": "3f1c…(64 hex digits)" }
```

`claimHash` is the SHA-256 of the claim secret from the setup QR (lower-case hex), so the app can
claim the device by scanning instead of typing the code. It is optional for the API.

`colors` is `bw` or `bwr`. `id` is informational; the API renders for `w` × `h` and `colors`, so new
panels work without API changes.

`features` lists what the hardware has:

- `button`: a button that shows the next view (see [Buttons and pages](#buttons-and-pages)). A
  screen with a button can hold several views; one without shows one view, and sending it another
  replaces it.
- `paging`: the screen can page through a view's tables (page buttons, or one button that pages on
  a short press). The app shows the screen's current page.
- `gray4`: a black/white panel that can draw four grey levels. It gets grey frames (flag `GRAY`
  below), which give text smooth edges. Ignored on black/white/red panels.

Unknown features are ignored, so firmware can report new ones before the API uses them. Leave the
list empty if there's no button.

→ `200`

```json
{ "deviceCode": "x4Jq…(43 chars)", "userCode": "K7MQ-4XRT", "verificationUri": "https://quiver.app/pair",
  "expiresIn": 900, "interval": 5 }
```

- `deviceCode` is a secret. Only the device knows it, and the API stores only its SHA-256.
- `userCode` uses the alphabet `ABCDEFGHJKMNPQRSTVWXYZ23456789`, with no 0/O/1/I/L/U.
- Calling `register` again returns a fresh code and invalidates the previous one for that hardware id.

### `POST /device/v1/token`

Polled every `interval` seconds until the code is claimed or expires.

```json
{ "deviceCode": "x4Jq…" }
```

| Response | Meaning |
| --- | --- |
| `400 {"error":"authorization_pending"}` | Not claimed yet, so keep polling. |
| `400 {"error":"slow_down"}` | Polling too often: add 5 s to the interval. |
| `400 {"error":"expired_token"}` | The code expired. Call `register` again; the screen shows a new code. |
| `200 {"deviceId":"…","deviceToken":"…","pollSeconds":900}` | Claimed. Store the token in flash and leave setup mode. |

The device token is shown exactly once. The API stores only a hash.

### Claiming (app side)

`POST /api/home/devices/claim` with the user's session token:

With the setup QR (the usual way, over Bluetooth or not):

```json
{ "hardwareId": "a1b2c3d4e5f6", "claimSecret": "SYRtZnHd…(43 chars)", "name": "Gang – Skohylle", "viewId": "…" }
```

Or with the code typed off the panel:

```json
{ "userCode": "K7MQ-4XRT", "name": "Gang – Skohylle", "viewId": "…" }
```

A claim secret that matches no pending registration is `400 {"code":"not_registered"}`: the device
hasn't registered yet (it isn't on Wi-Fi, or is still connecting). The app retries for a while. An
unknown or expired code is `400 {"code":"unknown_code"}`. A claim secret or code works once.

This binds the device to the caller's home and optionally assigns a view. A device that was in
another home is moved; its old token is revoked.

## 3. Updating: frames

Every wake-up the device makes **one** request:

```
GET /device/v1/frame
Authorization: Bearer <deviceToken>
If-None-Match: "<etag of the frame on screen>"
X-Telemetry: awake_ms=4200;bat_mv=3920;fails=0;heap=81234;rssi=-58;slot=a;wakes=12
X-Firmware: 3.1.0
X-Features: button,gray4
```

`X-Telemetry` is `key=value` pairs separated by `;`: keys `[a-z0-9_]{1,32}`, values up to 64
characters, at most 32 pairs. `bat_mv` and `rssi` are the battery and Wi-Fi signal; the API keeps
the rest as the device's latest telemetry and samples it for the admin, whatever the keys, so new
firmware can report new things without an API change. (The first firmware sent `X-Battery-mV` and
`X-RSSI` instead; the API still reads them.)

| Response | Device action |
| --- | --- |
| `304 Not Modified` | Nothing to draw. Sleep `X-Poll-Seconds`. |
| `200 application/vnd.quiver.frame` | Draw the frame, remember the `ETag`, sleep `X-Poll-Seconds`. |
| `410 {"error":"device_released","reason":…}` | The screen was released. Wipe Wi-Fi and token and enter setup mode. See [Removed screens](#removed-screens). |
| `401` | The API doesn't know the token. Keep everything and retry with backoff; only after `token_refused_hours` of `401`s drop the token (keep the Wi-Fi) and enter setup mode. |
| `5xx` / network error / anything else | Keep the current image and the credentials, and retry with backoff (5, 10, 20 … max 120 min). |

The headers report telemetry on every wake, so no extra request is needed: the frame request is
the device's heartbeat. `X-Features` repeats the features (`none` for none), so the API catches up
if it missed or didn't yet know one from a status report.

The answer can ask for more, which the device does after drawing the frame:

| Header | Device action |
| --- | --- |
| `X-Log-Request: 1` | An admin asked for the log: `POST /device/v1/log` with the end of `log.txt` (`text/plain`, up to 8 KB). |
| `X-Update: 1` | An update is waiting: `GET /device/v1/firmware/offer`, then check, download and install it ([ota.md](ota.md)). |

### Status

After any start other than a wake from deep sleep (power-on, RESET, watchdog …), and when the
firmware, panel, features or software change, the device sends `POST /device/v1/status` with the
same body as `register` plus:

```json
{ "batteryMv": 3920, "rssi": -58, "ssid": "Hjemme-2G",
  "model": "nano-esp32", "runtime": "1.0.0", "boot": "1.0.0",
  "ota": { "mode": "official", "keys": ["q1", "q2"] }, "build": "official", "reset": "power_on" }
```

| Field | |
| --- | --- |
| `model` | The board's name in releases. |
| `runtime`, `boot` | The Quiver runtime and its loader; empty on stock MicroPython. |
| `ota.mode` | `official` (Quiver's releases), `custom` (own keys too) or `off`. `ota.keys`: the ids of the keys it trusts. The API offers only releases signed with one of them. |
| `build` | `official` when the app is a release as installed, `custom` when it was changed by hand. |
| `reset` | Why it started: `power_on`, `hard_reset`, `soft_reset`, `watchdog`, `deepsleep`. Logged as a boot event, except `deepsleep`. |

### Events

Errors are queued on the device (`outbox.json`) while it can't reach the API, and sent with the next
wake that gets through, together with the outcome of updates:

```
POST /device/v1/events
{ "events": [ { "type": "error", "msg": "Får ikke kontakt: … (HttpError, 3 in a row)", "data": null },
              { "type": "update", "msg": "app 3.1.0: ok", "data": { "kind": "app", "to": "3.1.0", "result": "ok" } } ] }
```

`type` is `[a-z_]{1,20}`, `msg` up to 300 characters, `data` an object up to 2 KB, at most 20 events.
`error` sets the device's last error; `update` with `result` (`ok`, `failed`, `rolled_back`) its
update state. Events and logs are rate limited per token (30 an hour).

### Removed screens

Removing a screen in the app (`DELETE /api/home/devices/{id}`) and deleting the account
(`DELETE /api/me`) both release the screen: it leaves the home and its token stops working. The API
keeps the hash of that token with the reason, so the screen's next request with it, on `frame` or
`status`, gets:

```
410 Gone
{"error":"device_released","reason":"removed"}          // or "account_deleted"
```

The screen then **resets**: it wipes its Wi-Fi credentials and token, shows why on the setup screen,
and enters setup mode. With no Wi-Fi it can only be set
up again from the app over Bluetooth. Both reasons reset the same way; only the text differs.

Only this answer resets a screen. A token the API never issued, or no longer remembers, gets a plain
`401`, and the screen does not act on that at once: a restored database, a bad deploy or the wrong
`api_url` look the same, and wiping every screen's Wi-Fi then would be far worse than waiting. A
`410` without `device_released` (from a proxy, say) is just an error. Outages and `5xx` never make a
screen forget anything.

When the screen is claimed again, the old token's release is forgotten: it gets `401` from then on.

A released screen learns it on its next wake, so up to its poll interval later (or at the next
button press). A screen that never reaches the API again keeps its Wi-Fi until someone holds its
button for 5 s.

### Buttons and pages

A press wakes the device, which makes the same frame request with one extra header saying which
button it was:

```
GET /device/v1/frame
Authorization: Bearer <deviceToken>
If-None-Match: "<etag of the frame on screen>"
X-Button: next | page-down | page-up
```

| `X-Button` | The API |
| --- | --- |
| `next` | Moves the screen to its next view, in the order of the views on Hjem, wrapping around. The new view starts on its first page. |
| `page-down` | Moves to the view's next page; after the last page, back to the first. |
| `page-up` | Moves to the previous page; before the first, to the last. |

The answer is the frame for the new view and page, which the device draws as usual. When nothing
moves (fewer than two views, or a view with one page) the header changes nothing, and the answer is
the usual `304` or frame. Unknown values are ignored.

**The API keeps the state, not the device.** The screen's current view and page are stored on the
device row (`current_view_id`, `current_page`), so the firmware does no view or page logic: it
doesn't know how many views or pages there are, or which one is showing. That also means the app
sees what the screen shows, and the app picking a view resets the page too. A view's pages come
from its model (how many rows fit). If the data shrinks, a stored
page past the end shows the last page.

- Frames are drawn per request, from the page as part of the ETag, and not stored. Drawing takes
  a few milliseconds, so a press costs about the same as a poll with new data: Wi-Fi association,
  one request, and a possible cold start.
- Ignore further presses until the panel has finished refreshing.
- If the request fails, retry **without** `X-Button`. The API may already have moved on, and a
  missed press is better than skipping a view or page.
- The app can also choose which view shows now; the screen picks that up on its next wake.

The frame contents depend on the device's state:

- A screen with several views gets the frame of the one it shows now, on its current page.
- With no view assigned, the API sends a frame saying so ("Ingen visning valgt. Velg en i appen.").
- If the home's Strava connection is broken, the view is drawn with a small banner.

### Frame format (`application/vnd.quiver.frame`)

Little-endian. Header (16 bytes):

| Offset | Size | Field |
| --- | --- | --- |
| 0 | 4 | magic `RHF1` |
| 4 | 1 | version = `1` |
| 5 | 1 | planes: `1` (black) or `2` (black + red, or two grey planes) |
| 6 | 1 | flags: bit 0 `GRAY` (two planes of grey levels); other bits reserved, `0` |
| 7 | 1 | reserved |
| 8 | 2 | width (px) |
| 10 | 2 | height (px) |
| 12 | 2 | stride (bytes per row = ⌈width / 8⌉) |
| 14 | 2 | reserved |

Then `planes × height × stride` bytes, plane after plane:

- Rows go top to bottom. Within a byte, the **MSB is the leftmost pixel**.
- In the black plane, `1` means black ink. In the red plane, `1` means red ink.
- A pixel set in both planes is red.
- A `GRAY` frame holds a level per pixel, 0 (paper) to 3 (black): the first plane is its high bit
  (dark grey or black), the second its low bit. The first plane alone is the frame in black and
  white. Only devices that report `gray4` get one.

Waveshare drivers usually expect `1 = white` in the black RAM. The firmware then inverts the
black plane (`b ^ 0xFF`) while streaming it to the panel. A 4.2" BWR frame is
`16 + 2 × 300 × 50 = 30 016` bytes. A 7.5" B/W frame is `16 + 480 × 100 = 48 016` bytes.

The frame bytes are a pure function of `(view, settings, panel, data version, local date)`, so the
API computes the `ETag` without rendering, and a `304` costs almost nothing.

## 4. Poll interval

The interval is set per device in the app (5, 15, 30 or 60 min, 4 or 24 hours) and returned as
`X-Poll-Seconds` with every frame, so a new interval takes effect after the next wake. The
API may lower it right after a change, for example when the user saved a view, so the screen
updates sooner.
