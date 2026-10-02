# Facility Node

Offline-first door, sensor and camera node for one Raspberry Pi 5 per facility.
Replaces the legacy `gateway_service.py` (kept at the repo root until the switchover).

The internet is never in the unlock path. QR tokens and the Facility-app Auth Code
are cached locally; all events go into a SQLite queue first and drain to the admin
backend when it's reachable.

## Layout

| File | Role |
|---|---|
| `config.py` | Settings from env / `.env` |
| `store.py` | SQLite: token cache, hashed Auth Code, outbound queue |
| `relay.py` | KC868-A4S: `sw_ctl.cgi` fire, `input_ctl.cgi` inputs |
| `door.py` | Unlock decisions + open classification + propped/forced alerts |
| `uplink.py` | 4 admin APIs: push sensor / door-event / alert, pull tokens |
| `sensors_camera.py` | ENS160 + AHT20 (smbus2), camera probe / frame check / snapshot |
| `inputs.py` | MC-38 reed (gpiozero + lgpio), exit-button poller |
| `app.py` | Flask endpoints + background jobs |
| `test_door.py` | Test suite (no hardware needed) |
| `firmware/kc868/main.py` | Patched relay firmware |
| `deploy/` | systemd unit + installer |

## Local endpoints (LAN only)

| Method + path | Caller | Body |
|---|---|---|
| `POST /qr` | QR scanner | `{token}` → 200 opened / 403 denied / 502 relay error |
| `POST /facility/open` | Facility app | `{authCode}` or `X-Auth-Code` header |
| `POST /door/opened`, `/door/closed` | bench tests | only when `DOOR_TEST_ENDPOINTS=1` |
| `GET /health` | ops | queue size, token count, last pull/flush, camera |

5 denied unlocks from one IP within 60s → 429 for that IP.

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
