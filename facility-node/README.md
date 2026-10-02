# Facility Node

Offline-first door, sensor and camera node for one Raspberry Pi 5 per facility.
Replaces the legacy `gateway_service.py` (removed; in git history at commit `e364645`).

The internet is never in the unlock path. QR tokens and the Facility-app Auth Code
are cached locally; all events go into a SQLite queue first and drain to the admin
backend when it's reachable.

**→ Field installation: [SETUP.md](SETUP.md)** (wiring, firmware, Pi, scanners, QR codes, commissioning)

## Network and doors

Static IPs (reserved at the router, **outside the DHCP pool**):

| Device | IP | Notes |
|---|---|---|
| Router / gateway | 192.168.10.100 | Pi's default gateway + DNS |
| QR scanner (male) | 192.168.10.101 | → `POST /qr` on the Pi |
| QR scanner (female) | 192.168.10.102 | → `POST /qr` on the Pi |
| Raspberry Pi 5 | 192.168.10.104 | `:5454` |
| KC868-A4S relay | 192.168.10.174 | `:80` |
| IP camera | 192.168.10.180 | RTSP `:554` |

Point each scanner's upload URL at `http://192.168.10.104:5454/qr`.

| Door | Scanner | Relay | Exit button | Reed (BCM) |
|---|---|---|---|---|
| male | .101 | Relay01 | Input01 | GPIO27 |
| female | .102 | Relay02 | Input02 | GPIO22 |

- **Scan routing:** a scan is routed to a door by the scanner's source IP. A scan from any other IP gets `403 unknown_scanner`.
- **Tokens:** any valid token opens either door.
- **Events:** every door-event and alert carries `"section": "male" | "female"`.
- **Config checks:** the node refuses to start if two doors share a relay, input, GPIO or scanner IP.

If the Pi's IP is set on the Pi itself rather than by a router reservation:

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual \
  ipv4.addresses 192.168.10.104/24 ipv4.gateway 192.168.10.100 ipv4.dns 192.168.10.100
```

## Layout

| File | Role |
|---|---|
| `config.py` | Settings from env / `.env` |
| `store.py` | SQLite: token cache, hashed Auth Code, outbound queue |
| `relay.py` | KC868-A4S: `sw_ctl.cgi` fire, `input_ctl.cgi` inputs |
| `door.py` | Per-door unlock decisions + open classification + propped/forced alerts |
| `uplink.py` | 4 admin APIs: push sensor / door-event / alert, pull tokens |
| `sensors_camera.py` | ENS160 + AHT20 (smbus2), camera probe / frame check / snapshot + retention |
| `odour.py` | Odour alert: `odour_high` (+repeats) → `odour_resolved` |
| `mq.py` | MQ-135 (NH3) / MQ-136 (H2S) via ADS1115, plus `calibrate` / `read` CLI |
| `inputs.py` | MC-38 reed (gpiozero + lgpio), exit-button poller |
| `app.py` | Flask endpoints + background jobs |
| `qr_tool.py` | Create / list / revoke unlock QR codes on the Pi |
| `qr_api.py` | Configurable online QR validation (`QR_API_*`) with cache fallback |
| `dev/` | Dummy QR validation API + dummy QR images, for testing only |
| `test_door.py` | Test suite (no hardware needed) |
| `firmware/kc868/main.py` | Patched relay firmware |
| `deploy/` | systemd unit + installer |

## Local endpoints (LAN only)

| Method + path | Caller | Body |
|---|---|---|
| `POST /qr` | QR scanner | `{token}`, form field, or plain-text body → 200 opened / 403 denied or unknown scanner / 502 relay error |
| `GET\|POST /qrscanner` | legacy-format scanner | `?cardid=` → `{status, access_granted}` (always 200) |
| `POST /rakindaqrscanner` | Rakinda scanner | `{SCode}` → `{ResultCode: "1"\|"0"}` |
| `POST /facility/open` | Facility app | `{authCode, door}` or `X-Auth-Code` + `X-Door` headers; `door` = `male`/`female` |
| `POST /door/opened?door=male`, `/door/closed?door=male` | bench tests | only when `DOOR_TEST_ENDPOINTS=1` |
| `GET /health` | ops | queue size, token count, last pull/flush, per-door state, camera |

5 denied unlocks from one IP within 60s → 429 for that IP.

All three scan routes do the same thing: route the scan to a door by scanner IP, then check the code.

## Unlock QR codes

Backend tokens are pulled every 5 min. Codes for testing or staff are made on the Pi
with `qr_tool.py`. They're kept in a separate table, so the pull never removes them,
and they're logged as `source: qr_local`. They expire after 24 h by default.

```bash
sudo -u facility ./venv/bin/python qr_tool.py create --label test
sudo -u facility ./venv/bin/python qr_tool.py list
sudo -u facility ./venv/bin/python qr_tool.py revoke --label test
```

## Odour, gas sensors, camera

| Feature | Behaviour |
|---|---|
| Readings (every 60 s) | `tvoc`, `eco2`, `aqi`, `temperature`, `humidity`, plus `nh3_ppm` and `h2s_ppm` when `MQ=1`. `null` while warming up or after a failure |
| Odour alert | Any metric ≥ its limit for `ODOUR_HOLD` → `odour_high` (`repeat:false`), then every `ODOUR_REPEAT` (`repeat:true`); below all limits for `ODOUR_CLEAR_HOLD` → `odour_resolved`. Alert payload includes `metrics` (which limits tripped) and `readings` |
| Camera health | TCP probe every 30 s, frozen-frame check every 5 min; piggybacks the sensor push as `camera` |
| Snapshots | On `forced_open` and the first `propped_open`. Uploaded (multipart) **before** its alert when `SNAPSHOT_UPLOAD_PATH` is set; alert's `snapshot` = file name. Kept `SNAPSHOT_KEEP_DAYS` on the Pi |

## QR validation order

| Code | `QR_API_URL` empty | `local_first` (default) | `api_first` |
|---|---|---|---|
| Pi-made (`qr_tool`) | local | local | local |
| In backend cache | opens | opens, no API call | API decides; cache only if API down |
| Not in cache | denied | API decides; denied if API down | API decides; denied if API down |

Access-log `source`: `qr` (cache), `qr_api` (approved online), `qr_local` (Pi-made).
Denials carry a `reason`: `unknown`, `api_denied` or `empty`.
`/health` → `qr_api.last_ok` / `last_error` shows whether the API is answering.

## Facility-app auth

The app authenticates with the per-facility **Auth Code**, not the Facility ID
(the ID isn't secret). The backend returns it as `authCode` in the token pull; the
Pi stores only its SHA-256.

## Door-open classification

| Signal | Logged as |
|---|---|
| Valid QR → relay | door-event `entry` |
| Auth Code → relay | door-event `attendant` |
| KC868 `Input0N` rising edge | door-event `exit` (door opens in hardware; Pi doesn't fire) |
| Bad QR / Auth Code | door-event `denied` |
| Reed opens, no trigger in last `CLASSIFY_WINDOW` s | alert `forced_open` + snapshot |
| Open ≥ `PROPPED_THRESHOLD` | alert `propped_open` (`repeat:false`, snapshot), then every `PROPPED_REPEAT` (`repeat:true`) |
| Reed closes | door-event `door_closed` + `duration_s`; `propped_resolved` if it was propped |

## KC868 firmware

`firmware/kc868/main.py` is the board's `main.py` with these changes:

- **Input reporting**: `input_ctl.cgi` adds `Input01..04`. Set `PCF8574_INPUT_ADDR` from an I2C scan.
- **Wrong password on `sw_ctl.cgi` now returns 401** (was 200), so the Pi can detect a failed unlock.
- **`input_ctl.cgi` without `postpwd` returns 401** (was a KeyError that hung the socket).
- **Password in `board_secrets.py`**, not hard-coded `"Admin"`. Copy `board_secrets.py.example`.
- **Request logging drops the query string** (it contains the password).
- **Unknown paths return 404** instead of a fake "Relay Control Successful".

The relay still auto-offs after 1s, so the Pi only sends `ON`.

## Install

```bash
scp -r facility-node pi@192.168.10.104:~
ssh pi@192.168.10.104 'cd facility-node && sudo bash deploy/install.sh'
```

Then fill in the `CHANGE_ME` values in `/opt/facility-node/.env`, run the tests, and start the service.
The installer allows port 5454 only from `LAN_CIDR` (default `192.168.10.0/24`).

## Tests

```bash
python3 test_door.py
```
