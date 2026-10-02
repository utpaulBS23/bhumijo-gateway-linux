# Facility Node: Setup Guide

From a blank Raspberry Pi 5 to a commissioned facility with two doors (male and female).
Follow the steps in order. Each step ends with a check, so stop there if the check fails.

```
                     Router 192.168.10.100
      ┌──────────────┬──────┴───────┬───────────────┬──────────────┐
 QR male .101   QR female .102   Pi 5 .104      KC868 .174     Camera .180
      │  POST /qr      │ POST /qr     │ :5454          │ :80          │ RTSP :554
      └────────────────┴────────────► │ ─ sw_ctl.cgi ─►│              │
                                      │ ◄─ input_ctl ──│              │
                                      │ ◄──────────── snapshot ───────┘
                                      │ I2C: ENS160 + AHT20
                                      │ GPIO27: male reed   GPIO22: female reed
                                      └─► HTTPS (outbound only) ─► Admin backend

 KC868 Relay01 ─► male timer ─► male mag lock      Relay02 ─► female timer ─► female mag lock
 Male exit button:   pole 1 ─► male timer,   pole 2 ─► KC868 Input01
 Female exit button: pole 1 ─► female timer, pole 2 ─► KC868 Input02
```

---

## 1. Parts checklist

**Shared by the whole facility**

- [ ] Raspberry Pi 5 (4 GB), 5V/5A USB-C supply, **endurance** microSD (32 GB)
- [ ] KC868-A4S relay board
- [ ] ENS160 + AHT20 sensor module
- [ ] IP camera (already installed)
- [ ] Router
- [ ] Mini DC-UPS: powers the Pi, router and camera, **not** the locks

**For each door (×2: male and female)**

- [ ] QR scanner with network upload
- [ ] Programmable timer relay module
- [ ] 12V magnetic lock (fails open when power is lost)
- [ ] Dual-pole (DPDT) exit button
- [ ] MC-38 reed switch (optional; needed for forced-open and propped-open alerts)

**Optional: ammonia / H2S sensing**

- [ ] ADS1115 ADC module
- [ ] MQ-135 (ammonia) and/or MQ-136 (H2S) module
- [ ] Resistors for each MQ output: 10 kΩ + 20 kΩ (voltage divider)

**Information to collect first**

| Item | From |
|---|---|
| Facility ID | Admin backend |
| Admin API URL + key | Backend team |
| Facility-app Auth Code | Admin backend (per facility) |
| Camera RTSP URL | Camera config (test it in VLC first) |

---

## 2. Network: router and IP plan

In the router admin page (usually `http://192.168.10.100`):

1. Set the LAN to `192.168.10.0/24`, with the router at **192.168.10.100**.
2. **Shrink the DHCP pool** so it excludes the device addresses below, e.g. `192.168.10.150–199` minus .174/.180, or simply `192.168.10.200–250`.
3. Reserve or set these static addresses:

| Device | IP | Port |
|---|---|---|
| Router / gateway | 192.168.10.100 | — |
| QR scanner, **male** | 192.168.10.101 | sends to Pi :5454 |
| QR scanner, **female** | 192.168.10.102 | sends to Pi :5454 |
| Raspberry Pi 5 | 192.168.10.104 | 5454 |
| KC868-A4S | 192.168.10.174 | 80 |
| IP camera | 192.168.10.180 | 554 |

4. Change the router's admin password from the default.
5. **No port forwarding** to anything. The Pi only makes outbound connections.

✅ **Check:** a phone on the WiFi gets an address from the pool, not one of the reserved addresses above.

> A scanner that ends up on the wrong IP is rejected (`403 unknown_scanner`). That's on purpose: the Pi uses the scanner's IP to decide which door to open.

---

## 3. Wiring

### 3.1 Raspberry Pi header

| Signal | Pi physical pin | BCM |
|---|---|---|
| Sensor 3.3V | 1 | — |
| Sensor SDA | 3 | GPIO2 |
| Sensor SCL | 5 | GPIO3 |
| Sensor GND | 9 | — |
| **Male** reed, wire A | 13 | **GPIO27** |
| **Male** reed, wire B | 14 | GND |
| **Female** reed, wire A | 15 | **GPIO22** |
| **Female** reed, wire B | 20 | GND |

**Optional ADS1115 + MQ sensors** (on the same I2C bus as the ENS160/AHT20):

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

The reed switches go directly to the GPIO pins. No resistor is needed (the code enables the internal pull-up), and no optocoupler.
Mount each reed on the door's opening edge, **at least 10 cm from the mag lock**.

### 3.2 KC868-A4S

| KC868 terminal | Goes to |
|---|---|
| Relay01 COM/NO | **Male** timer module trigger |
| Relay02 COM/NO | **Female** timer module trigger |
| Input01 + GND | **Male** exit button, pole 2 (dry contact) |
| Input02 + GND | **Female** exit button, pole 2 (dry contact) |

### 3.3 Exit buttons (each door)

- **Pole 1** → that door's timer trigger, wired exactly as before. This opens the door in hardware, even with the Pi off.
- **Pole 2** → that door's KC868 input. This only lets the Pi *log* the exit.

### 3.4 Power

- Mini DC-UPS → Pi, router, camera.
- Mag locks stay on their own 12V supply through the timer modules. They fail open on a power cut, which is the safe behaviour for a public facility.

✅ **Check:** with the Pi powered off, pressing each exit button releases its own door.

---

## 4. KC868-A4S firmware

The board needs the patched `firmware/kc868/main.py`. It adds exit-input reporting, returns 401 on a wrong password, and moves the password out of the code.

### 4.1 Tools

On your laptop, install `mpremote` and connect the board over USB:

```bash
pip install mpremote
mpremote connect list
```

### 4.2 Find the input chip address

```bash
mpremote connect /dev/ttyUSB0 exec "import machine; i2c=machine.I2C(0,scl=machine.Pin(16),sda=machine.Pin(4)); print([hex(a) for a in i2c.scan()])"
```

You'll see something like `['0x22', '0x24']`. **0x24 is the relays; the other one is the inputs.**
Open `firmware/kc868/main.py` and set:

```python
PCF8574_INPUT_ADDR = 0x22   # the non-0x24 address you saw
```

### 4.3 Set the board password

```bash
cd facility-node/firmware/kc868
cp board_secrets.py.example board_secrets.py
python3 -c "import secrets; print(secrets.token_urlsafe(18))"   # copy this
nano board_secrets.py                                           # POST_PASSWORD = "<paste>"
```

Keep this password. It goes into the Pi's `.env` as `RELAY_PWD`. **Never commit `board_secrets.py`.**

### 4.4 Upload and restart

```bash
mpremote connect /dev/ttyUSB0 cp main.py :main.py + cp board_secrets.py :board_secrets.py + reset
```

### 4.5 Test from any machine on the LAN

```bash
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=<PASSWORD>&Relay01=ON"   # male door clicks
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=<PASSWORD>&Relay02=ON"   # female door clicks
curl -i "http://192.168.10.174/sw_ctl.cgi?postpwd=wrong&Relay01=ON"        # 401, nothing clicks
curl "http://192.168.10.174/input_ctl.cgi?postpwd=<PASSWORD>"              # hold male exit button
```

✅ **Check:**
- Each relay releases its own door.
- A wrong password returns `401`.
- `Input01=ON` while the male exit button is held, and `Input02=ON` for the female one.
- If pressed reads `OFF` and released reads `ON`, delete the `not` in `read_inputs()` and upload again.

---

## 5. Raspberry Pi OS

Follow **[INSTALL.md](INSTALL.md) sections 1–4**: flash Raspberry Pi OS Lite (64-bit), first login, static IP `192.168.10.104` (gateway `192.168.10.100`), OS update.

✅ **Check:** `ssh pi@192.168.10.104` works, and `ping -c1 192.168.10.174` gets a reply.

---

## 6. Install the node

Follow **[INSTALL.md](INSTALL.md) sections 5–10**: copy the code, `sudo bash deploy/install.sh`, `facility config`, `facility doctor`, reboot test.

Door settings for this site in `.env`:

```ini
DOORS=male,female
DOOR_MALE_RELAY=1
DOOR_MALE_EXIT_INPUT=0
DOOR_MALE_REED_GPIO=27
DOOR_MALE_SCANNER_IPS=192.168.10.101
DOOR_FEMALE_RELAY=2
DOOR_FEMALE_EXIT_INPUT=1
DOOR_FEMALE_REED_GPIO=22
DOOR_FEMALE_SCANNER_IPS=192.168.10.102
# 0 until the reed switches are fitted
REED=1
```

✅ **Check:** `facility doctor` shows both doors, `reachable, password accepted` for the relay, and `0 problem(s)`.

---

## 7. QR scanners

On each scanner's configuration page or tool:

| Setting | Male scanner | Female scanner |
|---|---|---|
| IP | 192.168.10.101 | 192.168.10.102 |
| Netmask | 255.255.255.0 | 255.255.255.0 |
| Gateway | 192.168.10.100 | 192.168.10.100 |
| Mode | HTTP upload / POST | HTTP upload / POST |
| Server URL | `http://192.168.10.104:5454/qr` | `http://192.168.10.104:5454/qr` |

If the scanner's firmware has a fixed path, point it at the matching compatible route instead:

| Scanner sends | Use URL |
|---|---|
| `?cardid=...` (GET or POST) | `http://192.168.10.104:5454/qrscanner` |
| `{"SCode": "..."}` (Rakinda) | `http://192.168.10.104:5454/rakindaqrscanner` |
| JSON `{token}`, a form field, or plain text | `http://192.168.10.104:5454/qr` |

All three routes run the same checks. The door is chosen by the scanner's IP.

✅ **Check:** scan any QR and watch `journalctl -u facility-node -f`:

| Log shows | Meaning |
|---|---|
| `[male] QR denied` | Scanner reaches the Pi. The code just isn't valid. Good. |
| `scan from unknown scanner 192.168.10.x` | The scanner's IP doesn't match `.env`. Fix the IP. |
| nothing | Wrong server URL, or the scanner isn't in HTTP mode |

---

## 8. Unlock QR codes (test, staff, commissioning)

User QR codes come from the admin backend automatically. Use `qr_tool.py` for codes made **on the Pi**, such as a test code for commissioning, or codes for cleaners and staff.
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

### Registering a code made somewhere else

If you were given a QR image along with its token, register that exact token:

```bash
facility qr create --label test --hours 24 --token <token>
```

---

## 8a. Online QR validation (optional)

By default the Pi checks codes against the token list it pulls every 5 minutes. To also ask your backend live, for example for tickets bought a minute ago or for instant revocation, set `QR_API_*` in `.env`.

### Match your backend's API

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

### Pick the order

- **`QR_API_ORDER=local_first`** (recommended): codes in the cache open instantly, and only unknown codes go to the API. With the internet down, every cached code still opens.
- **`QR_API_ORDER=api_first`:** every scan asks the API first, and a "no" from the API beats the cache, so revocation takes effect immediately. If the API doesn't answer within `QR_API_TIMEOUT` (3 s), the cache decides. Each scan can wait up to that long.

Codes made with `qr_tool.py` always open locally, in both modes.

### Test it now with the dummy API

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

---

### Test the whole node against a dummy backend

Before the real backend exists, run the dummy admin backend on the Pi. It answers every API the node calls:

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

## 8b. Odour sensors and alert

### What's measured

| Sensor | Values | Notes |
|---|---|---|
| ENS160 | `tvoc` (ppb), `eco2` (ppm), `aqi` (1–5) | General gases. A good *trend* signal, but not specific to toilet smells |
| AHT20 | `temperature`, `humidity` | Also used to correct the ENS160's readings |
| MQ-135 (optional) | `nh3_ppm` | Ammonia, the main urine smell |
| MQ-136 (optional) | `h2s_ppm` | Hydrogen sulphide, the "rotten egg" smell |

### Warm-up

- **ENS160:** 24–48 h burn-in when new, then about 3 min after each boot. It sends `null` until it's ready.
- **MQ sensors:** 24–48 h burn-in when new, then about 5 min of heater warm-up after each boot.

### Calibrate the MQ sensors (once, after burn-in)

1. `facility config`, set `MQ=1`, save.
2. Air the room out: door open, no people, no cleaning products for 30 min.
3. Run:

```bash
facility mq read          # Rs should be steady, not "no signal"
facility mq calibrate     # samples for 3 min
```

4. `facility config`, paste the printed `MQ135_R0=` / `MQ136_R0=` lines, save (the service restarts).

Until R0 is set, `nh3_ppm` and `h2s_ppm` stay `null`. Check `MQ_RL_KOHM` against the small resistor on the MQ board: `103` = 10 kΩ, `102` = 1 kΩ.

### Odour alert

| `.env` | Default | Meaning |
|---|---|---|
| `ODOUR_TVOC_LIMIT` | 1500 | ppb; empty = off |
| `ODOUR_AQI_LIMIT` | 4 | 1–5; empty = off |
| `ODOUR_NH3_LIMIT` | 10 | ppm; empty = off |
| `ODOUR_H2S_LIMIT` | 1 | ppm; empty = off |
| `ODOUR_HOLD` | 600 | Seconds above a limit before `odour_high` |
| `ODOUR_REPEAT` | 1800 | Repeat `odour_high` (`repeat:true`) while still high |
| `ODOUR_CLEAR_HOLD` | 300 | Seconds below every limit before `odour_resolved` |

**Tune the limits per site over the first week:**
1. Note the time whenever a cleaner reports the toilet "needs cleaning".
2. Compare against the readings in the admin panel.
3. Set each limit just below the values seen at those times.

The defaults are only starting points.

✅ **Check:** `curl -s http://192.168.10.104:5454/health` → `odour.limits` lists the metrics you turned on.

---

## 8c. Camera snapshots

- **When:** a snapshot is taken on `forced_open` and on the first `propped_open` alert.
- **Where:** saved to `/var/lib/facility/snapshots/` and deleted after `SNAPSHOT_KEEP_DAYS` (default 7).
- **Upload:** set `SNAPSHOT_UPLOAD_PATH` in `.env` once the backend has an upload endpoint. The Pi sends a multipart POST with field `file` and form fields `facility`, `name`, `ts`, using the same auth header as the other APIs.
- **Matching:** the upload is queued **before** its alert, so the image arrives first. The alert's `snapshot` field is the file name.

Copy snapshots off by hand:

```bash
scp pi@192.168.10.104:/var/lib/facility/snapshots/*.jpg .
```

---

## 9. Facility app (attendant phones)

In the app's settings:

| Setting | Value |
|---|---|
| Endpoint URL | `http://192.168.10.104` |
| Port | `5454` |
| Auth Code | the facility's Auth Code (from the admin backend), **not** the Facility ID |

The app unlocks with:

```http
POST /facility/open
Content-Type: application/json

{"authCode": "<auth code>", "door": "male"}     // or "female"
```

Five wrong Auth Codes in 60s from one phone block that phone for a minute.

---

## 10. Commissioning tests

Run every test for **both doors**. Tick each column.

| # | Test | Expected | Male | Female |
|---|---|---|---|---|
| 1 | `curl sw_ctl.cgi … RelayNN=ON` | Only this door releases | ☐ | ☐ |
| 2 | Hold exit button, `curl input_ctl.cgi` | Only this door's `InputNN=ON` | ☐ | ☐ |
| 3 | Show a valid QR (section 8) | This door opens; `entry` logged with `section` | ☐ | ☐ |
| 4 | Show an invalid QR | Stays locked; `denied` logged | ☐ | ☐ |
| 5 | Facility app open with `door` set | Only that door opens; `attendant` logged | ☐ | ☐ |
| 6 | Press exit button | Door opens (hardware); `exit` logged once | ☐ | ☐ |
| 7 | Open the door by hand, no trigger (reed fitted) | `forced_open` alert + snapshot | ☐ | ☐ |
| 8 | Hold the door open > 5 min | `propped_open`, repeats every 3 min, `propped_resolved` on close | ☐ | ☐ |
| 9 | Unplug the router WAN, repeat 3 + 5 | Door still opens offline | ☐ | ☐ |
| 10 | Reconnect WAN | `/health` queue drains to 0; events reach admin | ☐ | ☐ |
| 11 | Power-cycle the Pi | Service auto-starts; queue survives | ☐ | ☐ |
| 12 | Pi powered off, press exit button | Door still releases (hardware fail-safe) | ☐ | ☐ |

Then **revoke the test codes**: `facility qr revoke --label test`.

---

## 11. Daily operations

| Task | Command |
|---|---|
| Live log | `journalctl -u facility-node -f` |
| Health | `curl -s http://192.168.10.104:5454/health` |
| Restart | `sudo systemctl restart facility-node` |
| Update code | copy the new `facility-node/` → `sudo bash deploy/install.sh` → `sudo systemctl restart facility-node` (keeps `.env` and the DB) |
| Back up per site | `/opt/facility-node/.env` + the board's `board_secrets.py` + `PCF8574_INPUT_ADDR` value |
| Warm spare | Image the SD card after commissioning; keep one spare Pi per cluster |

---

## 12. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Service won't start: `X is required` | A `CHANGE_ME` value is left in `.env` |
| `relay channel used by more than one door` | Two doors share a relay, input, GPIO or scanner IP in `.env` |
| Scan → `unknown scanner` | The scanner's IP isn't the one in `DOOR_*_SCANNER_IPS` (check the DHCP pool) |
| Scan → nothing in the log | Wrong scanner URL or mode; try `curl -XPOST http://192.168.10.104:5454/qr -d test` from the LAN |
| Valid QR denied | The code expired, or the token pull hasn't run (`/health` → `tokens`, `last_pull`) |
| New tickets denied until the next pull | Turn on online validation (section 8a) |
| `QR API unavailable` in the log | Wrong `QR_API_URL`, backend down, or a 401 (check `QR_API_AUTH` / the key). `/health` → `qr_api.last_error` |
| API always denies | `QR_API_ALLOW_FIELD` doesn't match the reply. Run the dummy API to see the request, and compare with the backend's real reply |
| Door doesn't open, log shows `relay failed … wrong relay password` | `RELAY_PWD` ≠ the board's `POST_PASSWORD` |
| Door opens but no `exit` logged | `relay firmware does not report inputs` → firmware not flashed, wrong `PCF8574_INPUT_ADDR`, or polarity (section 4.5) |
| Wrong door logs the exit | `DOOR_*_EXIT_INPUT` swapped |
| False `forced_open` alerts | Reed too close to the mag lock, or polarity: set `REED_ACTIVE_HIGH=1` |
| Sensors `null` | I2C off or wiring: `sudo i2cdetect -y 1` should show 38 and 53. ENS160 needs ~3 min warm-up after boot and 24–48 h burn-in when new |
| `nh3_ppm` / `h2s_ppm` always `null` | `MQ=0`, R0 not set (run `mq.py calibrate`), or no `48` in `i2cdetect` |
| MQ `Rs` "no signal" | AOUT not reaching the ADS1115 channel, or the divider is wired backwards |
| ppm wildly high | `MQ_RL_KOHM` wrong for your board, or calibrated in dirty air: recalibrate |
| Odour alert never fires | Limits too high, or the metric's limit is empty; check `/health` → `odour.limits` |
| Odour alert fires constantly | Limits too low for this site; raise them after a week of data |
| Snapshots not reaching admin | `SNAPSHOT_UPLOAD_PATH` empty, or the backend rejects the upload (look for `snapshot` in `journalctl`) |
| Camera `reachable:false` | Wrong `CAM_HOST`; test the RTSP URL in VLC |
| Facility app 400 `unknown_door` | The app isn't sending `door` |
| Facility app 429 | Too many wrong Auth Codes; wait a minute and check the code |
| Queue keeps growing | Backend unreachable or `ADMIN_KEY` wrong; look for `backend HTTP 401` in the log |

---

## 13. Security checklist (before handover)

- [ ] Router admin password changed; no port forwarding
- [ ] KC868 password is a long random value in `board_secrets.py` (not `Admin`)
- [ ] `/opt/facility-node/.env` is `640 root:facility`
- [ ] `DOOR_TEST_ENDPOINTS=0`
- [ ] Test QR codes revoked (`qr list` shows none, or only staff codes with an expiry)
- [ ] `QR_API_URL` is the real backend or empty, **not** the dummy (`127.0.0.1:8090`); the dummy is stopped
- [ ] `ADMIN_URL` is the real backend, **not** the dummy backend (`127.0.0.1:8091`); no `ufw` rule for port 8091 (`sudo ufw status`)
- [ ] Remote access via Cloudflare Tunnel only
- [ ] SD image backed up
