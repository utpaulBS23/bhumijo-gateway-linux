# Facility node: API flow

How every request moves between the apps, the Raspberry Pi (facility node) and the backend. Written for the backend and app developers. The exact field-by-field contract is [README.md](README.md) sections 7 and 8; this document shows **who calls whom, when, and what happens on each answer**.

## Contents

1. [Who talks to whom](#1-who-talks-to-whom)
2. [Customer entry: Jabo Khotai QR](#2-customer-entry-jabo-khotai-qr)
3. [Staff entry: Facility app](#3-staff-entry-facility-app)
4. [Exit button](#4-exit-button)
5. [Door watching: forced and propped-open alerts](#5-door-watching-forced-and-propped-open-alerts)
6. [Air quality and camera health](#6-air-quality-and-camera-health)
7. [Settings from the admin panel (pull every 5 min)](#7-settings-from-the-admin-panel-pull-every-5-min)
8. [How the Pi delivers to the backend](#8-how-the-pi-delivers-to-the-backend)
9. [Endpoint summary and status](#9-endpoint-summary-and-status)
10. [Open questions for the backend](#10-open-questions-for-the-backend)

---

## 1. Who talks to whom

```
                         INTERNET                                   FACILITY LAN (192.168.10.0/24)
 ┌───────────────────────────────────────┐        ┌──────────────────────────────────────────────────────┐
 │ Backend  stage-api.bhumijo…/api        │        │  Raspberry Pi 5 — facility node, port 5454           │
 │  • /qr/validate     (QR check+charge) ◄├────────┤  • asks the backend about each customer QR           │
 │  • settings pull    (config)          ◄├────────┤  • pulls settings every 5 min                        │
 │  • sensor / door-event / alert / snap ◄├────────┤  • pushes everything it sees (queued on the SD card) │
 │ Admin panel (office staff)             │        │                                                      │
 └───────────────────────────────────────┘        │   ▲ POST /qr           ▲ POST /facility/open          │
                                                   │   │                    │                              │
 Jabo Khotai app (customer phone)                  │ QR scanners        Facility app (attendant phone,    │
  shows a QR ──────────── scanned at the gate ─────┤ male .101          on the facility WiFi)             │
                                                   │ female .102                                          │
                                                   │                                                      │
                                                   │ Pi ──► KC868 relay board .174 ──► door locks         │
                                                   │ Pi ──► cameras (RTSP)   Pi ◄── air sensor board .105 │
                                                   │ Pi ◄── reed switches (GPIO) on each door             │
                                                   └──────────────────────────────────────────────────────┘
```

Rules that shape every flow:

- **Every call goes Pi → backend.** The backend never calls the Pi (it is behind the router, LAN only).
- **The apps never call the backend to open a door.** The scanner and the Facility app talk to the Pi; the Pi decides and fires the relay.
- **Offline-first.** Everything the Pi records is saved on its SD card first and sent later. Only the customer QR check needs the internet (the backend charges per entry).
- **Doors by device id.** Each door (gate) has a device id from the admin panel, like `BHU-001`, set on the Pi as `DOOR_<NAME>_ID`. It is the `door` in every call. On the Pi, doors are wired as `male` and `female`.

---

## 2. Customer entry: Jabo Khotai QR

The customer opens the Jabo Khotai app, which shows a QR holding their `device_access_token` (e.g. `BHU-62AB…`). They hold it up to the scanner at the gate.

```mermaid
sequenceDiagram
    autonumber
    participant C as Customer (Jabo Khotai app)
    participant S as QR scanner (male .101)
    participant P as Pi (facility node)
    participant B as Backend /qr/validate
    participant U as Backend /user/accesses logic
    participant R as KC868 relay
    C->>S: shows QR (device_access_token)
    S->>P: POST /qr {token}  (from 192.168.10.101 → door "male")
    P->>B: POST /qr/validate<br/>Device-Auth-Code: <facility code><br/>{token, facility, door:"BHU-001", scan_id}
    B->>B: door → service_id, gender, price
    B->>U: {service_id, gender, device_access_token: token}
    U-->>B: granted (charged) / refused
    alt granted
        B-->>P: 200 {"message": "Service unlocked"}
        P->>R: hold relay 1 for 5 s
        P-->>S: 200 {result:"opened", message:"Please Enter the facility"}
        P--)B: door-event entry (door, scan_id, price)  [queued]
    else refused
        B-->>P: 4xx {"message": "insufficient_balance"}
        P-->>S: 403 {result:"denied", message:"Insufficient balance, please top up"}
        P--)B: door-event denied (reason api_denied, api_reason, scan_id)  [queued]
    else no internet / timeout (5 s) / 5xx / 401
        P-->>S: 403 {result:"denied", reason:"offline", message:"No internet, please try again"}
        P--)B: door-event denied (reason offline)  [queued]
    end
```

**Step by step**

1. **Which door.** The Pi picks the door from the scanner's IP (`.101` = male, `.102` = female). A scan from any other IP is refused (`403 unknown_scanner`); nothing opens.
2. **Read the code.** From JSON (`token`, `SCode`, `cardid`, `code`…), a form field, the query string or a plain-text body. Scanners may post to `/qr`, `/api/device/code`, `/qrscanner` or `/rakindaqrscanner`: same logic, each answered in that scanner's format.
3. **Same code again within 10 s** (scanners repeat): treated as the same scan. No second backend call, **no second charge**; an open door just stays open.
4. **The Pi asks the backend:** `POST {ADMIN_URL}/qr/validate`

   ```http
   POST /api/qr/validate
   Device-Auth-Code: <the facility's Auth Code>
   Idempotency-Key: 6f1c…-uuid
   Content-Type: application/json

   {"token": "<customer QR token>", "facility": "995", "door": "BHU-001",
    "scan_id": "6f1c…-uuid"}
   ```

   - `Device-Auth-Code`: one per facility (admin panel → Device List), kept in the Pi's `.env`. It identifies the facility; the admin key is not sent on this call.
   - `token`: exactly what the scanner read (the customer's `device_access_token`).
   - `facility`: the Pi's `FACILITY_ID`, the facility's id in the admin panel (e.g. `995`).
   - `door`: the door's device id (`DOOR_<NAME>_ID`, e.g. `BHU-001`).
   - `scan_id` / `Idempotency-Key`: a new random id for every scan. **Not a credential** (that's `Device-Auth-Code`): it lets the backend charge a scan once even if the Pi has to ask twice, and a `relay_failed` event names it for the refund.
5. **Inside `/qr/validate` (backend):**
   - find the gate with device id `door` → its `service_id`, `gender` and price (set in the admin panel; the Pi doesn't keep them);
   - run the `/user/accesses` logic with `{service_id, gender, device_access_token: token}`: check the balance and charge;
   - **charge each `scan_id` once:** asked again with the same `scan_id` (the Pi's reply got lost), answer the first result without charging again;
   - answer the Pi as below.
6. **The answer decides:**

   | `/qr/validate` reply | Pi does | Scanner shows |
   |---|---|---|
   | **200** `{"message": "Service unlocked"}` | opens the gate (relay held 5 s) | "Please Enter the facility" |
   | **4xx** (400 / 402 / 403 / 404 / 410 / 422) `{"message": "<reason>"}`: `402 insufficient_balance`, `403 invalid_token` / `user_blocked`, `400 recent_usage` | stays shut; logs `message` as `api_reason` | "Insufficient balance, please top up" if `message` is `insufficient_balance` (or `insufficient_funds`, `no_balance`, `low_balance`), else "Access denied" |
   | 200 with any other `message` | stays shut | "Access denied" |
   | **401** `invalid_device_code`, or **404** `unknown_door` | stays shut (`reason: setup_error`); logs an error: check `DEVICE_AUTH_CODE` / `DOOR_<NAME>_ID` (the Pi's setup is wrong, not the customer's code) | "Door error, please call the attendant" |
   | timeout (5 s), network error, 5xx (also `503 processing`) | stays shut (`reason: offline`) | "No internet, please try again" |

   The text is compared ignoring upper/lower case and outer spaces, so `"Service unlocked"` and `"SERVICE UNLOCKED"` both open; `"Service unlocked."` (with a full stop) would not.
7. **Relay failed after "Service unlocked"** (the customer was charged but the door did not open): the Pi sends a `relay_failed` door-event with the same `scan_id`, so the backend can **refund** it.
8. **Report:** every outcome becomes a door-event (section 8), sent from the queue.


---

## 3. Staff entry: Facility app

The manager app is retired. Attendants open doors with the **Facility app**, which calls the Pi directly over the facility WiFi. It works **with no internet**.

```mermaid
sequenceDiagram
    autonumber
    participant A as Attendant (Facility app, facility WiFi)
    participant P as Pi (facility node)
    participant R as KC868 relay
    participant B as Backend
    A->>P: POST http://192.168.10.104:5454/facility/open<br/>{authCode, door:"female", use:"customer", amount:20}
    P->>P: compare authCode with the cached hash (no internet needed)
    alt code matches
        P->>R: hold relay 2 for 5 s
        P-->>A: 200 {ok:true, result:"opened", door:"female"}
        P--)B: door-event attendant (door, use, purpose or payment cash + amount, attendant)  [queued]
    else wrong code
        P-->>A: 403 {result:"denied"}
        P--)B: door-event denied (door, no reason)  [queued]
    end
```

**Request**

```json
POST /facility/open
{"authCode": "<facility auth code>", "door": "BHU-002",
 "use": "personal", "purpose": "cleaning", "attendant": "rahim", "attendant_id": 615}
```

| Field | Meaning |
|---|---|
| `authCode` | The facility's Auth Code (also accepted as header `X-Auth-Code`) |
| `door` | The gate: its device id (`BHU-002`) or `male` / `female` (or header `X-Door`) |
| `use` | `customer`: a customer paid cash at the counter (`amount` optional, logged as a cash sale). No `amount` = the door's price; a different amount still opens but is flagged `price_mismatch: true`. `personal`: the attendant's own use, with `purpose`: any text, e.g. `cleaning` (required, up to 64 characters) |
| `attendant` | Who pressed it (name). **Required** for `personal`; optional for `customer` |
| `attendant_id` | The attendant's backend user id (whole number). **Required** for `personal`; optional for `customer` |

**Replies:** `200 opened` · `403 denied` (wrong code) · `400 unknown_door` / `bad_use` / `bad_purpose` / `bad_amount` / `bad_attendant_id` / `no_attendant` · `429` after 5 wrong codes from one phone in 60 s (blocked 1 min) · `502 relay_error` (relay board down; a cash sale is reported as `relay_failed` so it can be voided).

**The app's door buttons:** `GET /facility/doors` with header `X-Auth-Code` (never in the URL) returns each door's Pi name (`door`), device id (`door_id`), name, price and whether it is open, from the last settings pull, so it works offline:

```json
{"ok": true, "doors": [
  {"door": "male",   "door_id": "BHU-001", "name": "Gents Toilet",  "price": 10, "open": false},
  {"door": "female", "door_id": "BHU-002", "name": "Ladies Toilet", "price": 10, "open": false}]}
```

**Where the Auth Code comes from:** it is the facility's code from the admin panel's Device List, the same code the Pi sends as `Device-Auth-Code` to `/qr/validate`.

- **Now (no settings pull yet):** the Pi checks it against `DEVICE_AUTH_CODE` in its `.env`.
- **Once the backend sends `authCode`** in the settings pull (section 7): that code wins, and `.env` is no longer used. The Pi keeps only its SHA-256 hash, so it keeps working through an outage. Office staff can then change the code in the admin panel without touching the Pi.
- `facility health` → `auth_code_source` shows which one is in use (`backend`, `env`, or `null` = Facility app refused).

---

## 4. Exit button

The exit button opens the door **in hardware** (through a timer module), even with the Pi off. The Pi only records it: the KC868 reports the press, and the Pi queues a door-event `exit` for that door. No API call opens anything.

> On this site the KC868 firmware does not report inputs yet (flash `firmware/kc868/main.py`), so exits are not logged and look like forced openings.

---

## 5. Door watching: forced and propped-open alerts

Each door has a reed switch (needs `REED=1`). The Pi matches every physical opening with what triggered it.

```mermaid
sequenceDiagram
    autonumber
    participant D as Door reed switch
    participant P as Pi
    participant K as Camera for that door
    participant B as Backend
    D->>P: door opened
    alt QR / app / exit on this door in the last 15 s
        P->>P: normal opening (already logged as entry / attendant / exit)
    else no trigger
        P->>K: snapshot
        P--)B: snapshot upload, then alert forced_open {door, snapshot, camera}
    end
    loop still open at 300 s, then every 180 s
        P->>K: snapshot
        P--)B: alert propped_open {door, duration_s, repeat, snapshot, camera}
    end
    D->>P: door closed
    P--)B: door-event door_closed {door, duration_s}
    P--)B: alert propped_resolved (only if it had been propped open)
```

- **Which camera:** the one linked to that door in the admin panel (`cameraDoors`, section 7), else the `.env` link. One camera per door; one camera may cover both doors.
- **Snapshot first:** the image upload is queued before its alert, so the backend has the picture when the alert arrives. The alert's `snapshot` equals the upload's `name`.

---

## 6. Air quality and camera health

Every 60 s the Pi pushes one **sensor** item: air readings from the sensor board plus each camera's health.

```json
POST SENSOR_PATH
{"id": "7f3c…", "facility": "995", "ts": "2026-10-30T10:15:00Z",
 "tvoc": 120, "tvoc_score": 11, "tvoc_level": "very_good", "eco2": 450, "aqi": 2,
 "temperature": 28.4, "humidity": 71.2, "nh3_ppm": null, "h2s_ppm": null,
 "camera":  {"reachable": true, "streaming": true, "frozen": false, "checked_at": "…"},
 "cameras": {"entry": {"reachable": true, "streaming": true, "frozen": false, "checked_at": "…"}},
 "devices": {"relay":          {"ip": "192.168.10.174", "mac": "2c:bc:bb:bf:70:a3", "expected": ["2c:bc:bb:bf:70:a3"], "ok": true, "reachable": true},
             "scanner_male":   {"ip": "192.168.10.101", "mac": "00:88:22:09:87:58", "expected": null, "ok": null, "reachable": true},
             "camera_main":    {"ip": "192.168.10.149", "mac": "90:6a:94:66:7c:9d", "expected": null, "ok": null, "reachable": true},
             "sensor_board":   {"ip": "192.168.10.105", "mac": "64:e8:33:73:de:ac", "expected": null, "ok": null, "reachable": true}}}
```

**Device MACs.** `devices` is every LAN device the Pi talks to, with the MAC it actually sees (from its ARP table). Each can have its MAC set in `.env` (`RELAY_MAC`, `DOOR_<NAME>_SCANNER_MACS`, `CAM_MAC`, `SENSOR_MAC`; fill them on site with `facility macs save`). Then:

| Device | Wrong MAC means |
|---|---|
| Relay board | the Pi sends it nothing (the relay password never leaves the Pi); doors can't be opened from the Pi; exit buttons still work (wired) |
| QR scanner | its scans are refused (`reason: device_mismatch`) |
| Camera | no RTSP login sent, no snapshot; camera reported offline (`mac_ok: false`) |
| Sensor board | its readings are not used (nulls) |

Each case raises an alert `device_mismatch` (`device`, `ip`, `expected`, `seen`), at most every 10 min per device. `ok: null` = no MAC set, reported only. `reachable` is checked just before each push (TCP connect; for a scanner a heartbeat in the last 2 min, else a ping), so the backend can raise device-offline alerts. The Device List can show these next to the Pi's own `mac` from the settings pull. A MAC can be faked, so this complements a strong relay password and a separate customer WiFi; it doesn't replace them.

- `null` = sensor warming up, failed or not fitted.
- **Odour alert:** any metric over its limit for 10 min → alert `odour_high` (repeated every 30 min while high); all back under for 5 min → `odour_resolved`.
- **Camera health:** probed every 30 s (port open, stream decodes, picture not frozen). This feeds the admin panel's Camera Status page (Online/Offline).

---

## 7. Settings from the admin panel (pull every 5 min)

The Pi pulls its settings every 5 minutes (and at start). This is how office staff change things without touching the Pi.

```http
GET TOKENS_PATH?facility=995&mac=2c:cf:67:01:a7:ea
Authorization: Bearer <ADMIN_KEY>
```

```json
{"facilityId": "995",
 "authCode": "<Facility app auth code>",
 "doors": {"BHU-001": {"name": "Gents Toilet",  "price": 10},
           "BHU-002": {"name": "Ladies Toilet", "price": 10}},
 "cameraDoors": {"main": ["BHU-001", "BHU-002"]}}
```

Doors are named by their **device id** everywhere (`doors`, `cameraDoors`, and `door` in every QR check, event and alert). The Pi knows which physical door each id is by `DOOR_<NAME>_ID` in its `.env`:

```
DOOR_MALE_ID=BHU-001
DOOR_FEMALE_ID=BHU-002
```

`mac` is the Pi's MAC address (`DEVICE_MAC` in `.env`, default the eth0 MAC printed by `facility doctor`). The backend can match it with the **MAC** in the admin panel's Device List, to know which registered device is asking (and refuse one that isn't registered for this facility).

| Field | Used for | If missing |
|---|---|---|
| `authCode` | Facility app unlock (section 3); stored as a hash | Previous code kept |
| `doors` | Each door's name and price, by backend door id. The name goes into events and alerts and the Facility app's buttons; the price into paid entries, cash sales and the scanner screen | Last one kept |
| `cameraDoors` | Which camera pictures which door (section 5); key = camera name or IP, doors = backend door ids | `.env` `CAM_<NAME>_DOORS` |
| `tokens` | Not needed for Jabo Khotai entry: leave it out | — |

- All of it is **saved on the Pi**, so a reboot during an outage keeps the last settings.
- A failed pull keeps everything as it was.
- Invalid `cameraDoors` (unknown door or camera) is ignored and logged; the Pi falls back to `.env`.
- A door id the Pi doesn't have is ignored and logged. A name or price never stops a door opening.
- Gate service ids are **not** in this pull: the backend's `/qr/validate` looks them up itself.
- A reply for a different `facilityId` is ignored.

---

## 8. How the Pi delivers to the backend

Every event is **written to the SD card first**, then sent oldest-first by a background job (every 15 s).

```mermaid
flowchart LR
    E[Event: door-event / alert / sensor / snapshot] --> Q[(Queue on SD card)]
    Q -->|every 15 s, oldest first| B{Backend reply}
    B -->|2xx| D[Delivered, removed]
    B -->|network error, 5xx, 401, 403, 408, 429| W[Kept, retried later]
    B -->|other 4xx| X[Retried 5 times, then dropped and logged]
    W --> Q
```

- **Every item has a unique `id`** (UUID), in the body and as the `Idempotency-Key` header. A retry after a lost reply sends the **same id**. The backend must store ids and answer a repeat with 2xx **without saving it twice**.
- `ts` is when it happened (ISO 8601 UTC), not when it was sent: after an outage, events arrive late but with the right time.
- All pushes carry `Authorization: Bearer <ADMIN_KEY>` (the node key; header and scheme configurable). The backend answers `200 {"ok": true}` (`"duplicate": true` for a repeat).

**Door-event types** (`POST DOOR_EVENT_PATH`). Every one has `facility`, `door` (device id), `type`, `ts`; there is no `source`, the `type` says what happened:

```json
{"id": "…", "facility": "995", "door": "BHU-002", "type": "attendant", "ts": "…Z",
 "use": "personal", "purpose": "cleaning", "attendant": "rahim", "attendant_id": 615}
```

| `type` | When | Extra fields |
|---|---|---|
| `entry` | QR opened the gate | `scan_id`, `price` (charged by the backend; none for a staff code) |
| `attendant` | Facility app opened it | `use`; personal: `purpose` + `attendant` + `attendant_id`; customer: `payment: cash`, `amount`, `price`, `price_mismatch`?, `attendant`?, `attendant_id`? |
| `exit` | Exit button pressed | — |
| `denied` | Refused | QR: `reason` (`api_denied`, `offline`, `setup_error`, `unknown`, `empty`), `api_reason` (the backend's `message`), `scan_id`. Facility app: no `reason` |
| `relay_failed` | Paid (online or cash) but the door did not open | `scan_id` or the cash-sale fields: **refund / void** |
| `door_closed` | Door shut again | `duration_s` |

**Alert subtypes** (`POST ALERT_PATH`): `forced_open`, `propped_open` (`repeat` false = new, true = update), `propped_resolved` (all three with `door` = device id), `odour_high`, `odour_resolved` (no `door`), `device_mismatch` (`door` for a scanner).

---

## 9. Endpoint summary and status

### Backend endpoints the Pi calls

Paths are set in the Pi's `.env`; the defaults are the backend's paths (backend branch `feature/facility-node-api`, see `FACILITY_NODE_FEATURE_REPORT.md`).

| Call | Default path (`.env` key) | When | Status |
|---|---|---|---|
| QR check + charge | `POST /qr/validate` (`QR_API_URL`) | each customer scan | Built (backend branch), not yet deployed |
| Settings pull | `GET /gateway/facility-node/config` (`TOKENS_PATH`) | every 5 min | Built, not yet deployed |
| Door-event push | `POST /gateway/facility-node/door-event` (`DOOR_EVENT_PATH`) | each event | Built, not yet deployed |
| Alert push | `POST /gateway/facility-node/alert` (`ALERT_PATH`) | each anomaly | Built, not yet deployed |
| Sensor push | `POST /gateway/facility-node/sensor` (`SENSOR_PATH`) | every 60 s | Built, not yet deployed |
| Snapshot upload | multipart `POST /gateway/facility-node/snapshot` (`SNAPSHOT_UPLOAD_PATH`) | with each door alert | Built, not yet deployed |

Until an endpoint exists, the Pi keeps its items queued (nothing is lost), up to 200 000; the oldest sensor readings are dropped first.

### Pi endpoints (LAN only, port 5454)

| Call | Caller | Purpose |
|---|---|---|
| `POST /qr`, `POST /api/device/code` | QR scanner | Customer scan (section 2) |
| `GET\|POST /qrscanner`, `POST /rakindaqrscanner` | Other scanner models | Same, in their reply format |
| `POST /facility/open` | Facility app | Staff unlock (section 3) |
| `GET /facility/doors` | Facility app | Door buttons: id, name, price, open (section 3) |
| `GET /health` | Ops / `facility health` | Queue, last pull, doors (id, name, price), device MACs, cameras, QR API state |

---

## 10. Open questions for the backend

1. **`/qr/validate` replies:** `200 {"message": "Service unlocked"}` to open, exactly that text; every refusal a 4xx with the reason in `message` (e.g. `402 insufficient_balance`, `403 invalid_token`). (Agreed 2026-10-30.)
2. **Idempotency:** store `Idempotency-Key` (= `scan_id`) and answer a repeat with the first result, no second charge. Refund on `relay_failed`.
3. **Facility app Auth Code vs Device Auth Code:** are they the same code? The Facility app's comes in the pull; the Device Auth Code is set in the Pi's `.env`.
4. **Final paths and auth** for the pull and the four pushes, and the auth header they expect (default `Authorization: Bearer <key>`).
5. **`cameraDoors`** in the settings pull, edited from the admin panel (Camera Status), naming doors by backend door id; each gate's `service_id` stays in the backend for `/qr/validate`.
6. **`doors`** in the settings pull: name and price per backend door id. Give the Pi team each door's id for `DOOR_<NAME>_ID`.
7. **Device List MACs:** show the Pi's `mac` (settings pull) and each device's `devices` entry (sensor push); treat a `device_mismatch` alert as a security alarm.
