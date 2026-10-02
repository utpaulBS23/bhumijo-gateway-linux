"""Facility node entry point: local Flask endpoints + background jobs.

LAN only. Never expose port 5454 to the internet.

    POST /qr              {token}      QR scanner -> validate locally, open
    POST /facility/open   {authCode}   Facility app -> Auth Code check, open
    POST /door/opened                  bench test only (DOOR_TEST_ENDPOINTS=1)
    POST /door/closed                  bench test only (DOOR_TEST_ENDPOINTS=1)
    GET  /health                       liveness + queue size
"""

import logging
import threading
import time
from collections import defaultdict, deque

from flask import Flask, jsonify, request

log = logging.getLogger("app")

STATUS = {"opened": 200, "denied": 403, "relay_error": 502}


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


def create_app(settings, door, store, uplink, camera=None, limiter=None):
    app = Flask(__name__)
    limiter = limiter or FailureLimiter()

    def body():
        return request.get_json(silent=True) or {}

    def unlock(fn, value):
        ip = request.remote_addr or "?"
        if limiter.blocked(ip):
            return jsonify(ok=False, result="rate_limited"), 429
        result = fn(value)
        if result == "denied":
            limiter.fail(ip)
        return jsonify(ok=result == "opened", result=result), STATUS[result]

    @app.post("/qr")
    def qr():
        return unlock(door.qr, body().get("token"))

    @app.post("/facility/open")
    def facility_open():
        code = request.headers.get("X-Auth-Code") or body().get("authCode")
        return unlock(door.facility_open, code)

    if settings.door_test_endpoints:
        @app.post("/door/opened")
        def door_opened():
            door.door_opened()
            return jsonify(ok=True)

        @app.post("/door/closed")
        def door_closed():
            door.door_closed()
            return jsonify(ok=True)

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
            door_open=door.is_open,
            reed=settings.reed,
            camera=camera.snapshot_status() if camera else None,
        )

    return app


def main():
    import os

    from config import Settings
    from door import DoorController
    from inputs import ReedSwitch, run_door_ticker, run_exit_poller
    from relay import Relay
    from sensors_camera import Camera, Sensors, run_sensor_loop
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
    door = DoorController(s, store, relay, uplink, snapshot=camera.snapshot)

    stop = threading.Event()
    jobs = [
        lambda: uplink.run_pull_loop(stop),
        lambda: uplink.run_flush_loop(stop),
        lambda: run_exit_poller(s, relay, door, stop),
        lambda: run_door_ticker(door, stop),
        lambda: run_sensor_loop(s, Sensors(), camera, uplink, stop),
        lambda: camera.run_probe_loop(stop),
        lambda: camera.run_frame_loop(stop),
    ]
    for job in jobs:
        threading.Thread(target=job, daemon=True).start()

    if s.reed:
        ReedSwitch(s, door)

    log.info("facility node %s listening on %s:%d", s.facility_id, s.host, s.port)
    create_app(s, door, store, uplink, camera).run(host=s.host, port=s.port, threaded=True)


if __name__ == "__main__":
    main()
