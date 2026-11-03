"""Facility node entry point: local Flask endpoints + background jobs.

LAN only. Never expose port 5454 to the internet.

    POST /qr              {token}            QR scanner; door chosen by scanner IP
    POST /api/device/code {token}            same as /qr, for scanners with that fixed path
    POST /api/device/DoorHeart               scanner heartbeat (every 30 s): recorded, opens nothing
    GET|POST /qrscanner   ?cardid=..         same, legacy scanner format + response
    POST /rakindaqrscanner {SCode}           same, Rakinda format + response
    POST /facility/open   {authCode, door}   Facility app; door = section name or door id
                          + use: customer (cash, amount?) | personal (purpose), attendant?
    GET  /facility/doors  X-Auth-Code        Facility app: each door's id, name, price, open
    POST /door/opened     ?door=<name>       bench test only (DOOR_TEST_ENDPOINTS=1)
    POST /door/closed     ?door=<name>       bench test only (DOOR_TEST_ENDPOINTS=1)
    GET  /health                             liveness + queue size

With ENABLE_DOORS=0 the door endpoints answer 404 doors_off.
"""

import logging
import threading
import time
from collections import defaultdict, deque

from flask import Flask, g, jsonify, request

from camera.camera import camera_fields
from config import SCANNER_MESSAGES
from uplink import iso

log = logging.getLogger("app")

STATUS = {"opened": 200, "denied": 403, "relay_error": 502,
          "unknown_scanner": 403, "unknown_door": 400, "rate_limited": 429,
          "bad_request": 400}

ATTENDANT_USES = ("customer", "personal")

# Scanners beat every 30 s; no beat for this long = that scanner is offline
HEARTBEAT_STALE = 120


def attendant_fields(data, headers, purposes):
    """The Facility app's use / purpose / amount / attendant -> (fields, error).

    error is None, or why the request is refused (400): bad_use, bad_purpose,
    bad_amount, no_attendant (personal use must say who). An app that sends no
    `use` at all is accepted (older app).
    """
    def get(key, header):
        v = headers.get(header) if headers.get(header) is not None else data.get(key)
        return v.strip() if isinstance(v, str) else v

    use, purpose = get("use", "X-Use"), get("purpose", "X-Purpose")
    amount, attendant = data.get("amount"), get("attendant", "X-Attendant")
    use = use.lower() if isinstance(use, str) and use else None
    if use is None:
        return {}, None
    if use not in ATTENDANT_USES:
        return None, "bad_use"
    fields = {"use": use}
    if use == "personal":
        purpose = purpose.lower() if isinstance(purpose, str) else None
        if purpose not in purposes:
            return None, "bad_purpose"
        fields["purpose"] = purpose
    elif amount not in (None, ""):
        try:
            amount = round(float(amount), 2)
        except (TypeError, ValueError):
            return None, "bad_amount"
        if amount < 0 or isinstance(data.get("amount"), bool):
            return None, "bad_amount"
        fields["amount"] = amount
    if isinstance(attendant, (str, int)) and str(attendant).strip():
        fields["attendant"] = str(attendant).strip()[:64]
    elif use == "personal":
        return None, "no_attendant"
    return fields, None

# Door endpoints, switched off with ENABLE_DOORS=0
DOOR_ENDPOINTS = {"qr", "qrscanner", "rakinda", "facility_open", "facility_doors",
                  "door_heart", "door_opened", "door_closed"}

# Backend refusal reasons that mean "top up your balance"
NO_BALANCE = {"insufficient_balance", "insufficient_funds", "no_balance", "low_balance"}


def scanner_message(settings, result, why, price=None, door_name=None):
    """What the scanner should show the person, from the scan's result and reason.

    SCANNER_MSG_OPENED may hold {price} (what this scan was charged) and {door}
    (the door's admin-panel name); without a known price the plain default is shown.
    """
    msgs = settings.scanner_messages
    if result == "opened":
        msg = msgs["opened"]
        if "{price}" in msg:
            if price is None:
                return SCANNER_MESSAGES["opened"]
            msg = msg.replace("{price}", f"{price:g}")
        return msg.replace("{door}", door_name or "")
    if result == "relay_error":
        return msgs["error"]
    if why in NO_BALANCE:
        return msgs["no_balance"]
    if why == "offline":
        return msgs["offline"]
    return msgs["denied"]


# Field names scanners use for the scanned text, in priority order
TOKEN_FIELDS = ("token", "SCode", "scode", "cardid", "code", "qr", "data")


def scanned_token(req):
    """Pull the scanned code out of whatever the scanner sent.

    Accepts JSON, form fields or query string with any TOKEN_FIELDS name, or a
    plain-text body that is just the code.
    """
    data = req.get_json(silent=True)
    sources = [data if isinstance(data, dict) else {}, req.form, req.args]
    for src in sources:
        for field in TOKEN_FIELDS:
            value = src.get(field)
            if value:
                return str(value).strip()
    if isinstance(data, str) and data.strip():
        return data.strip()
    raw = req.get_data(as_text=True).strip()
    if raw and "=" not in raw and not raw.startswith("{") and len(raw) <= 256:
        return raw
    return None


def masked(token):
    """Log form of a scanned code: its length and, for long codes, the last 4
    characters. The whole code opens the door, so it never goes in the log."""
    tail = f", ends ...{token[-4:]}" if len(token) >= 12 else ""
    return f"{len(token)} chars{tail}"


def body_shape(req):
    """What a scan without a code looked like: field names only, no values."""
    data = req.get_json(silent=True)
    if isinstance(data, dict):
        return f"JSON fields {sorted(data)}"
    if req.form:
        return f"form fields {sorted(req.form)}"
    return f"{len(req.get_data())} bytes"


class FailureLimiter:
    """Blocks a client IP after too many denied unlocks (guessing the Auth Code)."""

    def __init__(self, max_failures=5, window=60.0, clock=time.monotonic):
        self.max_failures = max_failures
        self.window = window
        self.clock = clock
        self._fails = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, q, now):
        while q and now - q[0] > self.window:
            q.popleft()

    def blocked(self, key):
        with self._lock:
            q = self._fails[key]
            self._prune(q, self.clock())
            return len(q) >= self.max_failures

    def fail(self, key):
        with self._lock:
            q = self._fails[key]
            now = self.clock()
            self._prune(q, now)
            q.append(now)


def create_app(settings, doors, store, uplink, cameras=(), limiter=None, odour=None,
               qr_api=None, camera_links=None, guard=None):
    """doors: list of DoorController, one per section. guard: netcheck.MacGuard."""
    from netcheck import scanner_name
    app = Flask(__name__)
    limiter = limiter or FailureLimiter()
    by_name = {d.name: d for d in doors}
    by_id = {d.door.door_id: d for d in doors if d.door.door_id}
    by_scanner = {ip: d for d in doors for ip in d.door.scanner_ips}
    # Single door with no scanner IPs configured: accept scans from anywhere
    any_scanner = doors[0] if len(doors) == 1 and not doors[0].door.scanner_ips else None

    @app.before_request
    def doors_off():
        if not doors and request.endpoint in DOOR_ENDPOINTS:
            return jsonify(ok=False, result="doors_off", door=None), 404

    def body():
        return request.get_json(silent=True) or {}

    def reply(result, door=None):
        extra = {"reason": g.bad_request} if "bad_request" in g else {}
        if "qr_why" in g:   # a scan: tell the scanner (and the person) why
            extra = dict(reason=g.qr_why, message=message(result, door))
        return (jsonify(ok=result == "opened", result=result,
                        door=door.name if door else None, **extra), STATUS[result])

    def message(result, door):
        name = door.details()["name"] if door is not None else None
        return scanner_message(settings, result, g.get("qr_why"), g.get("qr_price"), name)

    def scan_qr(door, token):
        result, g.qr_why, g.qr_price = door.qr_scan(token)
        return result

    def attempt(door, fn_name, value, **kw):
        """-> (result, door). Applies the failure limiter."""
        ip = request.remote_addr or "?"
        # Known scanners are exempt: a run of bad codes at the door must not
        # lock the scanner out for everyone. Tokens can't be brute-forced by
        # holding codes up to a camera anyway.
        if fn_name == "qr" and ip in by_scanner:
            return scan_qr(door, value), door
        if limiter.blocked(ip):
            return "rate_limited", None
        if door is None:
            limiter.fail(ip)
            return ("unknown_scanner" if fn_name == "qr" else "unknown_door"), None
        result = scan_qr(door, value) if fn_name == "qr" else getattr(door, fn_name)(value, **kw)
        if result == "denied":
            limiter.fail(ip)
        return result, door

    def unlock(door, fn_name, value, **kw):
        return reply(*attempt(door, fn_name, value, **kw))

    def scan():
        door = by_scanner.get(request.remote_addr) or any_scanner
        name = scanner_name(guard, request.remote_addr) if guard is not None else None
        if name and not guard.check(name, request.remote_addr):
            # Not the wired-in scanner (DOOR_<NAME>_SCANNER_MACS): logged and alerted
            g.qr_why = "device_mismatch"
            return "denied", door
        if door is None:
            log.warning("scan from unknown scanner %s", request.remote_addr)
        token = scanned_token(request)
        # Commissioning aid: shows whether the scanner's code arrives and in
        # what shape. Drop once all scanners are confirmed working.
        if token:
            log.info("scan %s from %s: code %s", request.path, request.remote_addr,
                     masked(token))
        else:
            log.warning("scan %s from %s: no code found (content-type %r, %s)",
                        request.path, request.remote_addr, request.content_type,
                        body_shape(request))
        return attempt(door, "qr", token)

    def named_door(name):
        if name is None and len(doors) == 1:
            return doors[0]
        if not name:
            return None
        return by_id.get(str(name).strip()) or by_name.get(str(name).strip().lower())

    @app.post("/qr")
    @app.post("/api/device/code")   # path some scanners' firmware posts to
    def qr():
        return reply(*scan())

    # Scanner-compatible routes: same checks as /qr, replies in the format the
    # existing scanners already understand. Drop once all scanners use /qr.
    heartbeats = {}   # scanner IP -> last heartbeat (unix s)

    @app.post("/api/device/DoorHeart")
    def door_heart():
        """The scanners' "I'm alive" call. Only remembered, for /health: a scanner
        that stops beating is offline. Anyone on the LAN can send one, so it
        never opens anything or changes a door."""
        ip = request.remote_addr or "?"
        if ip in by_scanner or any_scanner is not None:
            heartbeats[ip] = time.time()
        return jsonify(ok=True), 200

    def scanner_state(d):
        """{ip: {"last_heartbeat": iso or None, "alive": bool or None}} for one door."""
        now = time.time()
        out = {}
        for ip in d.door.scanner_ips:
            ts = heartbeats.get(ip)
            out[ip] = {"last_heartbeat": iso(ts) if ts else None,
                       "alive": None if ts is None else now - ts < HEARTBEAT_STALE}
        return out

    @app.route("/qrscanner", methods=["GET", "POST"])
    def qrscanner():
        result, door = scan()
        ok = result == "opened"
        params = request.args.to_dict() or (request.get_json(silent=True) or {})
        return jsonify(status="success" if ok else "denied", data=[params],
                       access_granted=ok, door=door.name if door else None,
                       message=message(result, door)), 200

    @app.post("/rakindaqrscanner")
    def rakinda():
        result, door = scan()
        msg = message(result, door)
        if result == "opened":
            return jsonify(ResultCode="1", Msg=msg, Audio="40"), 200
        return jsonify(ResultCode="0", Msg=msg, Audio="0"), 200

    @app.post("/facility/open")
    def facility_open():
        data = body()
        code = request.headers.get("X-Auth-Code") or data.get("authCode")
        door = named_door(request.headers.get("X-Door") or data.get("door"))
        fields, error = attendant_fields(data, request.headers, settings.attendant_purposes)
        if error:
            g.bad_request = error
            return reply("bad_request")
        return unlock(door, "facility_open", code, **fields)

    @app.get("/facility/doors")
    def facility_doors():
        """The Facility app's door buttons: id, admin-panel name and price, open.
        Needs the Auth Code (header only: a query string would land in the log).
        Works offline from the last settings pull."""
        ip = request.remote_addr or "?"
        if limiter.blocked(ip):
            return jsonify(ok=False, result="rate_limited"), STATUS["rate_limited"]
        if not doors[0].auth_code_ok(request.headers.get("X-Auth-Code")):
            limiter.fail(ip)
            return jsonify(ok=False, result="denied"), STATUS["denied"]
        return jsonify(ok=True, doors=[
            {"door": d.name, "door_id": d.door.door_id, **d.details(), "open": d.is_open}
            for d in doors])

    if settings.door_test_endpoints:
        @app.post("/door/opened")
        def door_opened():
            door = named_door(request.args.get("door"))
            if door is None:
                return reply("unknown_door")
            door.door_opened()
            return jsonify(ok=True, door=door.name)

        @app.post("/door/closed")
        def door_closed():
            door = named_door(request.args.get("door"))
            if door is None:
                return reply("unknown_door")
            door.door_closed()
            return jsonify(ok=True, door=door.name)

    @app.get("/health")
    def health():
        return jsonify(
            ok=True,
            facility=settings.facility_id,
            mac=settings.device_mac or None,
            features={"doors": settings.enable_doors, "camera": settings.enable_camera,
                      "odour": settings.enable_odour},
            queue=store.queue_size(),
            tokens=store.token_count(),
            auth_code_cached=store.get_cred("auth_code_sha256") is not None,
            # Which code the Facility app is checked against: backend pull, else .env
            auth_code_source="backend" if store.get_cred("auth_code_sha256") is not None
            else ("env" if settings.device_auth_code else None),
            last_pull=uplink.last_pull_ok,
            last_flush=uplink.last_flush_ok,
            doors={d.name: {"open": d.is_open, "relay": d.door.relay,
                            "unlock_s": d.door.unlock_seconds,
                            "scanners": list(d.door.scanner_ips),
                            "scanner_heartbeat": scanner_state(d),
                            "door_id": d.door.door_id, **d.details()} for d in doors},
            devices=guard.state() if guard else None,
            reed=settings.reed,
            **camera_fields(cameras),
            camera_monitor=settings.cam_monitor if cameras else None,
            camera_links=camera_links.state() if camera_links else None,
            odour=odour.state() if odour else None,
            qr_api=qr_api.state() if qr_api else None,
        )

    return app


def main():
    import os

    from camera.camera import Camera, CameraLinks, run_prune_loop
    from config import Settings
    from door import DoorController
    from door.info import DoorInfo
    from door.inputs import ReedSwitch, run_door_ticker, run_exit_poller
    from netcheck import MacGuard, devices_from_settings
    from door.qr_api import QRValidator, qr_auth_headers
    from door.relay import Relay
    from odour.monitor import OdourMonitor
    from odour.mq import MQSensors
    from odour.sensors import make_sensors, run_sensor_loop
    from store import Store
    from uplink import Uplink

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = Settings.from_env()
    os.makedirs(os.path.dirname(s.db_path), exist_ok=True)

    store = Store(s.db_path, s.max_queue)
    uplink = Uplink(s, store)
    if any(h in s.admin_url for h in ("localhost", "127.0.0.1")):
        log.warning("ADMIN_URL points at this machine (%s): dummy backend? "
                    "Never leave this on in production", s.admin_url)

    # Every LAN device is checked against its .env MAC before the Pi talks to it
    guard = devices_from_settings(MacGuard(s, uplink), s)
    # Each function only builds its parts when it is switched on
    relay = (Relay(s.relay_url, s.relay_pwd, verify=guard.verifier("relay"))
             if s.enable_doors else None)
    cameras = [Camera(s, spec, verify=guard.verifier(f"camera_{spec.name}"))
               for spec in s.cameras]   # empty when the camera function is off
    links = CameraLinks(s, cameras, store, uplink) if cameras else None
    odour = OdourMonitor(s, uplink) if s.enable_odour else None
    sensors = make_sensors(s, verify=guard.verifier("sensor_board")) if s.enable_odour else None
    mq = MQSensors(s) if s.enable_odour and s.mq else None
    qr_api = None
    if s.enable_doors and s.qr_api_url:
        qr_api = QRValidator(s, auth_headers=qr_auth_headers(s, uplink))
        if any(h in s.qr_api_url for h in ("localhost", "127.0.0.1")):
            log.warning("QR_API_URL points at this machine (%s): dummy API? "
                        "Never leave this on in production", s.qr_api_url)
    # Each door's alerts are pictured by its camera (admin panel, else CAM_<NAME>_DOORS)
    info = DoorInfo(s, store)   # names and prices from the admin panel
    doors = [DoorController(s, spec, store, relay, uplink,
                            snapshot=links.snapshotter(spec.name) if links else None,
                            qr_api=qr_api, info=info)
             for spec in s.doors]   # empty when doors are off

    stop = threading.Event()
    jobs = [lambda: uplink.run_flush_loop(stop)]
    if doors:
        jobs += [lambda: uplink.run_pull_loop(stop),   # tokens + Auth Code
                 lambda: run_exit_poller(s, relay, doors, stop),
                 lambda: run_door_ticker(doors, stop)]
    if sensors or cameras:
        jobs += [lambda: run_sensor_loop(s, sensors, cameras, uplink, stop, mq=mq, odour=odour,
                                         guard=guard)]
    if cameras:
        jobs += [lambda: run_prune_loop(s, stop)]
    if s.cam_monitor:   # CAM_MONITOR=0: no health checks, alert snapshots only
        for cam in cameras:
            jobs += [lambda c=cam: c.run_probe_loop(stop),
                     lambda c=cam: c.run_frame_loop(stop)]
    for job in jobs:
        threading.Thread(target=job, daemon=True).start()

    # Keep references: a garbage-collected gpiozero Button stops firing callbacks
    reeds = [ReedSwitch(s, d) for d in doors if s.reed and d.door.reed_gpio is not None]

    on = lambda flag: "on" if flag else "OFF"
    log.info("functions: doors %s, camera %s, odour %s",
             on(s.enable_doors), on(s.enable_camera), on(s.enable_odour))
    for d in s.doors:
        log.info("door %s (id %s): relay %d, unlock %g s, exit input %s, reed GPIO %s, "
                 "scanners %s, .env camera %s", d.name, d.door_id or "not set", d.relay,
                 d.unlock_seconds, d.exit_input, d.reed_gpio,
                 ", ".join(d.scanner_ips) or "any", d.camera or "none")
    checked = [n for n, dev in guard.devices.items() if dev["macs"]]
    log.info("MAC checks: %s", ", ".join(checked) or "none set (facility macs)")
    if doors:
        log.info("QR validation: %s", f"{s.qr_api_method} {s.qr_api_url} ({s.qr_api_order})"
                 if qr_api else "local cache only")
    if links:
        door_cams, source = links.links()
        for c in s.cameras:
            log.info("camera %s: %s; pictures door %s (%s)", c.name, c.host or "no host",
                     ", ".join(d for d, cam in door_cams.items() if cam == c.name) or "none",
                     "admin panel" if source == "admin" else ".env")
    if s.enable_camera:
        log.info("camera health checks %s; snapshot upload %s",
                 on(s.cam_monitor and s.cameras), s.snapshot_upload_path or "off")
    if odour:
        log.info("air sensors: %s", s.sensor_url or "I2C on the Pi")
        log.info("odour limits %s; MQ sensors %s", odour.limits or "off", on(mq))
    log.info("facility node %s listening on %s:%d", s.facility_id, s.host, s.port)
    try:
        create_app(s, doors, store, uplink, cameras, odour=odour, qr_api=qr_api,
                   camera_links=links, guard=guard).run(
            host=s.host, port=s.port, threaded=True)
    finally:
        stop.set()
        for reed in reeds:
            reed.close()


if __name__ == "__main__":
    main()
