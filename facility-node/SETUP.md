# Facility Node: Setup Guide

From a blank Raspberry Pi 5 to a commissioned facility with two doors (male and female).

The node does three jobs. Each one has its own devices, its own section below and its own switch in `.env`, so a site can run all three or only the ones it has hardware for:

| # | Function | Devices | Set up with | Check with | Section |
|---|---|---|---|---|---|
| 1 | **Door lock / unlock** | KC868-A4S relay board, QR scanners, exit buttons, mag locks, reed switches | `facility config`, `facility qr` | `facility doctor door` | [4](#4-door-lock--unlock) |
| 2 | **Camera monitoring** | IP camera | `facility camera` | `facility doctor camera` | [5](#5-camera-monitoring) |
| 3 | **Odour monitoring** | ENS160 + AHT20 (on an ESP32-S3 board or the Pi), optional MQ-135/136 | `facility sensor` | `facility doctor odour` | [6](#6-odour-monitoring) |

Sections 1–3 are shared by all three functions. Follow the steps in order. Each step ends with a check, so stop there if the check fails.

```
                     Router 192.168.10.1
      ┌──────────────┬──────┴───────┬───────────────┬──────────────┬──────────────┐
 QR male .101   QR female .102   Pi 5 .104      KC868 .174     Camera .149   Air board .105
      │  POST /qr      │ POST /qr     │ :5454          │ :80          │ RTSP :554    │ :80 (WiFi)
      └────────────────┴────────────► │ ─ sw_ctl.cgi ─►│              │              │
                                      │ ◄─ input_ctl ──│              │              │
                                      │ ◄──────────── snapshot ───────┘              │
                                      │ ◄──────────────────────── GET /sensor ───────┘
                                      │ GPIO27: male reed   GPIO22: female reed
                                      └─► HTTPS (outbound only) ─► Admin backend

 1 Door:   12V ─► KC868 Relay01 (NC) ─► male timer (NC) ─► male mag lock     (Relay02 / female the same)
           QR / app: the KC868 opens the lock for UNLOCK_SECONDS (set in code)
           Exit buttons: pole 1 ─► that door's timer (own delay),  pole 2 ─► KC868 Input01 / Input02
 2 Camera: RTSP health check every 30 s, frozen-feed check every 5 min, snapshot on door alerts
 3 Odour:  ENS160 ─STEMMA QT─ AHT20 ─STEMMA QT─ ESP32-S3 Feather (or ENS160/AHT20 on the Pi's I2C)
```

---

## 1. Parts checklist

**Shared (every site)**

- [ ] Raspberry Pi 5 (4 GB), 5V/5A USB-C supply, **endurance** microSD (32 GB)
- [ ] Router
- [ ] Mini DC-UPS: powers the Pi, router and camera, **not** the locks

**1. Door lock / unlock**

- [ ] KC868-A4S relay board
- For each door (×2: male and female):
  - [ ] QR scanner with network upload
  - [ ] Timer relay module (e.g. HCW-M421), for the exit button; QR and app unlock times are set in code
  - [ ] 12V magnetic lock (fails open when power is lost)
  - [ ] Dual-pole (DPDT) exit button
  - [ ] MC-38 reed switch (optional; needed for forced-open and propped-open alerts)

**2. Camera monitoring**

- [ ] IP camera with RTSP (already installed)

**3. Odour monitoring**

- [ ] ENS160 + AHT20 sensor modules, either:
  - on an **Adafruit ESP32-S3 Feather** with STEMMA QT cables, plus a 5V USB-C supply (section 6.1, recommended), or
  - wired to the Pi's pins (section 6.2)
- Optional ammonia / H2S sensing (section 6.3):
  - [ ] ADS1115 ADC module
  - [ ] MQ-135 (ammonia) and/or MQ-136 (H2S) module
  - [ ] Resistors for each MQ output: 10 kΩ + 20 kΩ (voltage divider)

**Information to collect first**

| Item | From | For |
|---|---|---|
| Facility ID | Admin backend | all |
| Admin API URL + key | Backend team | all |
| Facility-app Auth Code | Admin backend (per facility) | door |
| Camera username + password (Imou: Safety Code) | Camera label / app | camera |
| 2.4 GHz WiFi name + password | Router | odour (ESP32 board) |

---

## 2. Network: router and IP plan

In the router admin page (usually `http://192.168.10.1`):

1. Set the LAN to `192.168.10.0/24`, with the router at **192.168.10.1**.
2. **Shrink the DHCP pool** so it excludes the device addresses below, e.g. simply `192.168.10.200–250`.
3. Reserve or set these static addresses:

| Device | Function | IP | Port |
|---|---|---|---|
| Router / gateway | — | 192.168.10.1 | — |
| Raspberry Pi 5 | all | 192.168.10.104 | 5454 |
| QR scanner, **male** | door | 192.168.10.101 | sends to Pi :5454 |
| QR scanner, **female** | door | 192.168.10.102 | sends to Pi :5454 |
| KC868-A4S | door | 192.168.10.174 | 80 |
| IP camera | camera | 192.168.10.149 | 554 |
| Air sensor board (ESP32-S3, if used) | odour | 192.168.10.105 | 80 |

4. Change the router's admin password from the default.
5. **No port forwarding** to anything. The Pi only makes outbound connections.

✅ **Check:** a phone on the WiFi gets an address from the pool, not one of the reserved addresses above.

> A scanner that ends up on the wrong IP is rejected (`403 unknown_scanner`). That's on purpose: the Pi uses the scanner's IP to decide which door to open.

---

## 3. Raspberry Pi: OS, install and functions

### 3.1 OS and install

1. Follow **[INSTALL.md](INSTALL.md) sections 1–4**: flash Raspberry Pi OS Lite (64-bit), first login, static IP `192.168.10.104` (gateway `192.168.10.1`), OS update.
2. Follow **[INSTALL.md](INSTALL.md) sections 5–10**: copy the code, `sudo bash deploy/install.sh`, `facility config`, `facility doctor`, reboot test.

✅ **Check:** `ssh pi@192.168.10.104` works and `facility status` shows the service active.

### 3.2 Power

- Mini DC-UPS → Pi, router, camera.
- Mag locks stay on their own 12V supply through the timer modules. They fail open on a power cut, which is the safe behaviour for a public facility.

### 3.3 Choose the functions

All three functions are on by default. Switch off the ones this site doesn't have, so the node doesn't check (and complain about) hardware that isn't there:

```bash
facility feature                 # door on, camera on, odour on
facility feature camera off      # no camera here
facility feature camera on       # back on: restarts and checks it
```

| `.env` | Off means |
|---|---|
| `ENABLE_DOORS=0` | No relay, exit-input or reed jobs, no token pull; the unlock endpoints answer `404 doors_off`. `RELAY_URL` / `RELAY_PWD` may stay `CHANGE_ME` |
| `ENABLE_CAMERA=0` | No camera checks or snapshots; door alerts go out without a picture. `CAM_*` may stay unset |
| `ENABLE_ODOUR=0` | No sensor reads or odour alerts; the 1-minute push sends `null` air values |

At least one must stay on. Each function's settings stay in `.env` when it is off, so switching back on needs no re-setup.

✅ **Check:** `facility doctor` → `functions - doors on, camera on, odour on` (or what you chose), and each section below its own heading.

---

## 4. Door lock / unlock

**Devices:** KC868-A4S relay board (.174), two QR scanners (.101, .102), timer modules, mag locks, DPDT exit buttons, optional MC-38 reed switches.
**Code:** `door/` · **Check:** `facility doctor door`

### 4.1 Wiring

A mag lock is **locked while it has power**. Each door's 12V lock power runs through **two NC (normally closed) contacts in series**: the KC868 relay and the timer module's relay. **Either one** opening cuts the power and opens the door:

```
 12V + ──► KC868 Relay01 COM→NC ──► timer module relay COM→NC ──► male mag lock ──► 12V −
              ▲                                ▲
   QR / app: the Pi asks the KC868      Exit button pole 1 ──► timer trigger
   to open for UNLOCK_SECONDS           (the timer's own delay; works with the Pi off)
```

| Event | What cuts the lock power | For how long |
|---|---|---|
| QR scan / app button | KC868 relay | `DOOR_<NAME>_UNLOCK_SECONDS` (default 5 s), set in code: `facility door delay male 8` |
| Exit button | Timer module | The timer's own delay, set on its buttons (e.g. 5 s) |
| Pi or KC868 off / crashed | — | Nothing: the door stays locked, and the exit button still works |
| Power cut | — | The lock has no power: it opens (fail-safe) |

The KC868 releases its relay by itself after the unlock time, even if the Pi or the network drops mid-unlock.

**KC868-A4S**

| KC868 terminal | Goes to |
|---|---|
| Relay01 COM / **NC** | **Male** lock power, in series with the male timer's relay |
| Relay02 COM / **NC** | **Female** lock power, in series with the female timer's relay |
| Input01 + GND | **Male** exit button, pole 2 (dry contact) |
| Input02 + GND | **Female** exit button, pole 2 (dry contact) |

**Timer module (each door, e.g. HCW-M421)**

- Its relay **COM / NC** goes in series in the lock power line (above), so the lock is powered while the timer is idle.
- Trigger ← exit button pole 1. Mode: triggered → relay on for the set time (on the HCW-M421, mode **P-1**), time e.g. **5 s**.
- Only the exit button uses it. QR and app unlock times are set in code, not on the timer.

**Exit buttons (each door)**

- **Pole 1** → that door's timer trigger. This opens the door in hardware, even with the Pi off.
- **Pole 2** → that door's KC868 input. This only lets the Pi *log* the exit.

**Reed switches (optional), on the Pi header**

| Signal | Pi physical pin | BCM |
|---|---|---|
| **Male** reed, wire A | 13 | **GPIO27** |
| **Male** reed, wire B | 14 | GND |
| **Female** reed, wire A | 15 | **GPIO22** |
| **Female** reed, wire B | 20 | GND |

The reed switches go directly to the GPIO pins. No resistor is needed (the code enables the internal pull-up), and no optocoupler.
Mount each reed on the door's opening edge, **at least 10 cm from the mag lock**.

✅ **Check:**
- With the Pi powered off, pressing each exit button releases its own door for the timer's delay.
- With the Pi off, both doors stay **locked** (if one is open, its KC868 contact is on NO instead of NC).

### 4.2 KC868-A4S firmware

The board needs the patched `firmware/kc868/main.py`. It adds the unlock time (`hold`, up to 30 s, released by the board itself), exit-input reporting, returns 401 on a wrong password, and moves the password out of the code.

**1. Tools.** On your laptop, install `mpremote` and connect the board over USB:

```bash
pip install mpremote
mpremote connect list
```

**2. Find the input chip address.**

```bash
mpremote connect /dev/ttyUSB0 exec "import machine; i2c=machine.I2C(0,scl=machine.Pin(16),sda=machine.Pin(4)); print([hex(a) for a in i2c.scan()])"
```

You'll see something like `['0x22', '0x24']`. **0x24 is the relays; the other one is the inputs.**
Open `firmware/kc868/main.py` and set:

```python
PCF8574_INPUT_ADDR = 0x22   # the non-0x24 address you saw
```

**3. Set the board password.**

```bash
cd facility-node/firmware/kc868
cp board_secrets.py.example board_secrets.py
python3 -c "import secrets; print(secrets.token_urlsafe(18))"   # copy this
nano board_secrets.py                                           # POST_PASSWORD = "<paste>"
```

Keep this password. It goes into the Pi's `.env` as `RELAY_PWD`. **Never commit `board_secrets.py`.**

**4. Upload and restart.**

```bash
mpremote connect /dev/ttyUSB0 cp main.py :main.py + cp board_secrets.py :board_secrets.py + reset
```

**5. Test from any machine on the LAN.**

```bash
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=<PASSWORD>&Relay01=ON&hold=5"   # male door opens 5 s
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=<PASSWORD>&Relay02=ON&hold=5"   # female door opens 5 s
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=wrong&Relay01=ON"        # 401, nothing clicks
curl "http://192.168.10.174/input_ctl.cgi?postpwd=<PASSWORD>"              # hold male exit button
```

✅ **Check:**
- Each relay releases its own door, for the `hold` time.
- `input_ctl.cgi` ends its relay list with `MaxHold=30` (the new firmware).
- A wrong password returns `401`.
- `Input01=ON` while the male exit button is held, and `Input02=ON` for the female one.
- If pressed reads `OFF` and released reads `ON`, delete the `not` in `read_inputs()` and upload again.

### 4.3 Door settings

In `.env` (`facility config`):

```ini
ENABLE_DOORS=1
RELAY_URL=http://192.168.10.174
RELAY_PWD=<the board's POST_PASSWORD>
DOORS=male,female
DOOR_MALE_RELAY=1
DOOR_MALE_EXIT_INPUT=0
DOOR_MALE_REED_GPIO=27
DOOR_MALE_SCANNER_IPS=192.168.10.101
DOOR_FEMALE_RELAY=2
DOOR_FEMALE_EXIT_INPUT=1
DOOR_FEMALE_REED_GPIO=22
DOOR_FEMALE_SCANNER_IPS=192.168.10.102
# QR / app unlock time in seconds (1-30)
DOOR_MALE_UNLOCK_SECONDS=5
DOOR_FEMALE_UNLOCK_SECONDS=5
# 0 until the reed switches are fitted
REED=1
```

**Change the unlock time** at any time, without editing the file:

```bash
facility door                   # each door's relay and unlock time
facility door delay male 8      # male door opens 8 s per QR / app unlock
facility door delay all 5       # every door
```

It saves the time, restarts the service and shows the new value. It never fires a relay: check it with a real scan.

✅ **Check:** `facility doctor door` shows both doors with their unlock time, `reachable, password accepted` and `unlock time supported` for the relay, and `0 problem(s)`.

### 4.4 QR scanners

On each scanner's configuration page or tool:

| Setting | Male scanner | Female scanner |
|---|---|---|
| IP | 192.168.10.101 | 192.168.10.102 |
| Netmask | 255.255.255.0 | 255.255.255.0 |
| Gateway | 192.168.10.1 | 192.168.10.1 |
| Mode | HTTP upload / POST | HTTP upload / POST |
| Server URL | `http://192.168.10.104:5454/qr` | `http://192.168.10.104:5454/qr` |

If the scanner's firmware has a fixed path, point it at the matching compatible route instead:

| Scanner sends | Use URL |
|---|---|
| `?cardid=...` (GET or POST) | `http://192.168.10.104:5454/qrscanner` |
| `{"SCode": "..."}` (Rakinda) | `http://192.168.10.104:5454/rakindaqrscanner` |
| JSON `{token}`, a form field, or plain text | `http://192.168.10.104:5454/qr` |

All three routes run the same checks. The door is chosen by the scanner's IP.

**What the scanner shows.** Scanners with a display (Rakinda: `Msg`) show why a scan was refused:

| Situation | Default message | `.env` |
|---|---|---|
| Opened | Please Enter the facility | `SCANNER_MSG_OPENED` |
| No balance (paid entry) | Insufficient balance, please top up | `SCANNER_MSG_NO_BALANCE` |
| No internet (paid entry) | No internet, please try again | `SCANNER_MSG_OFFLINE` |
| Any other refusal | Access denied | `SCANNER_MSG_DENIED` |
| Relay failed | Door error, please call the attendant | `SCANNER_MSG_ERROR` |

Change the texts in `.env` (e.g. to Bangla). Keep them short, and use plain Latin letters if the scanner shows boxes instead of other scripts.

✅ **Check:** scan any QR and watch `journalctl -u facility-node -f`:

| Log shows | Meaning |
|---|---|
| `[male] QR denied` | Scanner reaches the Pi. The code just isn't valid. Good. |
| `scan from unknown scanner 192.168.10.x` | The scanner's IP doesn't match `.env`. Fix the IP. |
| nothing | Wrong server URL, or the scanner isn't in HTTP mode |

### 4.5 Unlock QR codes (test, staff, commissioning)

User QR codes come from the admin backend automatically. Use `facility qr` for codes made **on the Pi**, such as a test code for commissioning, or codes for cleaners and staff.
Any valid code opens **either** door. Pi-made codes show up in the access log as `source: qr_local`.

```bash
facility qr create --label test                          # 1 code, valid 24 h
facility qr create --label cleaners --count 5 --hours 720  # 5 codes, valid 30 days
facility qr list
facility qr revoke --label test                          # or --token <t> / --expired / --all
```

Each code:
- prints as a QR in the terminal, so you can scan it straight off the laptop screen;
- is saved as PNG + SVG in `/var/lib/facility/qr/`. Copy them off with
  `scp pi@192.168.10.104:/var/lib/facility/qr/*.png .`

**Showing the code at the scanner:** open the PNG on a phone at full brightness, or print it at least 3 cm wide. Hold it 10–20 cm from the scanner.

> **Treat every code like a key.** Anyone with a photo of it can open the doors until it expires.
> Prefer short `--hours`, revoke codes as soon as they're no longer needed, and avoid `--no-expiry`.

**Registering a code made somewhere else.** If you were given a QR image along with its token, register that exact token:

```bash
facility qr create --label test --hours 24 --token <token>
```

### 4.6 Online QR validation and paid entry

By default the Pi checks codes against the token list it pulls every 5 minutes. To also ask your backend live, for example for tickets bought a minute ago or for instant revocation, set `QR_API_*` in `.env`.

**Paid entry (the backend charges per scan).** Use `QR_API_ORDER=api_only`: every scan goes to the backend, which checks the user's balance and charges in the same call. The Pi opens only on its "yes":

| Situation | QR scan |
|---|---|
| Internet up, enough balance | Opens; `entry` logged with `scan_id` |
| Internet up, no balance | Refused; `denied`, `api_reason: insufficient_balance` |
| **No internet / backend down** | **Refused**; `denied`, `reason: offline` |
| Scanner sends the same code again within 10 s | Same scan: no second charge, the door just stays open |
| Backend charged, but the relay failed | `relay_failed` with `scan_id` is sent so the backend refunds |
| Pi-made staff code (`facility qr`), Facility app, exit button | Open as always, also offline (no charge) |

```ini
QR_API_URL=/qr/validate          # the backend's check-and-charge API
QR_API_ORDER=api_only
QR_API_TIMEOUT=3
```

Because paid entry now needs the internet, give the router a **backup connection (4G/LTE failover SIM)**, and watch `facility health` → `qr_api.last_ok` / `last_error`. The backend must charge each `scan_id` only once and refund on `relay_failed` (README section 8, "Paid entry").

**Match your backend's API**

| Your backend… | `.env` |
|---|---|
| URL | `QR_API_URL=/qr/validate` (under `ADMIN_URL`) or a full `https://…` URL |
| Takes JSON POST / query GET | `QR_API_METHOD=POST` / `GET` |
| Calls the code field `card_id` | `QR_API_TOKEN_FIELD=card_id` |
| Doesn't want facility / door | `QR_API_FACILITY_FIELD=` / `QR_API_DOOR_FIELD=` |
| Needs a fixed field | `QR_API_EXTRA={"device_token":"abc"}` |
| Replies `{"data":{"access_granted":true}}` | `QR_API_ALLOW_FIELD=data.access_granted` |
| Replies `{"status":"granted"}` | `QR_API_ALLOW_FIELD=status` (`granted` is already an allow value) |
| Refuses with HTTP 403 | Already counted as "refused" (`QR_API_DENY_STATUS`) |
| Has its own key, not the admin key | `QR_API_AUTH=0` and put the key in `QR_API_EXTRA` |
| Refuses with HTTP 402 and `{"reason": "insufficient_balance"}` | Already handled (402 is a refusal; `QR_API_REASON_FIELD=reason`) |
| Calls the scan id `request_id` | `QR_API_SCAN_ID_FIELD=request_id` (it is always sent as `Idempotency-Key` too) |

**Pick the order**

- **`QR_API_ORDER=local_first`** (recommended): codes in the cache open instantly, and only unknown codes go to the API. With the internet down, every cached code still opens.
- **`QR_API_ORDER=api_first`:** every scan asks the API first, and a "no" from the API beats the cache, so revocation takes effect immediately. If the API doesn't answer within `QR_API_TIMEOUT` (3 s), the cache decides. Each scan can wait up to that long.
- **`QR_API_ORDER=api_only`:** paid entry, as above. The API decides every scan, and without it no QR opens.

Codes made with `facility qr` always open locally, in both modes.

**Test it now with the dummy API**

The repo ships a dummy validation server and test QR codes in `dev/`. Run the dummy on the Pi, in a second SSH session:

```bash
cd /opt/facility-node && sudo -u facility ./venv/bin/python dev/dummy_qr_api.py
```

(The installer copies `dev/` to the Pi; the QR images are in `/opt/facility-node/dev/qr/`.)

In `.env`, set the following and restart the service:

```ini
QR_API_URL=http://127.0.0.1:8090/qr/validate
QR_API_AUTH=0
```

Then show the codes at a scanner:

| QR image (`dev/qr/`) | Expected |
|---|---|
| `DUMMY-ALLOW-0001.png` | Door opens; dummy prints `ALLOW`; log `source: qr_api` |
| `DUMMY-DENY-0001.png` | Stays locked; dummy prints `DENY`; `reason: api_denied` |
| Any of them, with the dummy stopped (Ctrl-C) | Stays locked; the log shows `QR API unavailable … using local cache` |

The dummy prints every request exactly as the Pi sends it, which is handy for agreeing the format with your backend team.

> ⚠️ **Remove the dummy before handover.** It approves well-known codes. Set `QR_API_URL` to the real backend (or leave it empty) and stop the dummy. The node logs a warning whenever `QR_API_URL` points at `127.0.0.1` or `localhost`.

### 4.7 Facility app (attendant phones)

In the app's settings:

| Setting | Value |
|---|---|
| Endpoint URL | `http://192.168.10.104` |
| Port | `5454` |
| Auth Code | the facility's Auth Code (from the admin backend), **not** the Facility ID |

The app's button has two choices, and sends one of these:

```http
POST /facility/open
Content-Type: application/json

{"authCode": "<auth code>", "door": "male", "use": "customer", "amount": 20}
{"authCode": "<auth code>", "door": "female", "use": "personal", "purpose": "cleaning"}
```

| Field | Values | Meaning |
|---|---|---|
| `door` | `male` / `female` | Which door opens |
| `use` | `customer` | A customer paid **cash** at the counter: logged as a cash sale |
| | `personal` | The attendant's own use: needs `purpose` |
| `purpose` | `cleaning`, `bath`, `toilet`, `maintenance` | Personal use only; the list is `ATTENDANT_PURPOSES` in `.env` |
| `amount` | number, optional | Customer use: the cash taken, for the backend's sales total |
| `attendant` | text, optional | Who pressed it (name or ID), for the log |

It works **offline**: the door opens on the Auth Code alone and the event reaches the backend later. A wrong `use`, a missing or unknown `purpose`, or a bad `amount` gets `400` with `reason` (`bad_use`, `bad_purpose`, `bad_amount`) and opens nothing. An older app that sends no `use` still opens and is logged as before.

Five wrong Auth Codes in 60s from one phone block that phone for a minute.

### 4.8 Door tests

Run every test for **both doors**. Tick each column.

| # | Test | Expected | Male | Female |
|---|---|---|---|---|
| D1 | `curl sw_ctl.cgi … RelayNN=ON` | Only this door releases | ☐ | ☐ |
| D2 | Hold exit button, `curl input_ctl.cgi` | Only this door's `InputNN=ON` | ☐ | ☐ |
| D3 | Show a valid QR (section 4.5) | This door opens for its unlock time (time it); `entry` logged with `section` | ☐ | ☐ |
| D4 | Show an invalid QR | Stays locked; `denied` logged | ☐ | ☐ |
| D5 | Facility app open with `door` set | Only that door opens; `attendant` logged | ☐ | ☐ |
| D5a | App: customer use (cash), then personal use for cleaning | Both open; logged with `use: customer, payment: cash` and `use: personal, purpose: cleaning` | ☐ | ☐ |
| D5b | *Paid entry:* scan a code with balance, then one without | Opens once, balance goes down by one; the second is refused with `insufficient_balance`, scanner shows "Insufficient balance…" | ☐ | ☐ |
| D5c | *Paid entry:* unplug the router WAN, scan a paid code | Refused (`reason: offline`), scanner shows "No internet…"; a staff code and the app still open | ☐ | ☐ |
| D6 | Press exit button | Door opens for the timer's delay (hardware); `exit` logged once | ☐ | ☐ |
| D6b | `facility door delay <door> 10`, then D3 again | Opens for 10 s; set it back afterwards | ☐ | ☐ |
| D7 | Open the door by hand, no trigger (reed fitted) | `forced_open` alert (+ snapshot if the camera is on) | ☐ | ☐ |
| D8 | Hold the door open > 5 min | `propped_open`, repeats every 3 min, `propped_resolved` on close | ☐ | ☐ |
| D9 | Pi powered off, press exit button | Door still releases (hardware fail-safe) | ☐ | ☐ |

Then **revoke the test codes**: `facility qr revoke --label test`.

### 4.9 Door troubleshooting

| Symptom | Cause / fix |
|---|---|
| `relay channel used by more than one door` | Two doors share a relay, input, GPIO or scanner IP in `.env` |
| Scan → `unknown scanner` | The scanner's IP isn't the one in `DOOR_*_SCANNER_IPS` (check the DHCP pool) |
| Scan → nothing in the log | Wrong scanner URL or mode; try `curl -XPOST http://192.168.10.104:5454/qr -d test` from the LAN |
| Scan → `404 doors_off` | `ENABLE_DOORS=0`: `facility feature door on` |
| Valid QR denied | The code expired, or the token pull hasn't run (`/health` → `tokens`, `last_pull`) |
| New tickets denied until the next pull | Turn on online validation (section 4.6) |
| Paid QR refused with `reason: offline` | No internet or backend down (`facility health` → `qr_api.last_error`). By design with `api_only`; staff codes and the app still open |
| Paid QR refused with `api_reason: insufficient_balance` | The user must top up; the backend decides |
| Charged twice for one entry | The backend isn't de-duplicating `scan_id` / `Idempotency-Key` (README section 8) |
| `QR API unavailable` in the log | Wrong `QR_API_URL`, backend down, or a 401 (check `QR_API_AUTH` / the key). `/health` → `qr_api.last_error` |
| API always denies | `QR_API_ALLOW_FIELD` doesn't match the reply. Run the dummy API to see the request, and compare with the backend's real reply |
| Door doesn't open, log shows `relay failed … wrong relay password` | `RELAY_PWD` ≠ the board's `POST_PASSWORD` |
| Door opens only ~1 s, whatever the unlock time | Old KC868 firmware ignores `hold`: `facility doctor door` says `firmware has no unlock time`. Flash it (section 4.2) |
| Door stays open while the Pi is off, or never locks | A KC868 or timer contact is wired to **NO** instead of **NC** (section 4.1) |
| `unlock time … not in 1-30 s` at start | `DOOR_*_UNLOCK_SECONDS` out of range: `facility door delay <door> 5` |
| Door opens but no `exit` logged | `relay firmware does not report inputs` → firmware not flashed, wrong `PCF8574_INPUT_ADDR`, or polarity (section 4.2, step 5) |
| Wrong door logs the exit | `DOOR_*_EXIT_INPUT` swapped |
| False `forced_open` alerts | Reed too close to the mag lock, or polarity: set `REED_ACTIVE_HIGH=1` |
| Facility app 400 `unknown_door` | The app isn't sending `door` |
| Facility app 429 | Too many wrong Auth Codes; wait a minute and check the code |

---

## 5. Camera monitoring

**Devices:** IP camera (.149, RTSP port 554).
**Code:** `camera/` · **Check:** `facility doctor camera`

What the node watches: **reachable** (RTSP port answers, every 30 s), **streaming** and **frozen** (two frames 3 s apart differ, every 5 min). Both ride along with the 1-minute sensor push as `camera`. When doors are on, it also takes a **snapshot** on door alerts.

### 5.1 Connect the camera

1. Give the camera a fixed IP (`192.168.10.149`): reserve it for the camera's MAC in the router.
2. Run `facility camera` on the Pi. It asks for the IP, username, password (hidden) and brand, **tests the stream first**, then saves `CAM_HOST` / `CAM_RTSP` and restarts the service.
   - **Imou / Dahua:** user `admin`; the password is the **Safety Code (SC)** on the label, or the Device Password set in the Imou app. **Not** the Imou app login. These cameras have no web page.
   - `401 Unauthorized` = wrong user or password. Stop after 2–3 tries: the camera locks its login for ~30 min.
3. Check: `facility camera test` → e.g. `OK: hevc 2560x1440 @ 25 fps`, and `facility health` → `camera.streaming: true`.

| Command | Does |
|---|---|
| `facility camera` | Set up or change the camera (asks, tests, saves, restarts) |
| `facility camera test` | Test the saved stream: codec, resolution, fps |
| `facility camera show` | Saved settings, password hidden |
| `facility camera find` | Every device with RTSP open, plus model and serial number for Dahua/Imou |

### 5.2 Snapshots

- **When:** on `forced_open` and on the first `propped_open` alert (door function).
- **Where:** saved to `/var/lib/facility/snapshots/` and deleted after `SNAPSHOT_KEEP_DAYS` (default 7).
- **Upload:** set `SNAPSHOT_UPLOAD_PATH` in `.env` once the backend has an upload endpoint. The Pi sends a multipart POST with field `file` and form fields `facility`, `name`, `ts`, using the same auth header as the other APIs.
- **Matching:** the upload is queued **before** its alert, so the image arrives first. The alert's `snapshot` field is the file name.

Copy snapshots off by hand:

```bash
scp pi@192.168.10.104:/var/lib/facility/snapshots/*.jpg .
```

### 5.3 Camera tests

| # | Test | Expected | ☐ |
|---|---|---|---|
| C1 | `facility camera test` | `OK: <codec> <resolution> @ <fps>` | ☐ |
| C2 | `facility health` after a minute | `camera.reachable: true`, `streaming: true`, `frozen: false` | ☐ |
| C3 | Unplug the camera, wait 1 min | `reachable: false`; doors still open normally | ☐ |
| C4 | Plug it back in, wait 5 min | `reachable: true`, `streaming: true` again | ☐ |
| C5 | Door test D7 (forced open) | A JPEG appears in `/var/lib/facility/snapshots/` | ☐ |

### 5.4 Camera troubleshooting

| Symptom | Cause / fix |
|---|---|
| Camera `reachable:false` | Wrong `CAM_HOST`, camera off or on another IP: `facility camera find` |
| `streaming:false`, `reachable:true` | Wrong password or stream path: `facility camera test` shows the reason (401 / 404) |
| `401 Unauthorized` | Wrong user or password. Stop after 2–3 tries (the camera locks for ~30 min) |
| `camera: null` in `/health` | `ENABLE_CAMERA=0`: `facility feature camera on` |
| Snapshots not reaching admin | `SNAPSHOT_UPLOAD_PATH` empty, or the backend rejects the upload (look for `snapshot` in `journalctl`) |

---

## 6. Odour monitoring

**Devices:** ENS160 (TVOC, eCO2, AQI) + AHT20 (temperature, humidity), on an ESP32-S3 Feather (.105, WiFi) **or** the Pi's I2C pins; optional MQ-135/MQ-136.
**Code:** `odour/`, `firmware/air-sensor/` · **Check:** `facility doctor odour`

Pick **one** place for the ENS160 + AHT20: the WiFi board (6.1, recommended: it can hang near the toilets, away from the Pi) or the Pi's pins (6.2).

### 6.1 Air sensor board (ESP32-S3 Feather, recommended)

```
 ENS160 ──STEMMA QT── AHT20 ──STEMMA QT── ESP32-S3 Feather ··· WiFi ··· router ─── Pi
                                           192.168.10.105  GET /sensor   (Pi polls every 60 s)
```

**You need:** the Feather with both sensors plugged into its STEMMA QT port, a USB-C **data** cable, the **2.4 GHz** WiFi name and password, and internet on the Pi (first time only). Run every command **without sudo**; it asks for the Pi password only where it must.

**1. Flash and connect (once per board)**

1. Plug the Feather into the Pi with the USB-C cable.
2. Run:
   ```bash
   facility sensor flash
   ```
   It installs the tools, puts the board in bootloader mode by itself, erases it, installs MicroPython, checks both sensors are found (`0x38`, `0x53`), then asks for:
   - **WiFi name**: exactly as shown (it lists the 2.4 GHz networks it can see);
   - **WiFi password**: hidden, never saved on the Pi;
   - **Board IP**: press Enter for `192.168.10.105`.

   It finishes by reading the board over WiFi, e.g. `temperature 24.6 C   humidity 72 %`.
3. If it says *can't reach the bootloader*: **hold BOOT, press and release RESET, release BOOT**, then run it again.

**2. Point the node at the board**

```bash
facility sensor enable
```

This checks the board answers, sets `SENSOR_URL=http://192.168.10.105/sensor` in `.env` (backup in `.env.bak-sensor`) and restarts the service. The log then shows `air sensors: http://192.168.10.105/sensor`.

**3. Mount it**

Unplug it from the Pi and power it from any 5V USB-C supply, high on the toilet-area wall, away from vents, the door and water. It starts and joins the WiFi by itself after every power cut.

The board has a fixed IP, so it may not appear in the router's device list; that's normal. Make sure the router's **DHCP pool excludes `.105`** (e.g. pool `.200–.250`).

**4. Check it**

| Command | Shows |
|---|---|
| `facility sensor test` | The board's live reading: temperature, humidity, ENS160 state, TVOC + score |
| `facility doctor odour` | "Odour: air sensor board" section: reachable, AHT20, ENS160 |
| `facility health` | `odour.tvoc_score` / `tvoc_level` as the service sees them |

**ENS160 timing:** a **new** sensor reports *initial start-up* for **about 1 hour** (TVOC empty, which is normal), then *warming up* for ~3 min after each power-on. Readings settle over the first 24–48 h.

**5. Later changes**

| Task | Command |
|---|---|
| New WiFi name or password | `facility sensor upload` (USB again) |
| Firmware update after `git pull` | `facility sensor upload` |
| Go back to sensors wired to the Pi | `facility sensor disable` |

<details><summary>Manual steps (without <code>facility sensor</code>, e.g. from a laptop)</summary>

```bash
python3 -m venv ~/esp && ~/esp/bin/pip install esptool mpremote
# MicroPython: https://micropython.org/download/ESP32_GENERIC_S3/ (standard build, v1.26 or newer)
~/esp/bin/esptool --chip esp32s3 --port /dev/ttyACM0 erase-flash
~/esp/bin/esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0 ESP32_GENERIC_S3-*.bin
cd facility-node/firmware/air-sensor
cp board_secrets.py.example board_secrets.py && nano board_secrets.py
~/esp/bin/mpremote connect /dev/ttyACM0 cp board_secrets.py :board_secrets.py + cp main.py :main.py + reset
rm board_secrets.py
```

Then set `SENSOR_URL=http://192.168.10.105/sensor` with `facility config`.
</details>

### 6.2 Or: sensors on the Pi's I2C

Leave `SENSOR_URL` empty and wire the ENS160 + AHT20 to the Pi header:

| Signal | Pi physical pin | BCM |
|---|---|---|
| Sensor 3.3V | 1 | — |
| Sensor SDA | 3 | GPIO2 |
| Sensor SCL | 5 | GPIO3 |
| Sensor GND | 9 | — |

✅ **Check:** `sudo i2cdetect -y 1` shows `38` and `53`; `facility doctor odour` lists both.

### 6.3 Optional MQ-135 / MQ-136 gas sensors

**Wiring** (ADS1115 on the Pi's I2C, the same bus as 6.2):

| Signal | Connect to |
|---|---|
| ADS1115 VDD | Pi **3.3V** (pin 1/17). **Not** 5V: the Pi's I2C runs at 3.3V |
| ADS1115 GND, ADDR | GND (ADDR to GND = address 0x48) |
| ADS1115 SDA / SCL | Pi pin 3 / pin 5 (shared with the other sensors) |
| MQ-135 / MQ-136 VCC | Pi **5V** (pin 2/4). The heater needs 5V, about 150 mA each |
| MQ GND | GND |
| MQ-135 AOUT | **10 kΩ** → ADS1115 **A0**, and **20 kΩ** from A0 to GND |
| MQ-136 AOUT | **10 kΩ** → ADS1115 **A1**, and **20 kΩ** from A1 to GND |

The divider scales the MQ's 0–5V output to 0–3.33V, which is safe for the ADS1115. Without it, readings clip and the ADC can be damaged.
Mount the MQ sensors high on the wall in the toilet area, away from the air vents and the door.

**Calibrate (once, after 24–48 h burn-in)**

1. `facility config`, set `MQ=1`, save.
2. Air the room out: door open, no people, no cleaning products for 30 min.
3. Run:

```bash
facility mq read          # Rs should be steady, not "no signal"
facility mq calibrate     # samples for 3 min
```

4. `facility config`, paste the printed `MQ135_R0=` / `MQ136_R0=` lines, save (the service restarts).

Until R0 is set, `nh3_ppm` and `h2s_ppm` stay `null`. Check `MQ_RL_KOHM` against the small resistor on the MQ board: `103` = 10 kΩ, `102` = 1 kΩ.

### 6.4 TVOC score and odour alert

**What's measured**

| Sensor | Values | Notes |
|---|---|---|
| ENS160 | `tvoc` (ppb), `eco2` (ppm), `aqi` (1–5) | General gases. A good *trend* signal, but not specific to toilet smells |
| (Pi) | `tvoc_score` (0–100), `tvoc_level` | TVOC on a 0–100 scale, see below |
| AHT20 | `temperature`, `humidity` | Also used to correct the ENS160's readings |
| MQ-135 (optional) | `nh3_ppm` | Ammonia, the main urine smell |
| MQ-136 (optional) | `h2s_ppm` | Hydrogen sulphide, the "rotten egg" smell |

**Warm-up**

- **ENS160:** about 1 h initial start-up when new, then about 3 min after each power-on; readings settle over 24–48 h. It sends `null` until it's ready.
- **MQ sensors:** 24–48 h burn-in when new, then about 5 min of heater warm-up after each boot.

**TVOC score (0–100)**

The Pi turns TVOC (ppb) into a score, using the German UBA TVOC levels (the same scale the ENS160's AQI uses):

| Score | Level (`tvoc_level`) | TVOC (ppb) |
|---|---|---|
| 0–20 | `very_good` | 0–220 |
| 21–40 | `good` | 220–660 |
| 41–60 | `moderate` | 660–2200 |
| 61–80 | `dangerous` | 2200–5500 |
| 81–100 | `very_dangerous` | 5500–11000 and above (capped at 100) |

See it live with `facility health` → `odour.tvoc_score` / `odour.tvoc_level`.

**Odour alert**

| `.env` | Default | Meaning |
|---|---|---|
| `ODOUR_TVOC_SCORE_LIMIT` | 61 | TVOC score 0–100; 61 = alert from `dangerous` on; empty = off |
| `ODOUR_TVOC_LIMIT` | empty | raw TVOC in ppb instead of the score; empty = off |
| `ODOUR_AQI_LIMIT` | 4 | 1–5; empty = off. Alerts on its own, even when the score is lower: leave it empty if only the score should decide |
| `ODOUR_NH3_LIMIT` | 10 | ppm; empty = off |
| `ODOUR_H2S_LIMIT` | 1 | ppm; empty = off |
| `ODOUR_HOLD` | 600 | Seconds above a limit before `odour_high` |
| `ODOUR_REPEAT` | 1800 | Repeat `odour_high` (`repeat:true`) while still high |
| `ODOUR_CLEAR_HOLD` | 300 | Seconds below every limit before `odour_resolved` |

Any one metric at or above its limit counts as "high".

**Tune the limits per site over the first week:**
1. Note the time whenever a cleaner reports the toilet "needs cleaning".
2. Compare against the readings in the admin panel.
3. Set each limit just below the values seen at those times.

The defaults are only starting points.

✅ **Check:** `facility health` → `odour.limits` lists the metrics you turned on.

### 6.5 Odour tests

| # | Test | Expected | ☐ |
|---|---|---|---|
| O1 | `facility sensor test` (board) or `facility doctor odour` | Temperature, humidity, `ENS160 ready`, TVOC + score | ☐ |
| O2 | `facility health` after 1 min | `odour.tvoc_score` and `tvoc_level` filled in | ☐ |
| O3 | Hold hand sanitiser ~10 cm from the ENS160 for 30 s | TVOC and score jump within a minute, fall back within minutes | ☐ |
| O4 | Unplug the board, wait 2 min | Readings `null`; doors still open normally | ☐ |
| O5 | Plug it back in | Readings return by themselves (after ~3 min ENS160 warm-up) | ☐ |

### 6.6 Odour troubleshooting

| Symptom | Cause / fix |
|---|---|
| `no board on USB` | Charge-only cable, or not plugged in. Try another USB-C cable |
| A sensor missing in the I2C list | STEMMA QT cable not fully pushed in, or ENS160/AHT20 chain broken |
| `no answer from 192.168.10.105` after upload | Wrong WiFi name or password (case-sensitive), or 5 GHz-only WiFi. See the board's log with the `repl` command it prints, then `facility sensor upload` |
| `facility sensor test` works, `facility doctor` says not reachable | `SENSOR_URL` not set: `facility sensor enable` |
| Readings `null` in the backend | Board unplugged or out of WiFi range (it reconnects by itself), ENS160 still warming up, or `ENABLE_ODOUR=0` |
| Sensors `null` (Pi I2C) | I2C off or wiring: `sudo i2cdetect -y 1` should show 38 and 53 |
| `nh3_ppm` / `h2s_ppm` always `null` | `MQ=0`, R0 not set (run `facility mq calibrate`), or no `48` in `i2cdetect` |
| MQ `Rs` "no signal" | AOUT not reaching the ADS1115 channel, or the divider is wired backwards |
| ppm wildly high | `MQ_RL_KOHM` wrong for your board, or calibrated in dirty air: recalibrate |
| Odour alert never fires | Limits too high, or the metric's limit is empty; check `/health` → `odour.limits` |
| Odour alert fires while the score is low | `ODOUR_AQI_LIMIT` triggered it (AQI 4 is common indoors): raise it to 5 or leave it empty |
| Odour alert fires constantly | Limits too low for this site; raise them after a week of data |

---

## 7. Test against a dummy backend

Before the real backend exists, run the dummy admin backend on the Pi. It answers every API the node calls, for all three functions:

```bash
cd /opt/facility-node && sudo -u facility ./venv/bin/python dev/dummy_backend.py --data /var/lib/facility/dummy-backend
```

In `.env`, set the following and restart the service:

```ini
ADMIN_URL=http://127.0.0.1:8091/api
ADMIN_KEY=dummy-key
QR_API_URL=/qr/validate
```

The dummy hands out `DUMMY-ALLOW-000{1,2,3}` and Auth Code `dummy-auth-code`. Watch what arrives at `http://192.168.10.104:8091/` (start it with `--host 0.0.0.0` and run `sudo ufw allow from 192.168.10.0/24 to any port 8091` for the duration of the test).

Click **simulate outage** to check that doors keep opening and the queue drains afterwards with no duplicates.

> ⚠️ Remove it before handover: set the real `ADMIN_URL` / `ADMIN_KEY`, stop the dummy, and run `sudo ufw delete allow from 192.168.10.0/24 to any port 8091`.

---

## 8. Commissioning tests

First run the tests of each function that is on: **door** (4.8), **camera** (5.3), **odour** (6.5). Then these whole-system tests:

| # | Test | Expected | ☐ |
|---|---|---|---|
| S1 | `facility doctor` | `0 problem(s)`; `functions` shows what this site has | ☐ |
| S2 | Unplug the router WAN, repeat D3 + D5 | Doors still open offline (with paid entry, D3 is refused instead: D5c) | ☐ |
| S3 | Reconnect WAN | `/health` queue drains to 0; events reach admin | ☐ |
| S4 | Power-cycle the Pi | Service auto-starts; queue survives | ☐ |

---

## 9. Daily operations

| Task | Command |
|---|---|
| Live log | `facility logs` |
| Health | `facility health` |
| Check everything / one function | `facility doctor` / `facility doctor door` (`camera`, `odour`) |
| Functions on/off | `facility feature` / `facility feature camera off` |
| Door unlock time | `facility door` / `facility door delay male 8` |
| Restart | `facility restart` |
| Update code | `facility update` (keeps `.env` and the DB) |
| Back up per site | `/opt/facility-node/.env` + the relay board's `board_secrets.py` + `PCF8574_INPUT_ADDR` value |
| Warm spare | Image the SD card after commissioning; keep one spare Pi per cluster |

---

## 10. Troubleshooting (all functions)

For one function, see its own table: door 4.9, camera 5.4, odour 6.6.

| Symptom | Cause / fix |
|---|---|
| Service won't start: `X is required` | A `CHANGE_ME` value is left in `.env` (for a function you don't have: switch it off, section 3.3) |
| `ENABLE_DOORS, ENABLE_CAMERA and ENABLE_ODOUR are all off` | At least one function must be on |
| Queue keeps growing | Backend unreachable or `ADMIN_KEY` wrong; look for `backend HTTP 401` in the log |

---

## 11. Security checklist (before handover)

- [ ] Router admin password changed; no port forwarding
- [ ] KC868 password is a long random value in `board_secrets.py` (not `Admin`)
- [ ] Camera and WiFi passwords are not defaults and not shared in chat or tickets
- [ ] `/opt/facility-node/.env` is `640 root:facility`
- [ ] `DOOR_TEST_ENDPOINTS=0`
- [ ] Test QR codes revoked (`qr list` shows none, or only staff codes with an expiry)
- [ ] `QR_API_URL` is the real backend or empty, **not** the dummy (`127.0.0.1:8090`); the dummy is stopped
- [ ] `ADMIN_URL` is the real backend, **not** the dummy backend (`127.0.0.1:8091`); no `ufw` rule for port 8091 (`sudo ufw status`)
- [ ] Remote access via Cloudflare Tunnel only
- [ ] SD image backed up
