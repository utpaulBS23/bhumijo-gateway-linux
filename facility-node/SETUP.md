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

1. In **Raspberry Pi Imager**: *Raspberry Pi OS Lite (64-bit)*. In the settings (gear icon):
   - Hostname: `facility-<id>`
   - Username: `pi`, with a strong password
   - Enable SSH (public-key authentication preferred)
   - Leave WiFi unset if the Pi is on Ethernet (recommended)
2. Boot the Pi and connect it to the router.
3. Static IP. Either reserve .104 at the router, or set it on the Pi:

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual \
  ipv4.addresses 192.168.10.104/24 ipv4.gateway 192.168.10.100 ipv4.dns 192.168.10.100
sudo nmcli con up "Wired connection 1"
```

4. Update:

```bash
sudo apt update && sudo apt full-upgrade -y && sudo reboot
```

✅ **Check:** `ssh pi@192.168.10.104` works, and `ping -c1 192.168.10.174` gets a reply.

---

## 6. Install the node

From your laptop, in the repo:

```bash
scp -r facility-node pi@192.168.10.104:~
ssh pi@192.168.10.104
cd ~/facility-node && sudo bash deploy/install.sh
```

The installer handles:
- packages (Python, gpiozero/lgpio, ffmpeg, i2c-tools)
- the `facility` service user
- the code in `/opt/facility-node`
- enabling I2C
- the firewall: SSH, plus 5454 from the LAN only
- the hardware watchdog
- the systemd service

### 6.1 Configure

```bash
sudo nano /opt/facility-node/.env
```

Fill in every `CHANGE_ME` value and check the door section:

```ini
FACILITY_ID=<from admin backend>
RELAY_URL=http://192.168.10.174
RELAY_PWD=<board password from 4.3>
ADMIN_URL=<backend URL>
ADMIN_KEY=<backend key>
CAM_HOST=192.168.10.180
CAM_RTSP=rtsp://<user>:<pass>@192.168.10.180:554/stream1

DOORS=male,female
DOOR_MALE_RELAY=1
DOOR_MALE_EXIT_INPUT=0
DOOR_MALE_REED_GPIO=27
DOOR_MALE_SCANNER_IPS=192.168.10.101
DOOR_FEMALE_RELAY=2
DOOR_FEMALE_EXIT_INPUT=1
DOOR_FEMALE_REED_GPIO=22
DOOR_FEMALE_SCANNER_IPS=192.168.10.102

REED=1          # 0 if the reed switches aren't fitted yet
```

### 6.2 Test and start

```bash
cd /opt/facility-node
sudo -u facility ./venv/bin/python test_door.py      # last line: OK
sudo i2cdetect -y 1                                   # shows 38 and 53
sudo systemctl start facility-node
journalctl -u facility-node -f
```

In the log, look for:

```
door male: relay 1, exit input 0, reed GPIO 27, scanners 192.168.10.101
door female: relay 2, exit input 1, reed GPIO 22, scanners 192.168.10.102
token cache refreshed: N tokens
```

✅ **Check:**

```bash
curl -s http://192.168.10.104:5454/health
```

The response shows `"ok":true`, both doors, `tokens` > 0, and `auth_code_cached: true`.

**Reboot once** (`sudo reboot`) to turn on the watchdog, then confirm the service comes back by itself.

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
cd /opt/facility-node
alias qr='sudo -u facility ./venv/bin/python qr_tool.py'

qr create --label test                          # 1 code, valid 24 h
qr create --label cleaners --count 5 --hours 720  # 5 codes, valid 30 days
qr list
qr revoke --label test                          # or --token <t> / --expired / --all
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
qr create --label test --hours 24 --token <token>
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

Then **revoke the test codes**: `qr revoke --label test`.

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
| Door doesn't open, log shows `relay failed … wrong relay password` | `RELAY_PWD` ≠ the board's `POST_PASSWORD` |
| Door opens but no `exit` logged | `relay firmware does not report inputs` → firmware not flashed, wrong `PCF8574_INPUT_ADDR`, or polarity (section 4.5) |
| Wrong door logs the exit | `DOOR_*_EXIT_INPUT` swapped |
| False `forced_open` alerts | Reed too close to the mag lock, or polarity: set `REED_ACTIVE_HIGH=1` |
| Sensors `null` | I2C off or wiring: `sudo i2cdetect -y 1` should show 38 and 53. ENS160 needs ~3 min warm-up after boot and 24–48 h burn-in when new |
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
- [ ] Remote access via Cloudflare Tunnel only
- [ ] SD image backed up
