# Facility Node

Offline-first controller for one public facility, running on a single **Raspberry Pi 5**.
It unlocks two doors (male and female) by QR code or attendant app, logs exits, detects forced and propped doors, watches air quality and odour, checks the IP camera, and reports everything to the admin backend.

**The internet is never in the unlock path.** Codes are checked against a local cache, every event is queued on the Pi first, and the exit buttons and magnetic locks keep working in hardware even with the Pi switched off.

| Document | For |
|---|---|
| **README.md** (this file) | How the system works, the configuration and API reference |
| **[INSTALL.md](INSTALL.md)** | Installing the software on the Raspberry Pi, step by step |
| **[SETUP.md](SETUP.md)** | Field setup: wiring, relay firmware, scanners, QR codes, sensors, commissioning |

On an installed Pi, read them with `facility docs readme | install | setup`.

---

## Contents

1. [Architecture](#1-architecture)
2. [Features](#2-features)
3. [Quick start](#3-quick-start)
4. [Hardware and network](#4-hardware-and-network)
5. [How decisions are made](#5-how-decisions-are-made)
6. [Configuration](#6-configuration)
7. [Local HTTP endpoints](#7-local-http-endpoints)
8. [Backend API contract](#8-backend-api-contract)
9. [Offline behaviour and data](#9-offline-behaviour-and-data)
10. [Security](#10-security)
11. [Operations](#11-operations)
12. [Development](#12-development)
13. [Project layout](#13-project-layout)
14. [Open items](#14-open-items)

---

## 1. Architecture

```
                         Router 192.168.10.1    ──── internet (outbound HTTPS only) ──── Admin backend + MySQL
     ┌──────────────┬──────────┴─────┬────────────────┬───────────────┐
 QR male .101   QR female .102   Raspberry Pi 5 .104   KC868-A4S .174   IP camera .180
     │ POST /qr       │ POST /qr      │ :5454             │ :80             │ RTSP :554
     └────────────────┴─────────────► │ ── sw_ctl.cgi ──► │ Relay01 → male timer → male lock
                                      │                   │ Relay02 → female timer → female lock
 Facility app (phone) ─ POST ───────► │ ◄─ input_ctl.cgi ─│ Input01 ← male exit button (pole 2)
   /facility/open                     │                   │ Input02 ← female exit button (pole 2)
                                      │ ◄──────────── snapshot / health ──────────────┘
                                      │ I2C: AHT20 0x38, ENS160 0x53, [ADS1115 0x48 → MQ-135, MQ-136]
                                      │ GPIO27: male reed      GPIO22: female reed
```

Inside the Pi, a single Python service (`facility-node`, run by systemd):

```
 Flask endpoints ──► DoorController (one per door) ──► Relay (KC868 HTTP)
   /qr /facility/open      │  QR: local cache ⇄ optional QR API
                           ▼
                     Store (SQLite) ◄── token pull (every 5 min)
                     · token cache      · Pi-made codes
                     · Auth Code hash   · outbox queue ──► Uplink ──► 4 admin APIs (+ snapshot upload)
 Background threads: exit-input poller · reed watcher · door ticker · sensor loop (+ odour monitor)
                     camera probe · camera frame check · queue flush · token pull
```

---

## 2. Features

| Area | What it does |
|---|---|
| **QR unlock** | Each scanner opens its own door (routed by scanner IP). Checked against the local cache, optionally plus a live **QR validation API**. Any valid code opens either door |
| **Facility app unlock** | Attendant phone sends the per-facility **Auth Code** and the door name |
| **Pi-made QR codes** | `facility qr create`: test, staff and commissioning codes that expire after 24 h by default |
| **Exit tracking** | Exit button opens the door in hardware; a second contact into the KC868 lets the Pi log an `exit` |
| **Door anomalies** | Reed switch per door: `forced_open` (opened with no trigger), `propped_open` (repeats while open), `propped_resolved` |
| **Air quality** | ENS160 (TVOC, eCO2, AQI) + AHT20 (temperature, humidity) every 60 s |
| **Odour** | Optional MQ-135 (ammonia) and MQ-136 (H2S) through an ADS1115; `odour_high` / `odour_resolved` alerts against per-site limits |
| **Camera** | Reachability probe, frozen-feed detection, JPEG snapshots on door anomalies, optional upload |
| **Offline-first** | Doors open with no internet; events queue on the Pi and drain oldest-first when the backend is back |
| **Self-check** | `facility doctor` checks config, relay, camera, backend, sensors and the service in one go |
| **Resilience** | Hardware watchdog, auto-restart, survives power cuts (SQLite WAL), warm-spare procedure |

---

## 3. Quick start

On a Pi 5 with Raspberry Pi OS Lite (64-bit) at `192.168.10.104`:

```bash
# laptop
scp -r facility-node pi@192.168.10.104:~
# Pi
cd ~/facility-node && sudo bash deploy/install.sh
facility config      # fill in every CHANGE_ME, save -> service starts
facility doctor      # everything ✓
```

**From a git clone (recommended):**

```bash
git clone https://github.com/utpaulBS23/bhumijo-gateway-linux.git ~/bhumijo && cd ~/bhumijo
sudo bash facility-node/deploy/bootstrap.sh   # install + auto-reinstall on every git pull
facility config && facility doctor
```

**With Claude Code on the Pi:** run `claude` in the repo and say "install". It follows [`CLAUDE.md`](../CLAUDE.md).

The full walkthrough is in **[INSTALL.md](INSTALL.md)**. Wiring and commissioning are in **[SETUP.md](SETUP.md)**.

---

## 4. Hardware and network

### Per facility

| Item | Notes |
|---|---|
| Raspberry Pi 5 (4 GB) + endurance microSD + 5V/5A supply | Runs everything |
| KC868-A4S relay board | Patched firmware in `firmware/kc868/` |
| Per door ×2: timer relay module, 12V mag lock, DPDT exit button, QR scanner, MC-38 reed (optional) | Locks fail open on power loss |
| ENS160 + AHT20 | I2C |
| ADS1115 + MQ-135 + MQ-136 (optional) | I2C + analog, with a voltage divider on each MQ output |
| IP camera (existing) | RTSP |
| Router + mini DC-UPS | The UPS powers the Pi, router and camera, **not** the locks |

### IP plan

| Device | IP | Port |
|---|---|---|
| Router / gateway | 192.168.10.1 | — |
| QR scanner male / female | 192.168.10.101 / .102 | → Pi :5454 |
| Raspberry Pi 5 | 192.168.10.104 | 5454 (LAN only) |
| KC868-A4S | 192.168.10.174 | 80 |
| IP camera | 192.168.10.180 | 554 |

All of these are static and **outside the DHCP pool**. Only phones use DHCP.

### Door map (default `.env`)

| Door | Scanner | Relay | Exit input | Reed GPIO (pin) |
|---|---|---|---|---|
| male | .101 | Relay01 | Input01 | 27 (pin 13) |
| female | .102 | Relay02 | Input02 | 22 (pin 15) |

---

## 5. How decisions are made

### 5.1 QR scan → door

1. **Pick the door:** by the scanner's source IP (`DOOR_<NAME>_SCANNER_IPS`). An unknown IP gets `403 unknown_scanner` and nothing opens.
2. **Read the code:** from JSON (`token`, `SCode`, `cardid`, `code`…), form fields, the query string, or a plain-text body.
3. **Validate:**

| Code | No QR API | `QR_API_ORDER=local_first` (default) | `api_first` |
|---|---|---|---|
| Pi-made (`facility qr`) | ✓ local | ✓ local | ✓ local |
| In backend cache | ✓ | ✓, no API call | API decides; cache only if API down |
| Not cached | ✗ | API decides; ✗ if API down | API decides; ✗ if API down |

4. **Fire the relay**, then log a door-event:
   - `entry` with `source` = `qr` (cache), `qr_api` (approved online) or `qr_local` (Pi-made);
   - or `denied` with `reason` = `unknown`, `api_denied` or `empty`.

### 5.2 Facility app

`POST /facility/open {authCode, door}` → the Auth Code is compared with the cached hash → `attendant`.
Five failures from one phone in 60 s → `429` for a minute.

### 5.3 Classifying a physical opening (reed switch)

| Signal | Result |
|---|---|
| Valid QR / app unlock or exit press on **this door** within `CLASSIFY_WINDOW` (15 s) | Normal opening (already logged as entry / attendant / exit) |
| Opening with no trigger | Alert `forced_open` + snapshot |
| Open ≥ `PROPPED_THRESHOLD` (300 s) | Alert `propped_open` (`repeat:false`, snapshot), then every `PROPPED_REPEAT` (180 s) with `repeat:true` |
| Door closes | Door-event `door_closed` + `duration_s`; `propped_resolved` if it was propped |

Each door keeps its own state. A male QR scan doesn't excuse a female door opening.

### 5.4 Odour

Any metric (`tvoc`, `aqi`, `nh3_ppm`, `h2s_ppm`) at or above its limit for `ODOUR_HOLD` (600 s) → `odour_high`, repeated every `ODOUR_REPEAT` (1800 s) while still high. All metrics below their limits for `ODOUR_CLEAR_HOLD` (300 s) → `odour_resolved`. Missing readings (warm-up or failure) neither start nor clear an alert.

---

## 6. Configuration

All settings live in `/opt/facility-node/.env`, loaded by systemd. **`.env.example` documents every key.** Edit with `facility config`. The service refuses to start on a missing secret or a conflicting door map, and says why.

| Group | Keys | Notes |
|---|---|---|
| Identity | `FACILITY_ID` | required |
| Relay | `RELAY_URL`, `RELAY_PWD` | `RELAY_PWD` required |
| Doors | `DOORS`, `DOOR_<NAME>_RELAY`, `_EXIT_INPUT`, `_REED_GPIO`, `_SCANNER_IPS` | e.g. `DOORS=male,female` |
| Backend | `ADMIN_URL`, `ADMIN_KEY`, `ADMIN_AUTH_HEADER`, `ADMIN_AUTH_SCHEME`, `SENSOR_PATH`, `DOOR_EVENT_PATH`, `ALERT_PATH`, `TOKENS_PATH` | URL and key required |
| QR validation | `QR_API_URL`, `_METHOD`, `_ORDER`, `_TIMEOUT`, `_AUTH`, `_TOKEN_FIELD`, `_FACILITY_FIELD`, `_DOOR_FIELD`, `_EXTRA`, `_ALLOW_FIELD`, `_ALLOW_VALUES`, `_DENY_STATUS` | off when `QR_API_URL` is empty |
| Reed | `REED`, `REED_ACTIVE_HIGH`, `PROPPED_THRESHOLD`, `PROPPED_REPEAT`, `CLASSIFY_WINDOW` | |
| Camera | `CAM_HOST`, `CAM_PORT`, `CAM_RTSP`, `SNAPSHOT_DIR`, `SNAPSHOT_UPLOAD_PATH`, `SNAPSHOT_KEEP_DAYS` | upload off when the path is empty |
| Odour | `ODOUR_TVOC_LIMIT`, `_AQI_LIMIT`, `_NH3_LIMIT`, `_H2S_LIMIT`, `ODOUR_HOLD`, `_REPEAT`, `_CLEAR_HOLD` | empty limit = metric off |
| MQ sensors | `MQ`, `ADS1115_ADDR`, `MQ135_CHANNEL`, `MQ136_CHANNEL`, `MQ135_R0`, `MQ136_R0`, `MQ_RL_KOHM`, `MQ_DIVIDER`, `MQ_VC` | R0 from `facility mq calibrate` |
| Storage | `DB_PATH`, `MAX_QUEUE` | |
| Timing | `SENSOR_INTERVAL`, `PULL_INTERVAL`, `FLUSH_INTERVAL`, `CAM_PROBE_INTERVAL`, `CAM_FRAME_INTERVAL`, `EXIT_POLL_INTERVAL` | seconds |
| HTTP | `HOST`, `PORT`, `DOOR_TEST_ENDPOINTS` | keep test endpoints `0` in production |

One `KEY=value` per line, with comments on their own lines. systemd does **not** strip a comment that follows a value.

---

## 7. Local HTTP endpoints

Port 5454, **facility LAN only**: the firewall blocks everything else, and there's no port forwarding.

| Method + path | Caller | Request | Response |
|---|---|---|---|
| `POST /qr` | QR scanner | `{token}`, form field, or plain-text body | `200 opened` · `403 denied` / `unknown_scanner` · `502 relay_error` |
| `GET\|POST /qrscanner` | Legacy-format scanner | `?cardid=…` | always 200: `{status, access_granted}` |
| `POST /rakindaqrscanner` | Rakinda scanner | `{SCode}` | `{ResultCode:"1"\|"0", Msg, Audio}` |
| `POST /facility/open` | Facility app | `{authCode, door}` or headers `X-Auth-Code` + `X-Door` | `200` · `403` · `400 unknown_door` · `429` · `502` |
| `GET /health` | Ops | — | queue, tokens, last pull/flush, doors, camera, odour, qr_api |
| `POST /door/opened?door=…` / `/door/closed?door=…` | Bench tests | — | only if `DOOR_TEST_ENDPOINTS=1` |

---

## 8. Backend API contract

All calls go **Pi → backend**, with header `ADMIN_AUTH_HEADER: ADMIN_AUTH_SCHEME ADMIN_KEY` (default `Authorization: Bearer <key>`). Timestamps are ISO 8601 UTC (`2026-10-02T10:15:00Z`).

**Every pushed item carries a unique `id` (UUID)**, in the body and as the `Idempotency-Key` header. A retry after a lost reply resends the **same** `id`, so the backend must store the id and treat a repeat as success without saving it twice: reply 2xx and skip it.

**Test without the real backend:** `python3 dev/dummy_backend.py` implements all of these APIs, with a live dashboard (section 12).

### Push sensor: `POST SENSOR_PATH` (every 60 s)

```json
{"id": "7f3c…", "facility": "facility-001", "ts": "…Z",
 "tvoc": 120, "eco2": 450, "aqi": 2, "temperature": 28.4, "humidity": 71.2,
 "nh3_ppm": 3.1, "h2s_ppm": 0.2,
 "camera": {"reachable": true, "streaming": true, "frozen": false, "checked_at": "…"}}
```

Values are `null` when a sensor is warming up, failed, or not fitted.

### Push door-event: `POST DOOR_EVENT_PATH` (per event)

```json
{"id": "b21e…", "facility": "facility-001", "section": "male", "type": "entry", "ts": "…Z", "source": "qr"}
```

| `type` | Extra fields |
|---|---|
| `entry` | `source`: `qr` \| `qr_api` \| `qr_local` |
| `attendant` | `source`: `facility_app` |
| `exit` | `source`: `exit_button` |
| `denied` | `source`: `qr` \| `facility_app`; `reason` (QR only) |
| `door_closed` | `duration_s` |

### Push alert: `POST ALERT_PATH` (per anomaly)

```json
{"id": "c9a0…", "facility": "facility-001", "section": "female", "type": "anomaly", "subtype": "propped_open",
 "ts": "…Z", "duration_s": 300, "repeat": false, "snapshot": "20261002T101500Z.jpg"}
```

| `subtype` | Fields | Backend handling |
|---|---|---|
| `forced_open` | `section`, `snapshot?` | New alarm |
| `propped_open` | `section`, `duration_s`, `repeat`, `snapshot?` (first only) | `repeat:false` = new alarm, `true` = update |
| `propped_resolved` | `section`, `duration_s` | All-clear |
| `odour_high` | `duration_s`, `repeat`, `metrics`, `readings` (no `section`) | `repeat:false` = new alarm, `true` = update |
| `odour_resolved` | `duration_s`, `readings` | All-clear |

### Pull tokens: `GET TOKENS_PATH?facility=<id>` (every 5 min)

```json
{"facilityId": "facility-001", "authCode": "<facility app auth code>",
 "tokens": [{"token": "abc…", "expiresAt": "2026-10-03T00:00:00Z"}, {"token": "def…", "expiresAt": null}]}
```

The reply **replaces** the cache, so an empty list revokes everything. `expiresAt` can be ISO 8601, unix seconds or milliseconds, or null. A reply for a different `facilityId` is ignored. If the pull fails, the cache is kept.

### Optional: QR validation (`QR_API_URL`)

Default request: `POST {"token": "…", "facility": "…", "door": "male"}`. Default reply: `{"allowed": true}`.
The method, field names, extra fields, reply field, allow values and deny status codes are all configurable (`QR_API_*`). A timeout, network error, 5xx or 401 means "unavailable", and the cache decides. **[SETUP.md](SETUP.md) section 8a** shows how to match an existing backend.

### Optional: snapshot upload (`SNAPSHOT_UPLOAD_PATH`)

Multipart `POST` with field `file` (image/jpeg) plus form fields `id`, `facility`, `name` and `ts`. It's queued **before** its alert, so the image arrives first. The alert's `snapshot` equals `name`.

### Retry rules (all pushes)

| Backend reply | Pi does |
|---|---|
| 2xx | Delivered, removed from the queue (a duplicate `id` should also get 2xx) |
| Network error, 5xx, 401, 403, 408, 429 | Keeps it, pauses, retries every `FLUSH_INTERVAL` |
| Other 4xx | Retries 5 times, then drops that one item (logged) so it can't block the queue |

---

## 9. Offline behaviour and data

| Situation | Behaviour |
|---|---|
| Internet / backend down | Cached and Pi-made codes open; Facility app opens; events queue in SQLite |
| Backend back | Queue drains oldest-first; nothing lost |
| QR API down | Falls back to the cache within `QR_API_TIMEOUT` (3 s) |
| Relay board down | Unlock returns `502`, nothing is logged as entry; exit buttons still work in hardware |
| Pi off or crashed | Exit buttons and locks work in hardware; the watchdog reboots a hung Pi; the service auto-restarts |
| Power cut | Locks fail open (safe); the UPS keeps the Pi, router and camera up; SQLite WAL survives hard cuts |
| Queue over `MAX_QUEUE` (200 000) | Oldest **sensor readings** are dropped first; access log and alerts are kept |

Data on the Pi (`/var/lib/facility/`):
- `facility.db`: token cache, Auth Code (SHA-256 only), Pi-made codes, event queue
- `snapshots/`: JPEGs, deleted after `SNAPSHOT_KEEP_DAYS`
- `qr/`: images from `facility qr create`

---

## 10. Security

- **Network:** port 5454 is open only to the facility LAN (ufw). There are no inbound internet ports; use a Cloudflare Tunnel for remote SSH.
- **Scanner routing:** only the configured scanner IPs can trigger a QR unlock, and each opens only its own door.
- **Facility app:** uses a per-facility Auth Code, never the Facility ID. Only the hash is stored, and repeated failures are rate-limited per IP.
- **Secrets:** `.env` is `640 root:facility`. The service runs as the unprivileged `facility` user with systemd hardening (`ProtectSystem=strict`, `NoNewPrivileges`). The relay password lives in the board's `board_secrets.py`, never in the repo.
- **Relay firmware:** returns 401 on a wrong password and never logs it.
- **Pi-made codes:** expire after 24 h by default. `--no-expiry` needs an explicit flag and shows a warning. Revoke them with `facility qr revoke`.
- **Dummy QR API (`dev/`):** for testing only. The node and `facility doctor` both warn when `QR_API_URL` points at localhost.
- **Before handover:** work through the checklist in SETUP.md section 13.

---

## 11. Operations

| Task | Command |
|---|---|
| Is it healthy? | `facility doctor` · `facility health` |
| Live log | `facility logs` |
| Restart | `facility restart` |
| Change settings | `facility config` |
| Test / staff QR codes | `facility qr create --label test` · `facility qr list` · `facility qr revoke --label test` |
| Calibrate MQ sensors | `facility mq calibrate` |
| Update | `git pull` (auto-reinstalls after bootstrap) or `facility update`; scp copies: `sudo bash deploy/install.sh` |
| Backup / spare Pi / uninstall | INSTALL.md sections 14–15 |

---

## 12. Development

Runs on any machine with Python 3.9+. No Pi or hardware is needed for the tests.

```bash
cd facility-node
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_door.py            # 112 tests
```

The test suite fakes the relay, the backend and the clock. It covers:
- door decisions, offline queueing, two-door routing;
- every scanner format;
- QR API orders and fallbacks;
- odour timing, MQ maths, snapshot upload;
- config validation and the doctor.

**Run the whole node locally against fakes:** start `dev/dummy_qr_api.py` (QR API on :8090), point `RELAY_URL` / `ADMIN_URL` at local stubs, set `HOST=127.0.0.1` and `CAM_RTSP=`, then run `python app.py`. The sensors log I2C errors and report `null`, which is expected off-Pi.

**Dummy admin backend:** `python3 dev/dummy_backend.py` serves all the backend APIs on :8091 (token pull, sensor, door-event, alert, snapshot, QR validate). It checks auth, rejects missing fields with 422, ignores duplicate ids, and can simulate an outage. Open `http://127.0.0.1:8091/` for a live dashboard of everything the node sent. Point a node at it with `ADMIN_URL=http://127.0.0.1:8091/api` and `ADMIN_KEY=dummy-key`.

**Dummy QR codes:** `dev/qr/DUMMY-ALLOW-*.png` are approved by the dummy API, and `DUMMY-DENY-0001.png` is refused. Regenerate them with `python dev/make_dummy_qr.py`.

---

## 13. Project layout

```
facility-node/
├── app.py              Flask endpoints, background jobs, entry point
├── config.py           Settings from .env (validation, door map, QR API, odour, MQ)
├── door.py             Per-door unlock decisions, classification, propped/forced alerts
├── store.py            SQLite: tokens, Pi-made codes, Auth Code hash, outbox
├── uplink.py           Backend client: queue-first pushes, snapshot upload, token pull
├── qr_api.py           Configurable online QR validation
├── relay.py            KC868-A4S HTTP client
├── inputs.py           Reed switches (gpiozero/lgpio), exit-input poller, door ticker
├── sensors_camera.py   ENS160/AHT20, camera probe / frame check / snapshots
├── odour.py            Odour alert state machine
├── mq.py               ADS1115 + MQ-135/MQ-136; calibrate / read CLI
├── qr_tool.py          Pi-made unlock QR codes CLI
├── doctor.py           Installation self-check
├── test_door.py        Test suite
├── requirements.txt    flask, requests, smbus2, qrcode, pypng
├── .env.example        Every setting, documented
├── deploy/
│   ├── install.sh      Installer / updater
│   ├── bootstrap.sh    One-time: enable git hooks + install
│   ├── githooks/       post-merge / post-rewrite → reinstall on pull
│   ├── uninstall.sh
│   ├── facility        `facility` helper command
│   └── facility-node.service
├── firmware/kc868/
│   ├── main.py         Patched relay firmware (MicroPython)
│   └── board_secrets.py.example
├── dev/
│   ├── dummy_backend.py Dummy admin backend: all APIs + dashboard (testing only)
│   ├── dummy_qr_api.py Dummy QR validation API (testing only)
│   ├── make_dummy_qr.py
│   └── qr/             DUMMY-ALLOW-000{1,2,3}.png, DUMMY-DENY-0001.png
├── README.md · INSTALL.md · SETUP.md
```

---

## 14. Open items

| Item | Status |
|---|---|
| Backend API paths and auth header | Defaults are placeholders; set `*_PATH` / `ADMIN_AUTH_*` |
| Backend `authCode` in the token pull | Needed for Facility-app unlock |
| Backend accepts `section`, `reason`, `nh3_ppm`, `h2s_ppm`, odour alerts | Additive fields |
| QR validation API | Optional; configure `QR_API_*` when it exists |
| Snapshot upload endpoint | Optional; set `SNAPSHOT_UPLOAD_PATH` |
| Scanner output format | `/qr`, `/qrscanner` and `/rakindaqrscanner` all accepted; remove the unused ones once known |
| Hardware validation | I2C sensor reads, gpiozero reed, ffmpeg camera checks and the installer still need a run on a real Pi 5 |
