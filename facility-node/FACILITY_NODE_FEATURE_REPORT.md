# Facility Node (Raspberry Pi) Backend: Feature Report

Date: 2026-11-05 · Branch: `feature/facility-node-api` · Audience: backend team, QA, frontend (admin panel) and the field team

Source contracts: `API_FLOW 1.md` and `Facility Node Backend API 1.md` (3 November version), in this folder. Plan and decision log: `BACKEND_IMPLEMENTATION_PLAN.md`.

## Contents

1. [What the contract asked for](#1-what-the-contract-asked-for)
2. [What changed in the backend](#2-what-changed-in-the-backend)
3. [Why each change was made](#3-why-each-change-was-made)
4. [Final outcome](#4-final-outcome)
5. [API reference (curl, response, errors)](#5-api-reference)
   - [A. Pi endpoints](#a-pi-endpoints-called-by-the-raspberry-pi)
   - [A7. Facility app opens a door](#a7-facility-app-opens-a-door-no-backend-call)
   - [B. Admin endpoints (new)](#b-admin-endpoints-new-used-by-the-admin-panel)
   - [C. Existing endpoints whose output changed](#c-existing-endpoints-whose-output-changed)
6. [Notifications raised by this feature](#6-notifications-raised-by-this-feature)
7. [Scheduled commands](#7-scheduled-commands)
8. [Rollout checklist and open items](#8-rollout-checklist-and-open-items)

---

## 1. What the contract asked for

Each facility has one Raspberry Pi controlling two doors. The Pi calls the backend; the backend never calls the Pi. The two reference docs require:

| Contract item (from the docs) | Requirement |
|---|---|
| `POST /qr/validate` | Customer QR scan. Authenticated by `Device-Auth-Code`. Must answer exactly `200 {"message":"Service unlocked"}`; every refusal is a 4xx with a reason code. Each `scan_id` is charged once. |
| Settings pull | Returns `authCode`, `doors`, `cameraDoors`. Takes `facility` and `mac`. |
| Door event | Entry, exit, denial, `relay_failed`, `door_closed`, attendant opens (cash, personal use). Idempotent on `id`. |
| Alert | `forced_open`, `propped_open`, odour, `device_mismatch`. Idempotent on `id`. |
| Sensor | Air readings every 60 s, plus camera and LAN-device state. `null` means "no data". |
| Snapshot | Optional JPEG upload tied to a door alert. |
| Auth for pushes | One bearer key per facility, checked against the `facility` in the body. |
| Retries | 2xx delivered; 5xx/408/429/401/403 retried; other 4xx dropped after 5 tries. Never 500 for bad input. A catch-up burst after an outage must not be rate-limited. |
| 3 November changes | Doors named by device id (`BHU-001`), `section` renamed `door`, `source` removed, `doors` added to the pull, prices on entries and cash sales, `attendant` required for personal use, `devices` block and `device_mismatch` alert, camera name `main`. |

## 2. What changed in the backend

### 2.1 New endpoints

| Group | Endpoints |
|---|---|
| Pi, QR | `POST /api/qr/validate` |
| Pi, node key | `GET /api/gateway/facility-node/config`, `POST .../door-event`, `POST .../alert`, `POST .../sensor`, `POST .../snapshot` |
| Admin | `/api/backend/partners/{partner}/facility-node/` `gates`, `nodes`, `cameras`, `alerts`, `events`, `sensor-readings` |

### 2.2 Database

Ten migrations (`2026_11_03_100000` to `100009`), each with a `down()`:

| New table | Purpose |
|---|---|
| `facility_nodes` | One row per Pi: API key hash, camera-to-door map, last pull/seen/sensor times |
| `facility_gates` | Maps a door device (`BHU-001`) to a `facility_services` row. Gender comes from that row's `section` |
| `qr_scans` | Idempotency and refund ledger, one row per `scan_id` |
| `facility_node_events` | Door events |
| `facility_node_alerts` | One lifecycle row per door, odour or device alarm |
| `facility_node_cameras` | Current camera state |
| `facility_node_devices` | Current LAN-device state (relay, scanners, sensor board) |
| `facility_node_sensor_readings` | Air-quality time series |
| `facility_node_snapshots` | Door-alarm images (disk and path) |

Changes to `facility_accesses`: `user_id` is now nullable; new nullable columns `attendant_user_id`, `attendant_name`, `scan_id`, `price_mismatch`, `voided_at`; new access purpose `bath`. A global scope hides voided rows from every model query.

### 2.3 Code

| Area | What was added or changed |
|---|---|
| Services (`app/Services/FacilityNode/`) | `QrValidationService`, `AccessChargeService`, `ScanRefundService`, `GateResolver`, `FacilityNodeIngestService`, `NodeStateService`, `FacilityNodeSettingsService`, `AttendantService`, `FacilityNodeNotifier` |
| Middleware | `facility.node` (`AuthenticateFacilityNode`): checks the bearer key hash and that the body `facility` matches the key |
| Routes | `routes/api/gateway/facility_node.php` (outside `throttle:api`), `routes/api/backend/tenant/facility_nodes.php` |
| Controllers, requests, resources | Gateway controllers and one request class per push; seven admin controllers with requests and resources |
| Notifications | Four new sources (`door_forced_open`, `door_propped_open`, `door_relay_failed`, `device_mismatch`) and events, no new category. `camera-down:scan` and `device-offline:scan` now work. `odour:scan` is unscheduled |
| Existing code touched | `Device::nextDeviceId()` now generates `BHU-` ids; `FacilityAccess` (scope, fillable); `WalletTransaction` (`refund` type); `UserSubscriptionPackageRepository` (row-locked lookup); six revenue and report queries now ignore voided rows; `FacilityAccessResource` (both) and `FacilityAccessController` tolerate a null user, show the attendant, and accept the `internal` payment method |
| Permissions | `facility_node.view`, `facility_node.manage`, `facility_node.*` (PermissionSeeder) |
| Config | `config/facility_node.php`, `config/notifications.php` |
| Tests | 82 tests in `tests/Feature/FacilityNode/`, passing with the 14 existing notification tests |

## 3. Why each change was made

| Change | Reason |
|---|---|
| New charge service instead of reusing `/user/accesses` | The legacy endpoint returns `"Service unlocked!"` (with `!`), uses an exception code as the HTTP status, and returns free-text errors. The Pi needs the exact text and reason codes. The legacy endpoint is unchanged so the mobile app keeps working. |
| `qr_scans` ledger | The Pi retries, and one `scan_id` must be charged once. A repeat returns the first answer. A 5-second repeat check would otherwise refuse a retry. |
| Row locks (`lockForUpdate`) on wallet and package | The legacy flow reads then writes the balance, so two simultaneous scans could both spend it. |
| Charge order: free-access badge, package, wallet | Same rules as the legacy flow; a price-0 service now opens instead of failing validation. |
| Per-facility API key, stored hashed | A stolen Pi exposes one site and its key can be rotated alone. |
| `facility_gates` plus `devices.device_id` | The contract names doors by device id and the backend must know the price and gender per door. Gender is read from `facility_services.section`, so it is never stored twice. |
| Nullable `user_id`, attendant columns | Cash sales and personal use have no app-user customer. The attendant is a backend user, so it needs its own columns. |
| Automatic refund on `relay_failed` | The customer was charged but the door did not open. The wallet credit or package use is restored with a reference (`relay_failed:<scan_id>`), and the Supervisor is told. The customer is not messaged. |
| `voided_at` plus a global scope | Refunded rows disappear from revenue and usage lists without deleting billing history. |
| Newer-`ts`-wins camera and device state | After an outage the Pi sends old readings in a burst. Without this, old data would flip current state back. |
| Notifications reuse existing categories | Avoids a new category in the app. `forced_open` and `propped_open` map to `issue`, `device_mismatch` to `camera_down`. Alerts older than 30 minutes are stored but not pushed, so a replay does not flood supervisors. |
| Pi endpoints outside `throttle:api` | The contract says a catching-up facility must not be rate-limited. |
| Unknown `door` never rejected on pushes | The Pi would drop the item after 5 tries. It is stored and shown in admin instead. |
| `BHU-` prefix | Decision 11. Existing `BHM-` rows keep working because a door id is an opaque string. |

## 4. Final outcome

- A customer scans a QR; the backend authenticates the facility, resolves the door to a gate and price, charges once (package, then wallet), and answers `Service unlocked` or a reason code. Retries are safe.
- Attendant cash sales and personal use are stored in `facility_accesses` (cash feeds System Income; personal use is unpaid and has a purpose).
- A failed relay refunds the customer automatically. A charged scan with no entry is refunded only after 30 minutes and a later sensor reading.
- Admin users can provision a node, rotate its key, map gates, and view cameras, alerts, events and sensor readings, all paginated and permission-gated.
- Door, odour, camera-offline, device-offline and device-mismatch problems reach the right people through the existing notification system.
- Not live-tested against a real Pi. QR, gate, node and settings examples below were captured from a running server; door-event, alert, sensor, snapshot and monitoring examples come from the test suite.

---

## 5. API reference

### Conventions

- Base URL: `https://<host>/api` (examples use `$BASE`). All bodies are JSON unless noted.
- Example ids: partner `485`, facility `995`, door devices `BHU-001` and `BHU-002`.
- `facility` in every Pi call is the facility's own id (`facilities.id`) as a string.
- Pi push endpoints reply `200 {"ok":true}`, or `200 {"ok":true,"duplicate":true}` for a repeated `id`. Both mean delivered.
- Admin endpoints need `Authorization: Bearer <backend token>`. Permissions: `facility_node.view` (read), `facility_node.manage` (write). Lists return `data`, `links`, `meta`.

Errors common to all admin endpoints:

| Status | Body |
|---|---|
| 401 | `{"message":"Unauthenticated.","message_bn":"…"}` |
| 403 | `{"message":"Insufficient permissions.","message_bn":"…"}` |

Errors common to the Pi node-key endpoints (all of section A except `/qr/validate`):

| Status | Body | Pi behaviour |
|---|---|---|
| 401 | `{"message":"Missing API key."}` or `{"message":"Invalid API key."}` | Keeps and retries |
| 403 | `{"message":"This API key does not belong to the given facility."}` | Keeps and retries |
| 422 | `{"message":"…","errors":{"field":["…"]}}` | Drops the item after 5 tries |
| 5xx | any | Retries |

### A. Pi endpoints (called by the Raspberry Pi)

#### A1. `POST /api/qr/validate`: check and charge one QR scan

Auth: header `Device-Auth-Code` (the facility's device code). Not the node key.

```bash
curl -X POST "$BASE/api/qr/validate" \
  -H "Device-Auth-Code: 8f2k-Qx91-zt" -H "Content-Type: application/json" \
  -d '{"token":"BHU-62AB420EA75D10.29957199","facility":"995","door":"BHU-001","scan_id":"6f1c2b7e-5f0b-4d8e-9d39-2c51a0f4e7a2"}'
```

Success (200):

```json
{ "message": "Service unlocked" }
```

The text is exactly `Service unlocked`. A repeat of the same `scan_id` returns the first answer and never charges twice.

| Status | `message` | Meaning |
|---|---|---|
| 200 | `Service unlocked` | Charged (package first, then wallet; free-access badge and free services open without a charge) |
| 402 | `insufficient_balance` | Wallet too low |
| 403 | `invalid_token` | Unknown QR token |
| 403 | `user_blocked` | Customer is blocked or banned |
| 400 | `recent_usage` | Same customer already unlocked the same service in the last 5 seconds (refunded rows don't count) |
| 404 | `unknown_door` | `door` is not a gate of this facility (legacy `male`/`female` also accepted) |
| 401 | `invalid_device_code` | Code missing, wrong, or belongs to another facility |
| 422 | validation errors | e.g. `{"message":"The scan id must be a valid UUID.","errors":{"scan_id":["…"]}}` |
| 503 | `server_error` or `processing` | Unexpected failure (nothing charged, retry is safe), or the same `scan_id` is still in flight |

#### A2. `GET /api/gateway/facility-node/config`: settings pull

Auth: `Authorization: Bearer <node key>`. The `facility` must be the key's facility.

```bash
curl "$BASE/api/gateway/facility-node/config?facility=995&mac=2c:cf:67:01:a7:ea" \
  -H "Authorization: Bearer $NODE_KEY"
```

```json
{
  "facilityId": "995",
  "authCode": "8f2k-Qx91-zt",
  "doors": {
    "BHU-001": { "name": "Gents Toilet", "price": 10 },
    "BHU-002": { "name": "Ladies Toilet", "price": 10 }
  },
  "cameraDoors": { "main": ["BHU-001", "BHU-002"] }
}
```

`authCode` is omitted when none is set (the Pi keeps its old one). `doors` and `cameraDoors` are `{}` when empty. A MAC that differs from the registered device is recorded and shown in the Device List, but the pull is still served.

Errors: 401, 403 (common table), and `422` when `facility` is missing.

#### A3. `POST /api/gateway/facility-node/door-event`

```bash
curl -X POST "$BASE/api/gateway/facility-node/door-event" \
  -H "Authorization: Bearer $NODE_KEY" -H "Content-Type: application/json" \
  -d '{"id":"b21e6c0e-5f0b-4d8e-9d39-2c51a0f4e7a2","facility":"995","door":"BHU-002","type":"attendant","use":"personal","purpose":"cleaning","attendant":"rahim","attendant_id":615,"ts":"2026-11-03T10:15:03Z"}'
```

Response: `200 {"ok":true}`.

Required: `id`, `facility`, `type`, `ts`. `type` is one of `entry`, `attendant`, `exit`, `denied`, `relay_failed`, `door_closed`. Optional: `door`, `scan_id`, `reason`, `api_reason`, `use` (`customer` or `personal`), `purpose`, `payment`, `amount`, `price`, `price_mismatch`, `attendant`, `attendant_id`, `duration_s`.

Effects (first insert only):

| Event | Effect |
|---|---|
| `entry` with `scan_id` | Marks the charged scan as entered |
| `attendant` + `use: customer` | Cash `facility_accesses` row. Amount defaults to the door price; `price_mismatch` is kept |
| `attendant` + `use: personal` | Unpaid `internal` row with the purpose. Attendant stored by `attendant_id`, else a unique name match, else name only |
| `relay_failed` with `scan_id` | Automatic refund (wallet credit or package use restored), `reference = relay_failed:<scan_id>`, row voided, Supervisor notified |
| `relay_failed` with `payment: cash` | The matching cash sale is voided |

Errors: `422` (for example `type` not in the list), 401/403. An unknown `door` is stored, not rejected.

#### A4. `POST /api/gateway/facility-node/alert`

```bash
curl -X POST "$BASE/api/gateway/facility-node/alert" \
  -H "Authorization: Bearer $NODE_KEY" -H "Content-Type: application/json" \
  -d '{"id":"c9a04d51-2a7e-4f43-8c3b-61d1b9f0aa10","facility":"995","door":"BHU-002","type":"anomaly","subtype":"propped_open","duration_s":300,"repeat":false,"snapshot":"20261103T101500Z-main.jpg","camera":"main","ts":"2026-11-03T10:15:00Z"}'
```

Response: `200 {"ok":true}`.

`subtype`: `forced_open`, `propped_open`, `propped_resolved`, `odour_high`, `odour_resolved`, `device_mismatch` (needs `device`, `ip`, `expected`, `seen`). A new alarm opens one lifecycle row; repeats and all-clears update it. A notification goes out once per new alarm, and only if the alert is under 30 minutes old.

Errors: `422` for an unknown subtype or a missing field; 401/403.

#### A5. `POST /api/gateway/facility-node/sensor`

```bash
curl -X POST "$BASE/api/gateway/facility-node/sensor" \
  -H "Authorization: Bearer $NODE_KEY" -H "Content-Type: application/json" \
  -d '{"id":"7f3c2a9e-0d4b-4c1e-b0a8-5e9f71c3d2b4","facility":"995","ts":"2026-11-03T10:15:00Z","tvoc":412,"eco2":780,"aqi":3,"temperature":29.6,"humidity":78.4,"nh3_ppm":null,"h2s_ppm":null,
       "cameras":{"main":{"reachable":true,"streaming":true,"frozen":false}},
       "devices":{"relay":{"ip":"192.168.10.174","mac":"2c:bc:bb:bf:70:a3","expected":["2c:bc:bb:bf:70:a3"],"ok":true,"reachable":true}}}'
```

Response: `200 {"ok":true}`. `null` readings are stored as no data, never 0. Camera and device state change only when `ts` is newer than the stored state. A camera is offline when it is unreachable, not streaming, or frozen.

Errors: `422`; 401/403.

#### A6. `POST /api/gateway/facility-node/snapshot` (multipart)

```bash
curl -X POST "$BASE/api/gateway/facility-node/snapshot" \
  -H "Authorization: Bearer $NODE_KEY" \
  -F "file=@20261103T101500Z-main.jpg;type=image/jpeg" -F "id=4be1c0de-0000-4000-8000-000000000001" \
  -F "facility=995" -F "name=20261103T101500Z-main.jpg" -F "ts=2026-11-03T10:15:01Z" -F "camera=main"
```

Response: `200 {"ok":true}`. Errors: `422` if the file is not a JPEG or is over 4 MB, or `name` is not `[A-Za-z0-9._-]+`; 401/403.

#### A7. Facility app opens a door (no backend call)

The Facility app never calls the backend to open a door. It calls the Pi over the facility WiFi, so it works with no internet. The backend only sees the result later, as the door event in A3. The two Pi endpoints below are defined by the contract; the backend does not build or host them.

How the unlock happens:

1. The attendant's phone joins the facility WiFi. The Pi is on the same LAN at a fixed address (the contract example is `192.168.10.104`, port `5454`).
2. The app calls `POST http://<pi-ip>:5454/facility/open`.
3. The Pi checks the `authCode` against the SHA-256 hash it cached from the last settings pull (A2). This works offline.
4. If it matches, the Pi holds the door's relay on the KC868 relay board (`192.168.10.174`) for 5 seconds: relay 1 is the male door, relay 2 the female door. The relay powers the lock.
5. The Pi replies to the app, then queues a door event and pushes it to the backend (A3).

Request (Pi, LAN only):

```bash
curl -X POST "http://192.168.10.104:5454/facility/open" -H "Content-Type: application/json" \
  -d '{"authCode":"8f2k-Qx91-zt","door":"BHU-002","use":"personal","purpose":"cleaning","attendant":"rahim","attendant_id":615}'
```

| Field | Notes |
|---|---|
| `authCode` | The facility's Auth Code (also accepted as header `X-Auth-Code`). Same code the Pi sends as `Device-Auth-Code` to `/qr/validate` |
| `door` | Door device id (`BHU-002`), or `male` / `female` |
| `use` | `customer` (cash paid at the counter; `amount` optional, no amount means the door price, a different amount is flagged `price_mismatch`) or `personal` (the attendant's own use) |
| `purpose` | Required for `personal`: `cleaning`, `bath`, `toilet`, `maintenance` |
| `attendant` | Attendant name. Required for `personal`, optional for `customer` |
| `attendant_id` | Backend user id of the attendant. Not in the original contract; the field team must add it (section 8) |

Replies from the Pi:

| Status | Body or meaning |
|---|---|
| 200 | `{"ok":true,"result":"opened","door":"female"}` |
| 403 | `{"result":"denied"}` (wrong code) |
| 400 | `unknown_door`, `bad_use`, `bad_purpose`, `bad_amount`, `no_attendant` |
| 429 | 5 wrong codes from one phone in 60 s (blocked for 1 minute) |
| 502 | `relay_error` (relay board down; a cash sale is reported as `relay_failed` so it is voided) |

Door buttons: `GET http://<pi-ip>:5454/facility/doors` with header `X-Auth-Code` returns each door's `door`, `door_id`, `name`, `price` and `open` from the last settings pull, so it works offline.

What the backend stores from the resulting door event (A3):

| App action | Event | Backend result |
|---|---|---|
| Personal use | `attendant`, `use: personal` | Unpaid `facility_accesses` row, `payment_method='internal'`, price 0, the purpose, attendant id and name |
| Cash from a customer | `attendant`, `use: customer`, `payment: cash` | Cash row; amount defaults to the door price; a different amount sets `price_mismatch` |
| Wrong code | `denied` | Logged only |
| Relay failed on a cash sale | `relay_failed`, `payment: cash` | The cash sale is voided |

The settings pull no longer carries an attendant list. The app supplies the attendant name and id itself on every open.

How the app finds the Pi: the contract does not say, so the backend stores it. Office staff set `lan_ip` on the node (B2), and the app reads it from `GET /nodes`. The Pi's settings pull does not carry it. The Pi needs a DHCP reservation so the address stays fixed.

### B. Admin endpoints (new, used by the admin panel)

Base: `/api/backend/partners/{partner}/facility-node`. Lists are paginated with `per_page` (default 20, max 100; sensor readings default 60, max 500).

#### B1. Gates: map a door device to a service

| Method | Path | Permission |
|---|---|---|
| GET | `/gates?facility_id=` | view |
| POST | `/gates` | manage |
| PATCH | `/gates/{id}` (body: `facility_service_id`) | manage |
| DELETE | `/gates/{id}` (204) | manage |

```bash
curl -X POST "$BASE/api/backend/partners/485/facility-node/gates" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":1313,"facility_service_id":1206}'
```

```json
{ "id": 818, "door": "BHU-001", "name": "Gents Toilet", "gender": "male", "facility_id": 995,
  "facility": { "id": 995, "name": "Muktangon Toilet" },
  "device": { "id": 1313, "device_id": "BHU-001", "title": "Gents Toilet" },
  "service": { "facility_service_id": 1206, "title_en": "Toilet", "title_bn": "টয়লেট", "section": "male", "price": 10 },
  "created_at": "2026-11-03 12:45:11", "updated_at": "2026-11-03 12:45:11" }
```

Errors:

| Status | Body |
|---|---|
| 422 | `{"message":"This device is already a gate.","errors":{"device_id":["…"]}}` |
| 422 | Device not found or belongs to another partner; device has no `device_id`; service belongs to another facility; service `section` is not male or female |
| 404 | `{"message":"Gate not found.","message_bn":"…"}` |

#### B2. Nodes: provision and manage the Pi

| Method | Path | Permission |
|---|---|---|
| GET | `/nodes` | view |
| GET | `/nodes/{id}` | view (adds `cameras`, `lan_devices`, `open_alerts`) |
| POST | `/nodes` | manage |
| PATCH | `/nodes/{id}` (`device_id`, `camera_doors` and/or `lan_ip`) | manage |
| POST | `/nodes/{id}/rotate-key` | manage (the old key stops working at once) |

```bash
curl -X POST "$BASE/api/backend/partners/485/facility-node/nodes" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"device_id":1312,"camera_doors":{"main":["BHU-001","BHU-002"]},"lan_ip":"192.168.10.104"}'
```

```json
{ "id": 421, "facility_id": 995, "facility": { "id": 995, "name": "Muktangon Toilet" },
  "device": { "id": 1312, "device_id": "BHU-010", "title": "Facility Node (Raspberry Pi)", "mac": "2c:cf:67:01:a7:ea" },
  "camera_doors": { "main": ["BHU-001", "BHU-002"] }, "lan_ip": "192.168.10.104",
  "mac_matches": null, "last_pull_mac": null, "last_pull_at": null, "last_seen_at": null, "last_sensor_at": null, "online": false,
  "key_rotated_at": "2026-11-03T12:45:14+00:00", "created_at": "2026-11-03 12:45:14",
  "cameras": [], "lan_devices": [], "open_alerts": [],
  "api_key": "fnk_…", "api_key_notice": "Copy this key now. It is shown only once and cannot be recovered; rotate the key to get a new one." }
```

The plain `api_key` is returned once (on create and on rotate); only its hash is stored. Other responses omit it.

`lan_ip` (optional, IPv4, `null` clears it, leaving it out on PATCH keeps it) is the Pi's address on the facility LAN. The Facility app reads it from `GET /nodes` or `GET /nodes/{id}` (permission `facility_node.view`) and calls `POST http://<lan_ip>:5454/facility/open`. The Pi does not report it; office staff set it, and the Pi needs a DHCP reservation so it does not change.

Errors:

| Status | Body |
|---|---|
| 422 | `{"message":"Camera \"main\" is linked to \"BHU-999\", which is not a gate of this facility.","errors":{"camera_doors":["…"]}}` |
| 422 | "This facility already has a facility node." |
| 422 | `{"message":"The lan ip must be a valid IPv4 address.","errors":{"lan_ip":["…"]}}` |
| 404 | `{"message":"Facility node not found.","message_bn":"…"}` (also for another partner's node) |

#### B3. Read-only monitoring (permission: view)

| Endpoint | Filters | Notes |
|---|---|---|
| `GET /cameras` | `facility_id`, `status=online\|offline` | `status`, `reachable`, `streaming`, `frozen`, `offline_since`, `last_online_at`, `last_checked_at` |
| `GET /alerts` | `facility_id`, `subtype`, `status=open\|resolved`, `from`, `to` | One row per alarm with `repeat_count` and `snapshot_url` (short-lived signed URL, or null) |
| `GET /alerts/{id}` | none | Adds `history` (repeats and all-clear). 404 `{"message":"Alert not found.","message_bn":"…"}` |
| `GET /events` | `facility_id`, `door`, `type`, `unresolved_door=1`, `from`, `to` | `door_resolved: false` marks a door the backend does not know |
| `GET /sensor-readings` | `facility_id` (required), `from`, `to` | 422 without `facility_id`; nulls stay null |

```bash
curl "$BASE/api/backend/partners/485/facility-node/cameras?facility_id=995&status=offline&per_page=20" \
  -H "Authorization: Bearer $TOKEN"
```

### C. Existing endpoints whose output changed

These endpoints keep their paths and request shapes. Frontend must handle the changes below.

#### C1. Uses History: `GET /api/backend/facility-accesses` and `GET /api/backend/partners/{partner}/facility-accesses`

```bash
curl "$BASE/api/backend/partners/485/facility-accesses?facility_id=995&payment_method=internal&per_page=20" \
  -H "Authorization: Bearer $TOKEN"
```

What is new in each row:

| Field | Change |
|---|---|
| `user` | Can be `null` for cash and personal-use rows (no app-user customer). The UI must not assume a name and phone number. |
| `attendant` | New object `{ "user_id": 615, "name": "Rahim Uddin" }`. Present only when the row has an attendant. |
| `payment_method` | New filter value and row value `internal` (personal use, price 0). Existing values `cash`, `wallet_balance`, `package_use` are unchanged. |
| Voided rows | Refunded rows no longer appear in the list or in its summary totals. |

Example row (personal use):

```json
{ "id": 90211, "facility_id": 995, "facility": { "id": 995, "name": "Muktangon Toilet" },
  "user": null,
  "attendant": { "user_id": 615, "name": "Rahim Uddin" },
  "service": { "id": 3, "title_en": "Toilet", "title_bn": "টয়লেট" },
  "gender": "female", "user_type": "manager", "unlocked_via": "facility_app",
  "payment_method": "internal", "is_subscribed": false, "price": 0,
  "created_at": "2026-11-03 10:15:03" }
```

Errors (unchanged): `422` for an invalid filter (for example `payment_method` not in `cash`, `internal`, `wallet_balance`, `package_use`), `403` for a facility outside the caller's scope.

#### C2. Admin facility usages and revenue (`/api/admin/facilities/usages` and the same resource under admin)

The admin `FacilityAccessResource` also tolerates a null user and shows the attendant, with the same fields as above.

#### C3. Revenue, System Income, dashboard and gender-collection figures

No request or response shape changed. Totals now exclude voided (refunded) accesses, so the numbers drop by the refunded amount. Affected readers: facility revenue, System Income, dashboard metrics, gender collection and facility details.

#### C4. Device List

`Device::nextDeviceId()` now generates `BHU-###` ids for new devices (numbering continues from the highest existing number; old `BHM-` ids remain valid). The Device List shows the Pi's `mac` next to each LAN device reported by the node.

#### C5. Notifications (`/api/notifications` family)

No shape changes. New notification `source` values may appear in lists: `door_forced_open`, `door_propped_open`, `door_relay_failed`, `device_mismatch`. They arrive under the existing categories `issue` and `camera_down`. See section 6.

---

## 6. Notifications raised by this feature

| Trigger | Source | Category | Sent to |
|---|---|---|---|
| Alert `forced_open` | `door_forced_open` | `issue` | Facility staff per the notification rules |
| Alert `propped_open` (first) | `door_propped_open` | `issue` | same |
| Door event `relay_failed` (refund done) | `door_relay_failed` | `issue` | Supervisor. The customer is not messaged |
| Alert `device_mismatch` | `device_mismatch` | `camera_down` | same |
| Alert `odour_high` (first) | `odour_breach` | `odour_breach` | same |
| Camera offline 15 min / 60 min | `camera_down` / `camera_down_escalation` | `camera_down` | same |
| No sensor push for 30 min | `device_offline` | `device_offline` | same |

Rules: alerts older than 30 minutes are stored but not pushed; repeats never re-notify; a notification failure never fails a Pi push. At 5 minutes without data the node opens a `facility_offline` dashboard alert (no push).

## 7. Scheduled commands

| Command | Runs | Does |
|---|---|---|
| `camera-down:scan` | every 5 min | Camera offline 15 min: `camera_down`; 60 min: `camera_down_escalation`. One notification per outage. Skips a node silent for 10 min |
| `device-offline:scan` | every minute | 5 min silent: `facility_offline` dashboard alert; 30 min: `device_offline`; LAN devices once `reachable` is sent |
| `facility-node:review-unmatched-scans` | every 10 min | Refunds a charged scan with no `entry` event, only after 30 min and a later sensor reading |
| `facility-node:prune` | daily | Deletes sensor readings (90 days) and snapshots (30 days) |

## 8. Rollout checklist and open items

1. `php artisan migrate`, then `php artisan db:seed --class=PermissionSeeder`. (Both were already run on the dev database.)
2. Give the roles that manage Pis `facility_node.view` and `facility_node.manage`.
3. Per facility: register the Pi and each door as devices (`BHU-...`), `POST .../gates` for each door, `POST .../nodes` for the Pi, and give the one-time `api_key` to the field team.
4. Production needs a real queue driver and a worker for the `notifications` queue (`QUEUE_CONNECTION=sync` runs delivery inside the Pi's request) and `FACILITY_NODE_SNAPSHOT_DISK=s3`.
5. Field team: set the Pi's `FACILITY_ID` to the facility id, send `attendant_id` on Facility-app opens, add `reachable` per LAN device in the sensor push, use the final paths above, and give the Facility app the node's `lan_ip` (A7, B2). Until `reachable` is sent, LAN-device offline alerts stay off; until `attendant_id` is sent, attendants fall back to name matching.
6. Known gaps: the charge logic is a copy (`AccessChargeService`) and the legacy `/user/accesses` is unchanged, so it still has no row lock and concurrent requests can race there. The 5-second repeat check runs before the wallet lock, which leaves a small concurrent-scan gap. A future raw query on `facility_accesses` must add `whereNull('voided_at')`. `platform_critical` is out of scope. 12 `Unit\Middleware` tests fail on a clean checkout (Mockery setup), unrelated to this work.
