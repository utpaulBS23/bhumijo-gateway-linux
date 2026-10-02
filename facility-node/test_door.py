"""Facility node tests. Run: python3 test_door.py

No hardware or network needed: relay, backend and clock are faked.
"""

import os
import tempfile
import unittest
from dataclasses import replace

import requests

from app import FailureLimiter, create_app
from config import ConfigError, Settings
from door import DoorController
from inputs import run_exit_poller
from relay import Relay, RelayError, parse_states
from sensors_camera import aht20_decode, ens160_validity, frames_differ
from store import Store
from uplink import Uplink, parse_expiry

ENV = {
    "FACILITY_ID": "facility-001",
    "RELAY_URL": "http://relay.test",
    "RELAY_PWD": "relay-secret",
    "ADMIN_URL": "https://admin.test/api",
    "ADMIN_KEY": "admin-secret",
}


def settings(**kw):
    old = dict(os.environ)
    os.environ.update(ENV)
    try:
        s = Settings.from_env()
    finally:
        os.environ.clear()
        os.environ.update(old)
    return replace(s, **kw)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakeRelay:
    def __init__(self, fail=False):
        self.fired = []
        self.fail = fail

    def fire(self, channel=1):
        if self.fail:
            raise RelayError("down")
        self.fired.append(channel)


class FakeUplink:
    def __init__(self):
        self.items = []

    def enqueue(self, kind, payload):
        self.items.append((kind, payload))

    def of(self, kind):
        return [p for k, p in self.items if k == kind]


class FakeResp:
    def __init__(self, status=200, json_body=None, text=""):
        self.status_code = status
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    """Backend stub: online/offline switch, records POSTs."""

    def __init__(self):
        self.online = True
        self.posts = []
        self.post_status = 200
        self.get_resp = FakeResp(200, {"tokens": [], "facilityId": "facility-001"})
        self.get_calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        if not self.online:
            raise requests.ConnectionError("offline")
        self.posts.append((url, json, headers))
        return FakeResp(self.post_status)

    def get(self, url, params=None, headers=None, timeout=None):
        if not self.online:
            raise requests.ConnectionError("offline")
        self.get_calls.append((url, params, headers))
        return self.get_resp


def make_door(**kw):
    s = settings(**kw)
    store = Store(":memory:")
    store.replace_tokens([("good", None), ("old", 1.0)])
    store.set_auth_code("auth-code-123")
    relay, up, clock = FakeRelay(), FakeUplink(), Clock()
    door = DoorController(s, store, relay, up, snapshot=lambda: "snap.jpg",
                          clock=clock, spawn=lambda fn: fn())
    return door, relay, up, clock, store


class DoorTests(unittest.TestCase):
    def test_valid_qr_opens_and_logs_entry(self):
        door, relay, up, _, _ = make_door()
        self.assertEqual(door.qr("good"), "opened")
        self.assertEqual(relay.fired, [1])
        ev = up.of("door_event")[0]
        self.assertEqual((ev["type"], ev["source"], ev["facility"]),
                         ("entry", "qr", "facility-001"))

    def test_invalid_qr_denied_and_door_stays_locked(self):
        door, relay, up, _, _ = make_door()
        self.assertEqual(door.qr("nope"), "denied")
        self.assertEqual(door.qr(None), "denied")
        self.assertEqual(relay.fired, [])
        self.assertEqual([e["type"] for e in up.of("door_event")], ["denied", "denied"])

    def test_expired_qr_denied(self):
        door, relay, _, _, _ = make_door()
        self.assertEqual(door.qr("old"), "denied")
        self.assertEqual(relay.fired, [])

    def test_facility_app_auth_code_opens_as_attendant(self):
        door, relay, up, _, _ = make_door()
        self.assertEqual(door.facility_open("auth-code-123"), "opened")
        self.assertEqual(relay.fired, [1])
        self.assertEqual(up.of("door_event")[0]["type"], "attendant")

    def test_facility_id_is_not_accepted_as_auth(self):
        door, relay, _, _, _ = make_door()
        self.assertEqual(door.facility_open("facility-001"), "denied")
        self.assertEqual(door.facility_open(""), "denied")
        self.assertEqual(relay.fired, [])

    def test_exit_logged_once_per_press(self):
        door, relay, up, _, _ = make_door()
        for pressed in (False, True, True, True, False, True):
            door.exit_input(pressed)
        self.assertEqual([e["type"] for e in up.of("door_event")], ["exit", "exit"])
        self.assertEqual(relay.fired, [], "exit opens in hardware; Pi must not fire")

    def test_untriggered_open_raises_forced_open_with_snapshot(self):
        door, _, up, _, _ = make_door()
        door.door_opened()
        alert = up.of("alert")[0]
        self.assertEqual((alert["type"], alert["subtype"], alert["snapshot"]),
                         ("anomaly", "forced_open", "snap.jpg"))

    def test_triggered_opens_are_not_anomalies(self):
        door, _, up, clock, _ = make_door()
        door.qr("good"); clock.advance(3); door.door_opened(); door.door_closed()
        door.exit_input(True); clock.advance(1); door.door_opened(); door.door_closed()
        self.assertEqual(up.of("alert"), [])

    def test_one_trigger_covers_only_one_opening(self):
        door, _, up, clock, _ = make_door()
        door.qr("good"); door.door_opened(); door.door_closed()
        clock.advance(2); door.door_opened()
        self.assertEqual([a["subtype"] for a in up.of("alert")], ["forced_open"])

    def test_stale_trigger_does_not_excuse_open(self):
        door, _, up, clock, _ = make_door(classify_window=15)
        door.qr("good"); clock.advance(60); door.door_opened()
        self.assertEqual(up.of("alert")[0]["subtype"], "forced_open")

    def test_boot_with_door_open_is_not_forced(self):
        door, _, up, _, _ = make_door()
        door.door_opened(at_boot=True)
        self.assertEqual(up.of("alert"), [])
        self.assertTrue(door.is_open)

    def test_propped_open_repeats_then_resolves(self):
        door, _, up, clock, _ = make_door(propped_threshold=300, propped_repeat=180)
        door.qr("good"); door.door_opened()
        clock.advance(299); door.tick()
        self.assertEqual(up.of("alert"), [])
        clock.advance(1); door.tick(); door.tick()
        clock.advance(180); door.tick()
        clock.advance(180); door.tick()
        clock.advance(20); door.door_closed()
        alerts = up.of("alert")
        self.assertEqual([(a["subtype"], a.get("repeat")) for a in alerts],
                         [("propped_open", False), ("propped_open", True),
                          ("propped_open", True), ("propped_resolved", None)])
        self.assertEqual(alerts[0]["snapshot"], "snap.jpg")
        self.assertNotIn("snapshot", alerts[1])
        self.assertEqual(alerts[-1]["duration_s"], 680)
        self.assertEqual(up.of("door_event")[-1],
                         {"facility": "facility-001", "type": "door_closed",
                          "ts": up.of("door_event")[-1]["ts"], "duration_s": 680})

    def test_short_open_sends_no_propped_alerts(self):
        door, _, up, clock, _ = make_door()
        door.qr("good"); door.door_opened(); clock.advance(30); door.tick(); door.door_closed()
        self.assertEqual(up.of("alert"), [])

    def test_relay_failure_reports_error_and_logs_nothing(self):
        door, _, up, _, _ = make_door()
        door.relay = FakeRelay(fail=True)
        self.assertEqual(door.qr("good"), "relay_error")
        self.assertEqual(up.of("door_event"), [])


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.s = settings(flush_interval=1)
        self.store = Store(":memory:")
        self.http = FakeSession()
        self.uplink = Uplink(self.s, self.store, session=self.http)

    def test_door_opens_offline_and_queue_flushes_oldest_first(self):
        self.http.online = False
        self.store.replace_tokens([("good", None)])
        self.store.set_auth_code("code")
        relay = FakeRelay()
        door = DoorController(self.s, self.store, relay, self.uplink, spawn=lambda fn: fn())
        self.assertEqual(door.qr("good"), "opened")
        self.assertEqual(door.facility_open("code"), "opened")
        self.assertEqual(self.uplink.flush(), 0)
        self.assertEqual(self.store.queue_size(), 2)

        self.http.online = True
        self.assertEqual(self.uplink.flush(), 2)
        self.assertEqual(self.store.queue_size(), 0)
        types = [p["type"] for _, p, _ in self.http.posts]
        self.assertEqual(types, ["entry", "attendant"])
        self.assertTrue(self.http.posts[0][0].endswith(self.s.door_event_path))
        self.assertEqual(self.http.posts[0][2], {"Authorization": "Bearer admin-secret"})

    def test_server_error_keeps_queue(self):
        self.uplink.enqueue("sensor", {"x": 1})
        self.http.post_status = 503
        self.assertEqual(self.uplink.flush(), 0)
        self.assertEqual(self.store.queue_size(), 1)

    def test_rejected_row_dropped_after_retries(self):
        self.uplink.enqueue("alert", {"bad": True})
        self.uplink.enqueue("alert", {"good": True})
        self.http.post_status = 422
        for _ in range(5):
            self.uplink.flush()
        self.http.post_status = 200
        self.uplink.flush()
        self.assertEqual([p for _, p, _ in self.http.posts][-1], {"good": True})
        self.assertEqual(self.store.queue_size(), 0)

    def test_pull_failure_keeps_cache(self):
        self.store.replace_tokens([("cached", None)])
        self.http.online = False
        self.assertFalse(self.uplink.pull_tokens())
        self.assertTrue(self.store.token_valid("cached"))

    def test_pull_replaces_cache_and_auth_code(self):
        self.store.replace_tokens([("revoked", None)])
        self.http.get_resp = FakeResp(200, {
            "facilityId": "facility-001", "authCode": "new-code",
            "tokens": [{"token": "t1", "expiresAt": "2099-01-01T00:00:00Z"},
                       {"token": "t2", "expiresAt": None}]})
        self.assertTrue(self.uplink.pull_tokens())
        self.assertFalse(self.store.token_valid("revoked"))
        self.assertTrue(self.store.token_valid("t1"))
        self.assertTrue(self.store.token_valid("t2"))
        self.assertTrue(self.store.auth_code_valid("new-code"))
        self.assertEqual(self.http.get_calls[0][1], {"facility": "facility-001"})

    def test_pull_for_other_facility_ignored(self):
        self.store.replace_tokens([("cached", None)])
        self.http.get_resp = FakeResp(200, {"facilityId": "facility-999", "tokens": []})
        self.assertFalse(self.uplink.pull_tokens())
        self.assertTrue(self.store.token_valid("cached"))


class StoreTests(unittest.TestCase):
    def test_queue_and_cache_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "facility.db")
            st = Store(path)
            st.replace_tokens([("good", None)])
            st.set_auth_code("code")
            st.push("door_event", {"type": "entry"})
            st.close()
            st = Store(path)
            self.assertTrue(st.token_valid("good"))
            self.assertTrue(st.auth_code_valid("code"))
            self.assertEqual(st.peek()[0][2], {"type": "entry"})
            st.close()

    def test_auth_code_stored_hashed(self):
        st = Store(":memory:")
        st.set_auth_code("plain")
        self.assertNotIn("plain", st.get_cred("auth_code_sha256"))

    def test_queue_cap_sheds_sensor_readings_first(self):
        st = Store(":memory:", max_queue=3)
        st.push("door_event", {"n": 1})
        for i in range(5):
            st.push("sensor", {"n": i})
        st.push("alert", {"n": 2})
        kinds = [k for _, k, _, _ in st.peek()]
        self.assertEqual(kinds, ["door_event", "sensor", "alert"])


class RelayTests(unittest.TestCase):
    def test_parse_states(self):
        self.assertEqual(parse_states("Relay01=ON&Relay02=OFF&Input01=ON\n"),
                         {"Relay01": True, "Relay02": False, "Input01": True})

    def test_fire_sends_on_only(self):
        sess = FakeSession()
        calls = []
        sess.get = lambda url, params=None, timeout=None: calls.append((url, params)) or FakeResp(200, text="ok")
        Relay("http://r", "pw", session=sess).fire(1)
        self.assertEqual(calls, [("http://r/sw_ctl.cgi", {"postpwd": "pw", "Relay01": "ON"})])

    def test_inputs_missing_on_old_firmware(self):
        sess = FakeSession()
        sess.get = lambda *a, **k: FakeResp(200, text="Relay01=OFF&Relay02=OFF&Relay03=OFF&Relay04=OFF")
        self.assertIsNone(Relay("http://r", "pw", session=sess).read_inputs())

    def test_inputs_parsed(self):
        sess = FakeSession()
        sess.get = lambda *a, **k: FakeResp(200, text=(
            "Relay01=OFF&Relay02=OFF&Relay03=OFF&Relay04=OFF"
            "&Input01=ON&Input02=OFF&Input03=OFF&Input04=OFF"))
        self.assertEqual(Relay("http://r", "pw", session=sess).read_inputs(),
                         [True, False, False, False])

    def test_wrong_password_raises(self):
        sess = FakeSession()
        sess.get = lambda *a, **k: FakeResp(401)
        with self.assertRaises(RelayError):
            Relay("http://r", "pw", session=sess).fire(1)

    def test_exit_poller_feeds_door(self):
        import threading
        stop = threading.Event()
        seen = []

        class R:
            def __init__(self):
                self.n = 0

            def read_inputs(self):
                self.n += 1
                if self.n >= 3:
                    stop.set()
                return [self.n == 2, False, False, False]

        class D:
            def exit_input(self, pressed):
                seen.append(pressed)

        run_exit_poller(settings(exit_poll_interval=0), R(), D(), stop)
        self.assertEqual(seen, [False, True, False])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.door, self.relay, self.up, _, self.store = make_door()
        self.uplink = Uplink(self.door.s, self.store, session=FakeSession())

    def client(self, **kw):
        s = replace(self.door.s, **kw)
        return create_app(s, self.door, self.store, self.uplink,
                          limiter=FailureLimiter(max_failures=3)).test_client()

    def test_endpoints(self):
        c = self.client()
        self.assertEqual(c.post("/qr", json={"token": "good"}).status_code, 200)
        self.assertEqual(c.post("/qr", json={"token": "bad"}).status_code, 403)
        self.assertEqual(c.post("/facility/open", json={"authCode": "auth-code-123"}).status_code, 200)
        self.assertEqual(c.post("/facility/open", headers={"X-Auth-Code": "auth-code-123"}).status_code, 200)
        h = c.get("/health").json
        self.assertEqual((h["facility"], h["tokens"]), ("facility-001", 2))

    def test_door_test_endpoints_off_by_default(self):
        self.assertEqual(self.client().post("/door/opened").status_code, 404)
        c = self.client(door_test_endpoints=True)
        self.assertEqual(c.post("/door/opened").status_code, 200)
        self.assertTrue(self.door.is_open)

    def test_repeated_failures_rate_limited(self):
        c = self.client()
        for _ in range(3):
            self.assertEqual(c.post("/facility/open", json={"authCode": "x"}).status_code, 403)
        r = c.post("/facility/open", json={"authCode": "auth-code-123"})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.relay.fired, [])


class MiscTests(unittest.TestCase):
    def test_required_config(self):
        old = dict(os.environ)
        try:
            os.environ.update(ENV)
            os.environ["ADMIN_KEY"] = "CHANGE_ME"
            with self.assertRaises(ConfigError):
                Settings.from_env()
        finally:
            os.environ.clear()
            os.environ.update(old)

    def test_aht20_decode(self):
        # 50% RH, 25 C
        hum = int(0.5 * 1048576)
        tmp = int((25 + 50) / 200 * 1048576)
        raw = [0x1C, hum >> 12, (hum >> 4) & 0xFF, ((hum & 0xF) << 4) | (tmp >> 16),
               (tmp >> 8) & 0xFF, tmp & 0xFF]
        t, h = aht20_decode(raw)
        self.assertAlmostEqual(t, 25.0, places=1)
        self.assertAlmostEqual(h, 50.0, places=1)

    def test_ens160_validity(self):
        self.assertEqual(ens160_validity(0b10000011), 0)
        self.assertEqual(ens160_validity(0b00000100), 1)
        self.assertEqual(ens160_validity(0b00001100), 3)

    def test_frames_differ(self):
        a = bytes([100] * 64)
        self.assertFalse(frames_differ(a, a))
        self.assertTrue(frames_differ(a, bytes([110] * 64)))

    def test_parse_expiry(self):
        self.assertIsNone(parse_expiry(None))
        self.assertEqual(parse_expiry(1_900_000_000), 1_900_000_000)
        self.assertEqual(parse_expiry(1_900_000_000_000), 1_900_000_000)
        self.assertEqual(parse_expiry("2030-01-01T00:00:00Z"), 1893456000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
