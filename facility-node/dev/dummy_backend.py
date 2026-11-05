#!/usr/bin/env python3
"""Dummy admin backend: all the APIs the facility node calls, for end-to-end
testing before the real backend exists.

    python3 dev/dummy_backend.py                    # http://127.0.0.1:8091
    python3 dev/dummy_backend.py --host 0.0.0.0     # reachable from the LAN
    python3 dev/dummy_backend.py --outage           # start in "backend down" mode (503)

Point the node at it (.env), then restart:

    ADMIN_URL=http://127.0.0.1:8091/api
    ADMIN_KEY=dummy-key
    QR_API_URL=/qr/validate        # optional: online QR validation too

Or like the Jabo Khotai backend (start with --jabo; the .env.example QR
settings, with):

    DEVICE_AUTH_CODE=dummy-device-code

Open http://127.0.0.1:8091/ for a live dashboard of what the Pi sent.

Implements the contract in README.md section 8, with the default paths:

    GET  /api/gateway/facility-node/config?facility=<id>&mac=<mac>
                                   settings pull: authCode, doors (--doors), cameraDoors
                                   (--camera-doors); tokens too, except with --jabo
    POST /api/gateway/facility-node/sensor        sensor readings
    POST /api/gateway/facility-node/door-event    access log
    POST /api/gateway/facility-node/alert         anomalies
    POST /api/gateway/facility-node/snapshot      multipart JPEG upload
    POST /api/qr/validate          online QR check + charge (paid entry); --jabo:
                                   Device-Auth-Code header, 200 {"message":
                                   "Service unlocked"} or 4xx {"message": reason}

Behaves like the real backend: checks the node key, refuses a body whose
facility isn't the key's (403, with --facility), rejects payloads missing
required fields (422), ignores repeated event ids (200 {"ok": true,
"duplicate": true}), and can simulate an outage (503) to test the Pi's queue.

Paid entry, like the real backend must do it: each code has a balance
(--balance, default 3 entries). /api/qr/validate charges one entry per
scan_id: asking again with the same scan_id gives the same answer and no
second charge; an empty balance gets 402 {"allowed": false,
"reason": "insufficient_balance"}. A "relay_failed" door-event refunds the
charge of its scan_id.

Outage:

    curl -XPOST http://127.0.0.1:8091/admin/outage -d on=1    # down
    curl -XPOST http://127.0.0.1:8091/admin/outage -d on=0    # back up

TEST ONLY. It hands out well-known unlock codes; never point a live site at it.
"""

import argparse
import json
import os
import threading
import time
from collections import deque

from flask import Flask, abort, jsonify, request

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_TOKENS = ["DUMMY-ALLOW-0001", "DUMMY-ALLOW-0002", "DUMMY-ALLOW-0003"]

REQUIRED = {
    "sensor": {"id", "facility", "ts"},
    "door_event": {"id", "facility", "door", "type", "ts"},
    "alert": {"id", "facility", "type", "subtype", "ts"},
}
DOOR_TYPES = {"entry", "attendant", "exit", "denied", "door_closed", "relay_failed"}
ALERT_SUBTYPES = {"forced_open", "propped_open", "propped_resolved",
                  "odour_high", "odour_resolved", "device_mismatch"}
NODE = "/api/gateway/facility-node"


class State:
    def __init__(self, tokens, auth_code, data_dir, balance=3):
        self.lock = threading.Lock()
        self.tokens = list(tokens)
        self.balance = {t: balance for t in tokens}   # paid entries left per code
        self.charges = {}                              # scan_id -> (token, allowed, reason)
        self.auth_code = auth_code
        self.data_dir = data_dir
        self.seen_ids = set()
        self.recent = {k: deque(maxlen=50) for k in ("door_event", "alert", "sensor", "snapshot")}
        self.counts = {k: 0 for k in self.recent}
        self.duplicates = 0
        self.outage = False
        os.makedirs(os.path.join(data_dir, "snapshots"), exist_ok=True)
        self.log_path = os.path.join(data_dir, "events.jsonl")

    def record(self, kind, payload):
        """-> True if new, False if this id was already stored."""
        with self.lock:
            if payload["id"] in self.seen_ids:
                self.duplicates += 1
                return False
            self.seen_ids.add(payload["id"])
            self.counts[kind] += 1
            self.recent[kind].appendleft({"received": time.strftime("%H:%M:%S"), **payload})
            with open(self.log_path, "a") as f:
                f.write(json.dumps({"kind": kind, **payload}) + "\n")
            return True


def create_app(key="dummy-key", tokens=DEFAULT_TOKENS, auth_code="dummy-auth-code",
               data_dir=os.path.join(HERE, "backend-data"), outage=False, quiet=False,
               balance=3, camera_doors=None, jabo=False,
               device_auth_code="dummy-device-code", doors=None, facility=None):
    app = Flask("dummy_backend")
    st = State(tokens, auth_code, data_dir, balance)
    st.outage = outage
    app.config["state"] = st

    def say(msg):
        if not quiet:
            print(msg, flush=True)

    @app.before_request
    def gate():
        if not request.path.startswith("/api/"):
            return None
        if st.outage:
            return jsonify(error="simulated outage"), 503
        if jabo and request.path == "/api/qr/validate":
            if request.headers.get("Device-Auth-Code") != device_auth_code:
                say("401 /api/qr/validate: bad or missing Device-Auth-Code header")
                return jsonify(message="Unauthenticated."), 401
            return None
        if key and request.headers.get("Authorization") != f"Bearer {key}":
            say(f"401 {request.method} {request.path}: bad or missing Authorization header")
            return jsonify(message="Invalid API key."), 401
        sent = (request.args.get("facility") or request.form.get("facility")
                or (request.get_json(silent=True) or {}).get("facility"))
        if facility and request.path.startswith(NODE) and sent not in (None, facility):
            say(f"403 {request.path}: facility {sent!r} is not the key's ({facility!r})")
            return jsonify(message="This API key does not belong to the given facility."), 403
        return None

    def push(kind):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="JSON object expected"), 400
        missing = REQUIRED[kind] - payload.keys()
        if missing:
            say(f"422 {kind}: missing {sorted(missing)}")
            return jsonify(message=f"missing fields: {sorted(missing)}",
                           errors={f: ["required"] for f in sorted(missing)}), 422
        if kind == "door_event" and payload["type"] not in DOOR_TYPES:
            return jsonify(message=f"unknown door-event type {payload['type']!r}"), 422
        if kind == "alert" and payload["subtype"] not in ALERT_SUBTYPES:
            return jsonify(message=f"unknown alert subtype {payload['subtype']!r}"), 422
        new = st.record(kind, payload)
        if new and kind == "door_event" and payload["type"] == "relay_failed":
            with st.lock:   # charged, but nobody got in: refund
                token, allowed, _ = st.charges.get(payload.get("scan_id"), (None, False, None))
                if allowed:
                    st.balance[token] += 1
                    st.charges[payload["scan_id"]] = (token, False, "refunded")
                    say(f"REFUND scan {payload['scan_id']}: {token} back to {st.balance[token]}")
        brief = {k: v for k, v in payload.items() if k not in ("id", "facility", "camera", "readings")}
        say(f"{'NEW' if new else 'DUP'} {kind:<10} {json.dumps(brief)[:140]}")
        return delivered(new)

    def delivered(new):
        return (jsonify(ok=True) if new else jsonify(ok=True, duplicate=True)), 200

    @app.post(NODE + "/sensor")
    def sensor():
        return push("sensor")

    @app.post(NODE + "/door-event")
    def door_event():
        return push("door_event")

    @app.post(NODE + "/alert")
    def alert():
        return push("alert")

    @app.post(NODE + "/snapshot")
    def snapshot():
        f = request.files.get("file")
        meta = request.form.to_dict()
        if f is None or not {"id", "facility", "name", "ts"} <= meta.keys():
            return jsonify(message="need file + id, facility, name, ts"), 422
        name = os.path.basename(meta["name"])
        if not st.record("snapshot", {**meta, "name": name}):
            return delivered(False)
        f.save(os.path.join(st.data_dir, "snapshots", name))
        say(f"NEW snapshot   {name}" + (f"  camera {meta['camera']}" if meta.get("camera") else ""))
        return delivered(True)

    @app.get(NODE + "/config")
    def settings_pull():
        wanted = request.args.get("facility")
        if not wanted:
            return jsonify(message="The facility field is required.",
                           errors={"facility": ["required"]}), 422
        mac = request.args.get("mac")
        say(f"settings pull for {wanted}" + (f" (MAC {mac})" if mac else " (no MAC)"))
        body = dict(facilityId=wanted, authCode=st.auth_code,
                    doors=doors or {}, cameraDoors=camera_doors or {})
        if not jabo:       # older backends: tokens for the Pi's local cache
            body["tokens"] = [{"token": t, "expiresAt": None} for t in st.tokens]
        return jsonify(body)

    def charge(token, scan_id):
        """One charge per scan_id -> (ok, reason, note)."""
        with st.lock:
            if scan_id and scan_id in st.charges:      # same scan asked again
                _, ok, reason = st.charges[scan_id]
                note = "repeat, no new charge"
            elif token not in st.balance:
                ok, reason, note = False, "unknown_code", ""
            elif st.balance[token] <= 0:
                ok, reason, note = False, "insufficient_balance", ""
            else:
                st.balance[token] -= 1
                ok, reason, note = True, None, f"charged, {st.balance[token]} left"
            if scan_id and scan_id not in st.charges:
                st.charges[scan_id] = (token, ok, reason)
        return ok, reason, note

    @app.route("/api/qr/validate", methods=["GET", "POST"])
    def qr_validate():
        data = {**request.args.to_dict(), **(request.get_json(silent=True) or {})}
        token = data.get("token")
        scan_id = data.get("scan_id") or request.headers.get("Idempotency-Key")
        if jabo and doors and data.get("door") not in doors:   # not a gate: nothing charged
            say(f"QR validate door={data.get('door')!r}: not one of --doors -> 404 unknown_door")
            return jsonify(message="unknown_door"), 404
        ok, reason, note = charge(token, scan_id)
        say(f"QR validate {token!r} door={data.get('door')} "
            f"scan={scan_id} -> "
            f"{'ALLOW' if ok else 'DENY ' + str(reason)} {note}")
        if jabo:   # the backend ran /user/accesses for this door's service
            if ok:
                return jsonify(message="Service unlocked"), 200
            if reason == "insufficient_balance":
                return jsonify(message=reason), 402
            return jsonify(message="invalid_token" if reason == "unknown_code" else reason), 403
        if reason == "insufficient_balance":
            return jsonify(allowed=False, reason=reason), 402
        return jsonify(allowed=ok, reason=reason)

    @app.post("/admin/outage")
    def set_outage():
        on = (request.form.get("on") or request.args.get("on") or "1") in ("1", "true", "on")
        st.outage = on
        say(f"*** outage {'ON: all APIs return 503' if on else 'OFF: APIs back'}")
        return jsonify(outage=on)

    @app.get("/admin/state")
    def state():
        with st.lock:
            return jsonify(counts=st.counts, duplicates=st.duplicates, outage=st.outage,
                           balance=st.balance,
                           recent={k: list(v) for k, v in st.recent.items()})

    @app.get("/admin/snapshots/<path:name>")
    def get_snapshot(name):
        from flask import send_from_directory
        return send_from_directory(os.path.join(st.data_dir, "snapshots"), name)

    @app.get("/")
    def dashboard():
        return DASHBOARD

    return app


DASHBOARD = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dummy Backend</title><style>
:root{--bg:#fff;--fg:#1a1a1a;--mute:#666;--line:#e5e5e5;--bad:#c62828;--ok:#2e7d32;--warn:#ef6c00}
@media (prefers-color-scheme:dark){:root{--bg:#121212;--fg:#eee;--mute:#999;--line:#333}}
body{font:14px system-ui,sans-serif;margin:0 16px 32px;background:var(--bg);color:var(--fg)}
h1{font-size:18px;margin:16px 0 4px}h2{font-size:15px;margin:20px 0 6px}
.mute{color:var(--mute)}.bad{color:var(--bad)}.ok{color:var(--ok)}.warn{color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{text-align:left;padding:4px 8px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mute);font-weight:500}
.wrap{overflow-x:auto}code{font-size:12px}button{font:inherit;padding:4px 10px;margin-left:8px}
</style></head><body>
<h1>Dummy admin backend <span class="mute">· test only</span></h1>
<div id="top" class="mute">loading…</div>
<h2>Door events</h2><div class="wrap"><table id="door_event"></table></div>
<h2>Alerts</h2><div class="wrap"><table id="alert"></table></div>
<h2>Sensor (latest first)</h2><div class="wrap"><table id="sensor"></table></div>
<h2>Snapshots</h2><div class="wrap"><table id="snapshot"></table></div>
<script>
const cols={door_event:["received","ts","door","type","reason","purpose","attendant","duration_s"],
 alert:["received","ts","door","subtype","repeat","duration_s","metrics","snapshot"],
 sensor:["received","ts","tvoc","tvoc_score","tvoc_level","eco2","aqi","temperature","humidity","nh3_ppm","h2s_ppm","camera"],
 snapshot:["received","ts","name"]};
const esc=v=>v==null?"":String(typeof v==="object"?JSON.stringify(v):v)
 .replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function tick(){try{
 const s=await (await fetch("/admin/state")).json();
 document.getElementById("top").innerHTML=
  `received: door ${s.counts.door_event} · alerts ${s.counts.alert} · sensor ${s.counts.sensor} · `+
  `snapshots ${s.counts.snapshot} · duplicates ignored ${s.duplicates} · `+
  (s.outage?'<b class="bad">OUTAGE (503)</b>':'<b class="ok">up</b>')+
  `<button onclick="out(${!s.outage})">${s.outage?"end outage":"simulate outage"}</button>`;
 for(const k in cols){const rows=s.recent[k]||[];
  document.getElementById(k).innerHTML="<tr>"+cols[k].map(c=>`<th>${c}</th>`).join("")+"</tr>"+
  (rows.length?rows.map(r=>"<tr>"+cols[k].map(c=>{let v=esc(r[c]);
   if(k==="snapshot"&&c==="name")v=`<a href="/admin/snapshots/${encodeURIComponent(r.name)}">${v}</a>`;
   if(c==="type"&&r.type==="denied"||c==="subtype"&&!/resolved/.test(r.subtype||""))v=`<span class="warn">${v}</span>`;
   return `<td>${v}</td>`}).join("")+"</tr>").join(""):
   `<tr><td class="mute" colspan="${cols[k].length}">nothing yet</td></tr>`)}
}catch(e){document.getElementById("top").textContent="dashboard error: "+e}}
async function out(on){await fetch("/admin/outage?on="+(on?1:0),{method:"POST"});tick()}
tick();setInterval(tick,3000);
</script></body></html>"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8091)
    p.add_argument("--key", default="dummy-key", help="expected ADMIN_KEY (Bearer); '' = no auth")
    p.add_argument("--token", action="append", help="unlock code to hand out (repeatable)")
    p.add_argument("--auth-code", default="dummy-auth-code", help="Facility-app Auth Code")
    p.add_argument("--data", default=os.path.join(HERE, "backend-data"),
                   help="where events.jsonl and snapshots are written")
    p.add_argument("--outage", action="store_true", help="start returning 503")
    p.add_argument("--balance", type=int, default=3, help="paid entries per code")
    p.add_argument("--camera-doors", type=json.loads, default=None,
                   help='admin panel camera links, e.g. \'{"entry": ["male", "female"]}\'')
    p.add_argument("--doors", type=json.loads, default=None,
                   help='door names and prices by backend door id, e.g. '
                        '\'{"BHU-001": {"name": "Gents Toilet", "price": 10}}\'')
    p.add_argument("--jabo", action="store_true",
                   help="/api/qr/validate answers like the Jabo Khotai backend")
    p.add_argument("--device-auth-code", default="dummy-device-code",
                   help="expected Device-Auth-Code on /api/qr/validate with --jabo")
    p.add_argument("--facility", default=None,
                   help="the node key's facility id: other facilities get 403")
    args = p.parse_args()
    tokens = args.token or DEFAULT_TOKENS
    print(f"Dummy backend on http://{args.host}:{args.port}  (dashboard at /)\n"
          f"  ADMIN_URL=http://{args.host}:{args.port}/api   ADMIN_KEY={args.key}\n"
          f"  tokens: {', '.join(tokens)}   authCode: {args.auth_code}\n"
          f"  data: {args.data}")
    create_app(args.key, tokens, args.auth_code, args.data, args.outage,
               balance=args.balance, camera_doors=args.camera_doors,
               jabo=args.jabo, device_auth_code=args.device_auth_code,
               doors=args.doors, facility=args.facility).run(
        host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
