# Facility Node

Offline-first controller for one public facility, running on a single **Raspberry Pi 5**. It has three functions, each switched on or off on its own (`ENABLE_DOORS`, `ENABLE_CAMERA`, `ENABLE_ODOUR`):

| # | Function | What it does | Code |
|---|---|---|---|
| 1 | **Door lock / unlock** | Unlocks two doors (male and female) by QR code or attendant app, logs exits, detects forced and propped doors | `door/` |
| 2 | **Camera monitoring** | Checks the IP camera is reachable and its feed live; snapshots on door alerts | `camera/` |
| 3 | **Odour monitoring** | Reads TVOC, eCO2, temperature and humidity, scores TVOC 0–100, raises odour alerts | `odour/` |

Everything is reported to the admin backend.

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
 QR male .101   QR female .102   Raspberry Pi 5 .104   KC868-A4S .174   IP camera .149
     │ POST /qr       │ POST /qr      │ :5454             │ :80             │ RTSP :554
     └────────────────┴─────────────► │ ── sw_ctl.cgi ──► │ Relay01 → male timer → male lock
                                      │                   │ Relay02 → female timer → female lock
 Facility app (phone) ─ POST ───────► │ ◄─ input_ctl.cgi ─│ Input01 ← male exit button (pole 2)
   /facility/open                     │                   │ Input02 ← female exit button (pole 2)
                                      │ ◄──────────── snapshot / health ──────────────┘
                                      │ I2C: AHT20 0x38, ENS160 0x53, [ADS1115 0x48 → MQ-135, MQ-136]
                                      │ GPIO27: male reed      GPIO22: female reed
```

Inside the Pi, a single Python service (`facility-node`, run by systemd). Each function only starts its threads when it is on:

```
 1 door/    Flask /qr /facility/open ──► DoorController (one per door) ──► Relay (KC868 HTTP)
                                            │  QR: local cache ⇄ optional QR API
            threads: exit-input poller · reed watcher · door ticker · token pull (5 min)
 2 camera/  threads: camera probe (30 s) · frame check (5 min) · snapshot on door alerts
 3 odour/   thread:  sensor loop (60 s): ENS160/AHT20 (I2C or WiFi board) [+ MQ] → TVOC score → odour monitor

 shared     Store (SQLite): token cache · Pi-made codes · Auth Code hash · outbox queue
            Uplink: outbox ──► 4 admin APIs (+ snapshot upload) · thread: queue flush
```

---

## 2. Features

| Function | Feature | What it does |
|---|---|---|
| 1 Door | **QR unlock** | Each scanner opens its own door (routed by scanner IP). Checked against the local cache, optionally plus a live **QR validation API**. Any valid code opens either door |
| 1 Door | **Paid entry** | `QR_API_ORDER=api_only`: every scan is checked online, so the backend can check the balance and charge. No internet = QR refused. One charge per scan (`scan_id`), a refund request if the door then fails to open |
| 1 Door | **Facility app unlock** | Attendant phone sends the per-facility **Auth Code** and the door name |
| 1 Door | **Pi-made QR codes** | `facility qr create`: test, staff and commissioning codes that expire after 24 h by default |
| 1 Door | **Exit tracking** | Exit button opens the door in hardware; a second contact into the KC868 lets the Pi log an `exit` |
| 1 Door | **Door anomalies** | Reed switch per door: `forced_open` (opened with no trigger), `propped_open` (repeats while open), `propped_resolved` |
| 2 Camera | **Camera health** | Reachability probe, frozen-feed detection; set up with `facility camera` |
| 2 Camera | **Snapshots** | JPEG on door anomalies, optional upload |
| 3 Odour | **Air quality** | ENS160 (TVOC, eCO2, AQI) + AHT20 (temperature, humidity) every 60 s, on the Pi's I2C or an ESP32-S3 WiFi board (`facility sensor`) |
| 3 Odour | **TVOC score + odour alert** | TVOC as 0–100 with five levels; `odour_high` / `odour_resolved` alerts against per-site limits; optional MQ-135 (ammonia) and MQ-136 (H2S) through an ADS1115 |
| all | **Function switches** | `facility feature camera off`: a site runs only the functions it has hardware for |
| all | **Offline-first** | Doors open with no internet; events queue on the Pi and drain oldest-first when the backend is back |
| all | **Self-check** | `facility doctor` checks config, backend and every function that is on; `facility doctor door` (`camera`, `odour`) checks one |
| all | **Resilience** | Hardware watchdog, auto-restart, survives power cuts (SQLite WAL), warm-spare procedure |

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

| Function | Item | Notes |
|---|---|---|
| shared | Raspberry Pi 5 (4 GB) + endurance microSD + 5V/5A supply | Runs everything |
| shared | Router + mini DC-UPS | The UPS powers the Pi, router and camera, **not** the locks |
| 1 Door | KC868-A4S relay board | Patched firmware in `firmware/kc868/` |
| 1 Door | Per door ×2: timer relay module, 12V mag lock, DPDT exit button, QR scanner, MC-38 reed (optional) | Locks fail open on power loss |
| 2 Camera | IP camera (existing) | RTSP |
| 3 Odour | ENS160 + AHT20 | On the Pi's I2C, **or** on an ESP32-S3 Feather WiFi board (`firmware/air-sensor/`) |
| 3 Odour | ADS1115 + MQ-135 + MQ-136 (optional) | I2C + analog, with a voltage divider on each MQ output |

### IP plan

| Device | Function | IP | Port |
|---|---|---|---|
| Router / gateway | shared | 192.168.10.1 | — |
| Raspberry Pi 5 | shared | 192.168.10.104 | 5454 (LAN only) |
| QR scanner male / female | 1 Door | 192.168.10.101 / .102 | → Pi :5454 |
| KC868-A4S | 1 Door | 192.168.10.174 | 80 |
| IP camera | 2 Camera | 192.168.10.149 | 554 |
| Air sensor board (ESP32-S3, optional) | 3 Odour | 192.168.10.105 | 80, polled by the Pi |

All of these are static and **outside the DHCP pool**. Only phones use DHCP.

### Door map (default `.env`)

| Door | Scanner | Relay | Exit input | Reed GPIO (pin) | Unlock time |
|---|---|---|---|---|---|
| male | .101 | Relay01 | Input01 | 27 (pin 13) | 5 s |
| female | .102 | Relay02 | Input02 | 22 (pin 15) | 5 s |

Each door's lock power runs through the KC868 relay's NC contact **and** the timer module's NC contact, in series: QR and app unlocks use the KC868 (time set in code), the exit button uses the timer module (its own delay, works with the Pi off). Wiring: SETUP.md section 4.1.

---

## 5. How decisions are made

Sections 5.1–5.3 are the door function, 5.4 the odour function.

### 5.1 QR scan → door

1. **Pick the door:** by the scanner's source IP (`DOOR_<NAME>_SCANNER_IPS`). An unknown IP gets `403 unknown_scanner` and nothing opens.
2. **Read the code:** from JSON (`token`, `SCode`, `cardid`, `code`…), form fields, the query string, or a plain-text body.
3. **Validate:**

| Code | No QR API | `QR_API_ORDER=local_first` (default) | `api_first` | `api_only` (paid entry) |
|---|---|---|---|---|
| Pi-made (`facility qr`) | ✓ local | ✓ local | ✓ local | ✓ local (staff codes, no charge) |
| In backend cache | ✓ | ✓, no API call | API decides; cache only if API down | API decides; **✗ if API down** |
| Not cached | ✗ | API decides; ✗ if API down | API decides; ✗ if API down | API decides; ✗ if API down |

Every API call carries a fresh `scan_id`. A scanner repeating the same code at the same door within `QR_DEDUP_SECONDS` (10 s) is the same scan: no second API call (no second charge) and no second event; an open door just stays open longer.

4. **Open the door:** the Pi asks the KC868 to hold this door's relay for `DOOR_<NAME>_UNLOCK_SECONDS` (default 5 s), which cuts the lock's power; the board releases it by itself. Then it logs a door-event:
   - `entry` with `source` = `qr` (cache), `qr_api` (approved online) or `qr_local` (Pi-made);
   - or `denied` with `reason` = `unknown`, `api_denied` (plus the backend's `api_reason`, e.g. `insufficient_balance`), `offline` (`api_only` with the API unreachable) or `empty`;
   - or, if the relay fails after the backend approved (charged) the scan, `relay_failed` with its `scan_id`, so the backend can refund.

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

Every reading also carries **`tvoc_score`** (0–100) and **`tvoc_level`**, worked out on the Pi from TVOC. Each 20 points is one level of the German UBA TVOC guidance, the same scale the ENS160's own AQI uses; inside a level the score is a straight line:

| Score | Level (`tvoc_level`) | TVOC (ppb) |
|---|---|---|
| 0–20 | `very_good` | 0–220 |
| 21–40 | `good` | 220–660 |
| 41–60 | `moderate` | 660–2200 |
| 61–80 | `dangerous` | 2200–5500 |
| 81–100 | `very_dangerous` | 5500–11000 and above (capped at 100) |

Any metric (`tvoc`, `tvoc_score`, `aqi`, `nh3_ppm`, `h2s_ppm`) at or above its limit for `ODOUR_HOLD` (600 s) → `odour_high`, repeated every `ODOUR_REPEAT` (1800 s) while still high. All metrics below their limits for `ODOUR_CLEAR_HOLD` (300 s) → `odour_resolved`. Missing readings (warm-up or failure) neither start nor clear an alert.

---

## 6. Configuration

All settings live in `/opt/facility-node/.env`, loaded by systemd. **`.env.example` documents every key.** Edit with `facility config`. The service refuses to start on a missing secret or a conflicting door map, and says why.

| Function | Group | Keys | Notes |
|---|---|---|---|
| shared | Identity | `FACILITY_ID` | required |
| shared | **Functions** | `ENABLE_DOORS`, `ENABLE_CAMERA`, `ENABLE_ODOUR` | `1` (default) / `0`; at least one on. An off function's keys below aren't required. `facility feature` |
| shared | Backend | `ADMIN_URL`, `ADMIN_KEY`, `ADMIN_AUTH_HEADER`, `ADMIN_AUTH_SCHEME`, `SENSOR_PATH`, `DOOR_EVENT_PATH`, `ALERT_PATH`, `TOKENS_PATH` | URL and key required |
| shared | Storage | `DB_PATH`, `MAX_QUEUE` | |
| shared | Timing | `SENSOR_INTERVAL`, `PULL_INTERVAL`, `FLUSH_INTERVAL` | seconds |
| shared | HTTP | `HOST`, `PORT` | |
| 1 Door | Relay | `RELAY_URL`, `RELAY_PWD` | `RELAY_PWD` required while doors are on |
| 1 Door | Doors | `DOORS`, `DOOR_<NAME>_RELAY`, `_EXIT_INPUT`, `_REED_GPIO`, `_SCANNER_IPS`, `EXIT_POLL_INTERVAL` | e.g. `DOORS=male,female` |
| 1 Door | Unlock time | `UNLOCK_SECONDS` (all doors), `DOOR_<NAME>_UNLOCK_SECONDS` | 1–30 s, default 5; `facility door delay male 8` |
| 1 Door | Reed | `REED`, `REED_ACTIVE_HIGH`, `PROPPED_THRESHOLD`, `PROPPED_REPEAT`, `CLASSIFY_WINDOW` | |
| 1 Door | QR validation | `QR_API_URL`, `_METHOD`, `_ORDER`, `_TIMEOUT`, `_AUTH`, `_TOKEN_FIELD`, `_FACILITY_FIELD`, `_DOOR_FIELD`, `_EXTRA`, `_ALLOW_FIELD`, `_ALLOW_VALUES`, `_DENY_STATUS`, `_SCAN_ID_FIELD`, `_REASON_FIELD`, `QR_DEDUP_SECONDS` | off when `QR_API_URL` is empty; `QR_API_ORDER=api_only` = paid entry |
| 1 Door | Attendant purposes | `ATTENDANT_PURPOSES` | what personal use may be for; default `cleaning,bath,toilet,maintenance` |
| 1 Door | Scanner messages | `SCANNER_MSG_OPENED`, `_NO_BALANCE`, `_OFFLINE`, `_DENIED`, `_ERROR` | what the scanner shows; English defaults |
| 1 Door | Bench test | `DOOR_TEST_ENDPOINTS` | keep `0` in production |
| 2 Camera | Camera | `CAM_HOST`, `CAM_PORT`, `CAM_RTSP`, `CAM_PROBE_INTERVAL`, `CAM_FRAME_INTERVAL` | set with `facility camera` |
| 2 Camera | Snapshots | `SNAPSHOT_DIR`, `SNAPSHOT_UPLOAD_PATH`, `SNAPSHOT_KEEP_DAYS` | upload off when the path is empty |
| 3 Odour | Air sensor board | `SENSOR_URL`, `SENSOR_TIMEOUT` | empty URL = ENS160/AHT20 on the Pi's I2C; set with `facility sensor enable` |
| 3 Odour | Odour alert | `ODOUR_TVOC_SCORE_LIMIT`, `ODOUR_TVOC_LIMIT`, `_AQI_LIMIT`, `_NH3_LIMIT`, `_H2S_LIMIT`, `ODOUR_HOLD`, `_REPEAT`, `_CLEAR_HOLD` | empty limit = metric off |
| 3 Odour | MQ sensors | `MQ`, `ADS1115_ADDR`, `MQ135_CHANNEL`, `MQ136_CHANNEL`, `MQ135_R0`, `MQ136_R0`, `MQ_RL_KOHM`, `MQ_DIVIDER`, `MQ_VC` | R0 from `facility mq calibrate` |

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

`POST /facility/open` also takes `use` = `customer` (cash at the counter, optional `amount`) or `personal` (with `purpose`, one of `ATTENDANT_PURPOSES`), and an optional `attendant` (also as headers `X-Use`, `X-Purpose`, `X-Attendant`). A bad value → `400` with `reason` `bad_use` / `bad_purpose` / `bad_amount`; no `use` = an older app, accepted.

Scan replies also say why, for scanners with a display: `/qr` adds `reason` and `message`, `/qrscanner` adds `message`, and the Rakinda reply's `Msg` is the message. The message comes from the reason: opened, `insufficient_balance` (also `insufficient_funds`, `no_balance`, `low_balance`) → no balance, `offline` → no internet, `relay_error` → door error, anything else → access denied. Each text is set with `SCANNER_MSG_*`.

With `ENABLE_DOORS=0` every door endpoint answers `404 {"result": "doors_off"}`. `/health` always answers and includes `features: {doors, camera, odour}`; a function that is off shows `null` (camera, odour) or `{}` (doors).

---

## 8. Backend API contract

All calls go **Pi → backend**, with header `ADMIN_AUTH_HEADER: ADMIN_AUTH_SCHEME ADMIN_KEY` (default `Authorization: Bearer <key>`). Timestamps are ISO 8601 UTC (`2026-10-02T10:15:00Z`).

**Every pushed item carries a unique `id` (UUID)**, in the body and as the `Idempotency-Key` header. A retry after a lost reply resends the **same** `id`, so the backend must store the id and treat a repeat as success without saving it twice: reply 2xx and skip it.

**Test without the real backend:** `python3 dev/dummy_backend.py` implements all of these APIs, with a live dashboard (section 12).

### Push sensor: `POST SENSOR_PATH` (every 60 s)

```json
{"id": "7f3c…", "facility": "facility-001", "ts": "…Z",
 "tvoc": 120, "tvoc_score": 11, "tvoc_level": "very_good",
 "eco2": 450, "aqi": 2, "temperature": 28.4, "humidity": 71.2,
 "nh3_ppm": 3.1, "h2s_ppm": 0.2,
 "camera": {"reachable": true, "streaming": true, "frozen": false, "checked_at": "…"}}
```

Values are `null` when a sensor is warming up, failed, or not fitted. `tvoc_score` (0–100) and `tvoc_level` come from `tvoc` (section 5.4) and are `null` with it. With `ENABLE_ODOUR=0` every air value is `null`; with `ENABLE_CAMERA=0`, `camera` is `null`. The push runs while either is on.

### Push door-event: `POST DOOR_EVENT_PATH` (per event)

```json
{"id": "b21e…", "facility": "facility-001", "section": "male", "type": "entry", "ts": "…Z", "source": "qr"}
```

| `type` | Extra fields |
|---|---|
| `entry` | `source`: `qr` \| `qr_api` \| `qr_local`; `scan_id` when the API approved it |
| `attendant` | `source`: `facility_app`; `use`: `customer` (+ `payment: "cash"`, `amount`?) or `personal` (+ `purpose`: `cleaning` \| `bath` \| `toilet` \| `maintenance`); `attendant`? (who pressed it) |
| `exit` | `source`: `exit_button` |
| `denied` | `source`: `qr` \| `facility_app`; `reason` (QR only: `unknown`, `api_denied`, `offline`, `empty`); `api_reason` and `scan_id` when the API refused |
| `relay_failed` | `source`, and `scan_id` (a charged QR scan) or the cash-sale fields (`use: customer`, `payment`, `amount`): it was paid but the relay failed: **refund / void it** |
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
| `odour_high` | `duration_s`, `repeat`, `metrics` (e.g. `["tvoc_score"]`), `readings` (no `section`) | `repeat:false` = new alarm, `true` = update |
| `odour_resolved` | `duration_s`, `readings` | All-clear |

### Pull tokens: `GET TOKENS_PATH?facility=<id>` (every 5 min)

```json
{"facilityId": "facility-001", "authCode": "<facility app auth code>",
 "tokens": [{"token": "abc…", "expiresAt": "2026-10-03T00:00:00Z"}, {"token": "def…", "expiresAt": null}]}
```

The reply **replaces** the cache, so an empty list revokes everything. `expiresAt` can be ISO 8601, unix seconds or milliseconds, or null. A reply for a different `facilityId` is ignored. If the pull fails, the cache is kept.

### Optional: QR validation (`QR_API_URL`)

Default request: `POST {"token": "…", "facility": "…", "door": "male", "scan_id": "<uuid>"}`, with header `Idempotency-Key: <scan_id>`. Default reply: `{"allowed": true}`, or `{"allowed": false, "reason": "insufficient_balance"}` (HTTP 200 or 402).

**Paid entry (`QR_API_ORDER=api_only`), what the backend must do:**
- Check the balance and **charge in the same call**, then reply `allowed`.
- **Charge each `scan_id` once.** Asked again with the same `scan_id` (a lost reply), return the first answer without charging again.
- **Refund** a scan when a `relay_failed` door-event with its `scan_id` arrives. Ideally also when no `entry` with that `scan_id` arrives within a few minutes (the Pi crashed mid-scan).
- Put the refusal reason in `reason` (`insufficient_balance`, `expired`, …); the Pi logs it as `api_reason`.

`dev/dummy_backend.py` does all of this (`--balance N`), for testing.

The method, field names, extra fields, reply field, allow values and deny status codes are all configurable (`QR_API_*`). A timeout, network error, 5xx or 401 means "unavailable", and the cache decides. **[SETUP.md](SETUP.md) section 4.6** shows how to match an existing backend.

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
| QR API down | `local_first` / `api_first`: falls back to the cache within `QR_API_TIMEOUT` (3 s). `api_only` (paid entry): **QR refused** (`reason: offline`); Pi-made staff codes, the Facility app and the exit buttons still open |
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
- **Before handover:** work through the checklist in SETUP.md section 11.

---

## 11. Operations

| Task | Command |
|---|---|
| Is it healthy? | `facility doctor` · `facility doctor door` (`camera`, `odour`) · `facility health` |
| Functions on/off | `facility feature` · `facility feature camera off` |
| Door unlock time | `facility door` · `facility door delay male 8` |
| Live log | `facility logs` |
| Restart | `facility restart` |
| Change settings | `facility config` |
| Test / staff QR codes | `facility qr create --label test` · `facility qr list` · `facility qr revoke --label test` |
| Calibrate MQ sensors | `facility mq calibrate` |
| Air sensor board (ESP32) | `facility sensor flash` · `facility sensor enable` · `facility sensor test` |
| Set up / check the camera | `facility camera` · `facility camera test` · `facility camera find` |
| Update | `git pull` (auto-reinstalls after bootstrap) or `facility update`; scp copies: `sudo bash deploy/install.sh` |
| Backup / spare Pi / uninstall | INSTALL.md sections 14–15 |

---

## 12. Development

Runs on any machine with Python 3.9+. No Pi or hardware is needed for the tests.

```bash
cd facility-node
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_door.py            # 153 tests
```

The test suite fakes the relay, the backend and the clock. It covers:
- door decisions, offline queueing, two-door routing;
- every scanner format;
- QR API orders and fallbacks;
- odour timing, MQ maths, snapshot upload;
- config validation, the function switches and the doctor.

**Run the whole node locally against fakes:** start `dev/dummy_qr_api.py` (QR API on :8090), point `RELAY_URL` / `ADMIN_URL` at local stubs, set `HOST=127.0.0.1` and `CAM_RTSP=`, then run `python app.py`. The sensors log I2C errors and report `null`, which is expected off-Pi.

**Dummy admin backend:** `python3 dev/dummy_backend.py` serves all the backend APIs on :8091 (token pull, sensor, door-event, alert, snapshot, QR validate). It checks auth, rejects missing fields with 422, ignores duplicate ids, and can simulate an outage. Open `http://127.0.0.1:8091/` for a live dashboard of everything the node sent. Point a node at it with `ADMIN_URL=http://127.0.0.1:8091/api` and `ADMIN_KEY=dummy-key`.

**Dummy QR codes:** `dev/qr/DUMMY-ALLOW-*.png` are approved by the dummy API, and `DUMMY-DENY-0001.png` is refused. Regenerate them with `python dev/make_dummy_qr.py`.

---

## 13. Project layout

```
facility-node/
├── app.py              (shared) Flask endpoints, starts each function's jobs, entry point
├── config.py           (shared) Settings from .env: function switches, validation, door map
├── store.py            (shared) SQLite: tokens, Pi-made codes, Auth Code hash, outbox
├── uplink.py           (shared) Backend client: queue-first pushes, snapshot upload, token pull
├── doctor.py           (shared) Installation self-check, per function
├── door/               1. Door lock / unlock
│   ├── controller.py   Per-door unlock decisions, classification, propped/forced alerts
│   ├── relay.py        KC868-A4S HTTP client (relay hold = unlock time)
│   ├── inputs.py       Reed switches (gpiozero/lgpio), exit-input poller, door ticker
│   ├── qr_api.py       Configurable online QR validation
│   └── qr_tool.py      Pi-made unlock QR codes CLI (facility qr)
├── camera/             2. Camera monitoring
│   └── camera.py       Probe, frozen-feed check, snapshots
├── odour/              3. Odour monitoring
│   ├── sensors.py      ENS160/AHT20 (I2C or WiFi board), the 1-minute sensor push
│   ├── monitor.py      TVOC 0-100 score, odour alert state machine
│   └── mq.py           ADS1115 + MQ-135/MQ-136; calibrate / read CLI (facility mq)
├── test_door.py        Test suite
├── requirements.txt    flask, requests, smbus2, qrcode, pypng
├── .env.example        Every setting, documented
├── deploy/
│   ├── install.sh      Installer / updater
│   ├── bootstrap.sh    One-time: enable git hooks + install
│   ├── githooks/       post-merge / post-rewrite → reinstall on pull
│   ├── uninstall.sh
│   ├── facility        `facility` helper command
│   ├── feature.sh      `facility feature`: switch a function on/off
│   ├── door-setup.sh   `facility door`: unlock time per door
│   ├── camera-setup.sh `facility camera`
│   ├── sensor-setup.sh `facility sensor`
│   └── facility-node.service
├── firmware/air-sensor/   (3. odour)
│   ├── main.py         ESP32-S3 + ENS160/AHT20 over WiFi: GET /sensor (MicroPython)
│   └── board_secrets.py.example  WiFi name, password and IP (real file stays on the board)
├── firmware/kc868/        (1. door)
│   ├── main.py         Patched relay firmware (MicroPython): hold time, inputs, 401
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
