"""Facility node entry point: local Flask endpoints + background jobs.

LAN only. Never expose port 5454 to the internet.

    POST /qr              {token}            QR scanner; door chosen by scanner IP
    GET|POST /qrscanner   ?cardid=..         same, legacy scanner format + response
    POST /rakindaqrscanner {SCode}           same, Rakinda format + response
    POST /facility/open   {authCode, door}   Facility app; door = section name
    POST /door/opened     ?door=<name>       bench test only (DOOR_TEST_ENDPOINTS=1)
    POST /door/closed     ?door=<name>       bench test only (DOOR_TEST_ENDPOINTS=1)
    GET  /health                             liveness + queue size
"""

import logging
import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, request

log = logging.getLogger("app")

STATUS = {"opened": 200, "denied": 403, "relay_error": 502,
          "unknown_scanner": 403, "unknown_door": 400, "rate_limited": 429}

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


def create_app(settings, doors, store, uplink, camera=None, limiter=None, odour=None):
    """doors: list of DoorController, one per section."""
    app = Flask(__name__)
    limiter = limiter or FailureLimiter()
    by_name = {d.name: d for d in doors}
    by_scanner = {ip: d for d in doors for ip in d.door.scanner_ips}
    # Single door with no scanner IPs configured: accept scans from anywhere
    any_scanner = doors[0] if len(doors) == 1 and not doors[0].door.scanner_ips else None

    def body():
        return request.get_json(silent=True) or {}

    def reply(result, door=None):
        return (jsonify(ok=result == "opened", result=result,
                        door=door.name if door else None), STATUS[result])

    def attempt(door, fn_name, value):
        """-> (result, door). Applies the failure limiter."""
        ip = request.remote_addr or "?"
        # Known scanners are exempt: a run of bad codes at the door must not
        # lock the scanner out for everyone. Tokens can't be brute-forced by
        # holding codes up to a camera anyway.
        if fn_name == "qr" and ip in by_scanner:
            return door.qr(value), door
        if limiter.blocked(ip):
            return "rate_limited", None
        if door is None:
            limiter.fail(ip)
            return ("unknown_scanner" if fn_name == "qr" else "unknown_door"), None
        result = getattr(door, fn_name)(value)
        if result == "denied":
            limiter.fail(ip)
        return result, door

    def unlock(door, fn_name, value):
        return reply(*attempt(door, fn_name, value))

    def scan():
        door = by_scanner.get(request.remote_addr) or any_scanner
        if door is None:
            log.warning("scan from unknown scanner %s", request.remote_addr)
        return attempt(door, "qr", scanned_token(request))

    def named_door(name):
        if name is None and len(doors) == 1:
            return doors[0]
        return by_name.get(str(name).strip().lower()) if name else None

    @app.post("/qr")
    def qr():
        return reply(*scan())

    # Scanner-compatible routes: same checks as /qr, replies in the format the
    # existing scanners already understand. Drop once all scanners use /qr.
    @app.route("/qrscanner", methods=["GET", "POST"])
    def qrscanner():
        result, door = scan()
        ok = result == "opened"
        params = request.args.to_dict() or (request.get_json(silent=True) or {})
        return jsonify(status="success" if ok else "denied", data=[params],
                       access_granted=ok, door=door.name if door else None), 200

    @app.post("/rakindaqrscanner")
    def rakinda():
        result, _ = scan()
        if result == "opened":
            return jsonify(ResultCode="1", Msg="Please Enter the facility", Audio="40"), 200
        return jsonify(ResultCode="0", Msg="Access denied", Audio="0"), 200

    @app.post("/facility/open")
    def facility_open():
        data = body()
        code = request.headers.get("X-Auth-Code") or data.get("authCode")
        door = named_door(request.headers.get("X-Door") or data.get("door"))
        return unlock(door, "facility_open", code)

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
            queue=store.queue_size(),
            tokens=store.token_count(),
            auth_code_cached=store.get_cred("auth_code_sha256") is not None,
            last_pull=uplink.last_pull_ok,
            last_flush=uplink.last_flush_ok,
            doors={d.name: {"open": d.is_open, "relay": d.door.relay,
                            "scanners": list(d.door.scanner_ips)} for d in doors},
            reed=settings.reed,
            camera=camera.snapshot_status() if camera else None,
            odour=odour.state() if odour else None,
        )

    return app


def main():
    import os

    from config import Settings
    from door import DoorController
    from inputs import ReedSwitch, run_door_ticker, run_exit_poller
    from relay import Relay
    from mq import MQSensors
    from odour import OdourMonitor
    from sensors_camera import Camera, Sensors, make_snapshotter, run_sensor_loop
    from store import Store
    from uplink import Uplink

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    s = Settings.from_env()
    os.makedirs(os.path.dirname(s.db_path), exist_ok=True)

    store = Store(s.db_path, s.max_queue)
    relay = Relay(s.relay_url, s.relay_pwd)
    uplink = Uplink(s, store)
    camera = Camera(s)
    snapshot = make_snapshotter(camera, uplink)
    doors = [DoorController(s, spec, store, relay, uplink, snapshot=snapshot)
             for spec in s.doors]
    odour = OdourMonitor(s, uplink)
    mq = MQSensors(s) if s.mq else None

    stop = threading.Event()
    jobs = [
        lambda: uplink.run_pull_loop(stop),
        lambda: uplink.run_flush_loop(stop),
        lambda: run_exit_poller(s, relay, doors, stop),
        lambda: run_door_ticker(doors, stop),
        lambda: run_sensor_loop(s, Sensors(), camera, uplink, stop, mq=mq, odour=odour),
        lambda: camera.run_probe_loop(stop),
        lambda: camera.run_frame_loop(stop),
    ]
    for job in jobs:
        threading.Thread(target=job, daemon=True).start()

    # Keep references: a garbage-collected gpiozero Button stops firing callbacks
    reeds = [ReedSwitch(s, d) for d in doors if s.reed and d.door.reed_gpio is not None]

    for d in s.doors:
        log.info("door %s: relay %d, exit input %s, reed GPIO %s, scanners %s",
                 d.name, d.relay, d.exit_input, d.reed_gpio, ", ".join(d.scanner_ips) or "any")
    log.info("odour limits %s; MQ sensors %s; snapshot upload %s", odour.limits or "off",
             "on" if mq else "off", s.snapshot_upload_path or "off")
    log.info("facility node %s listening on %s:%d", s.facility_id, s.host, s.port)
    try:
        create_app(s, doors, store, uplink, camera, odour=odour).run(
            host=s.host, port=s.port, threaded=True)
    finally:
        stop.set()
        for reed in reeds:
            reed.close()


if __name__ == "__main__":
    main()
