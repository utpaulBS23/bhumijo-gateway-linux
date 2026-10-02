"""Facility node tests. Run: python3 test_door.py

No hardware or network needed: relay, backend and clock are faked.
"""

import os
import tempfile
import unittest
from dataclasses import replace

import requests

from app import FailureLimiter, create_app
from config import ConfigError, DoorSpec, Settings
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


TWO_DOORS = {
    "DOORS": "male,female",
    "DOOR_MALE_RELAY": "1", "DOOR_MALE_EXIT_INPUT": "0", "DOOR_MALE_REED_GPIO": "27",
    "DOOR_MALE_SCANNER_IPS": "192.168.10.101",
    "DOOR_FEMALE_RELAY": "2", "DOOR_FEMALE_EXIT_INPUT": "1", "DOOR_FEMALE_REED_GPIO": "22",
    "DOOR_FEMALE_SCANNER_IPS": "192.168.10.102",
}

MAIN = DoorSpec("main", 1, 0, 27, ())


def settings(env=None, **kw):
    old = dict(os.environ)
    os.environ.update(ENV)
    os.environ.update(env or {})
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
        self.uploads = []
        self.post_status = 200
        self.get_resp = FakeResp(200, {"tokens": [], "facilityId": "facility-001"})
        self.get_calls = []

    def post(self, url, json=None, headers=None, timeout=None, files=None, data=None):
        if not self.online:
            raise requests.ConnectionError("offline")
        if files is not None:
            name, fh, ctype = files["file"]
            self.uploads.append((url, name, fh.read(), ctype, data, headers))
            return FakeResp(self.post_status)
        self.posts.append((url, json, headers))
        return FakeResp(self.post_status)

    def get(self, url, params=None, headers=None, timeout=None):
        if not self.online:
            raise requests.ConnectionError("offline")
        self.get_calls.append((url, params, headers))
        return self.get_resp


def make_doors(env=None, **kw):
    s = settings(env, **kw)
    store = Store(":memory:")
    store.replace_tokens([("good", None), ("old", 1.0)])
    store.set_auth_code("auth-code-123")
    relay, up, clock = FakeRelay(), FakeUplink(), Clock()
    doors = [DoorController(s, spec, store, relay, up, snapshot=lambda: "snap.jpg",
                            clock=clock, spawn=lambda fn: fn()) for spec in s.doors]
    return doors, relay, up, clock, store


def make_door(**kw):
    doors, relay, up, clock, store = make_doors(**kw)
    return doors[0], relay, up, clock, store


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
                         {"facility": "facility-001", "section": "main", "type": "door_closed",
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
        door = DoorController(self.s, MAIN, self.store, relay, self.uplink, spawn=lambda fn: fn())
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
            door = MAIN

            def exit_input(self, pressed):
                seen.append(pressed)

        run_exit_poller(settings(exit_poll_interval=0), R(), [D()], stop)
        self.assertEqual(seen, [False, True, False])


class AppTests(unittest.TestCase):
    def setUp(self):
        self.door, self.relay, self.up, _, self.store = make_door()
        self.uplink = Uplink(self.door.s, self.store, session=FakeSession())

    def client(self, **kw):
        s = replace(self.door.s, **kw)
        return create_app(s, [self.door], self.store, self.uplink,
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


class TwoDoorTests(unittest.TestCase):
    """Male door: scanner .101, Relay01, Input01, GPIO27.
    Female door: scanner .102, Relay02, Input02, GPIO22."""

    def setUp(self):
        self.doors, self.relay, self.up, self.clock, self.store = make_doors(TWO_DOORS)
        self.male, self.female = self.doors
        uplink = Uplink(self.male.s, self.store, session=FakeSession())
        self.c = create_app(self.male.s, self.doors, self.store, uplink,
                            limiter=FailureLimiter(max_failures=3)).test_client()

    def scan(self, ip, token="good"):
        return self.c.post("/qr", json={"token": token}, environ_base={"REMOTE_ADDR": ip})

    def test_config_parsed(self):
        self.assertEqual(self.male.door, DoorSpec("male", 1, 0, 27, ("192.168.10.101",)))
        self.assertEqual(self.female.door, DoorSpec("female", 2, 1, 22, ("192.168.10.102",)))

    def test_scanner_ip_selects_door(self):
        r = self.scan("192.168.10.101")
        self.assertEqual((r.status_code, r.json["door"]), (200, "male"))
        r = self.scan("192.168.10.102")
        self.assertEqual((r.status_code, r.json["door"]), (200, "female"))
        self.assertEqual(self.relay.fired, [1, 2])
        self.assertEqual([(e["section"], e["type"]) for e in self.up.of("door_event")],
                         [("male", "entry"), ("female", "entry")])

    def test_any_valid_token_opens_either_door(self):
        self.assertEqual(self.scan("192.168.10.101").status_code, 200)
        self.assertEqual(self.scan("192.168.10.102").status_code, 200)

    def test_unknown_scanner_rejected(self):
        r = self.scan("192.168.10.50")
        self.assertEqual((r.status_code, r.json["result"]), (403, "unknown_scanner"))
        self.assertEqual(self.relay.fired, [])

    def test_bad_token_denied_at_its_door(self):
        r = self.scan("192.168.10.102", token="nope")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.up.of("door_event")[0]["section"], "female")

    def test_facility_app_must_name_door(self):
        r = self.c.post("/facility/open", json={"authCode": "auth-code-123"})
        self.assertEqual((r.status_code, r.json["result"]), (400, "unknown_door"))
        r = self.c.post("/facility/open", json={"authCode": "auth-code-123", "door": "female"})
        self.assertEqual((r.status_code, r.json["door"]), (200, "female"))
        r = self.c.post("/facility/open", headers={"X-Auth-Code": "auth-code-123", "X-Door": "Male"})
        self.assertEqual((r.status_code, r.json["door"]), (200, "male"))
        self.assertEqual(self.relay.fired, [2, 1])

    def test_doors_track_state_independently(self):
        self.scan("192.168.10.101")
        self.male.door_opened()       # legit: male trigger
        self.female.door_opened()     # no female trigger -> forced
        alerts = self.up.of("alert")
        self.assertEqual([(a["section"], a["subtype"]) for a in alerts],
                         [("female", "forced_open")])

    def test_exit_inputs_routed_per_door(self):
        import threading
        stop = threading.Event()

        class R:
            n = 0

            def read_inputs(self):
                R.n += 1
                if R.n >= 2:
                    stop.set()
                return [False, True, False, False]   # Input02 = female exit

        run_exit_poller(replace(self.male.s, exit_poll_interval=0), R(), self.doors, stop)
        self.assertEqual([(e["section"], e["type"]) for e in self.up.of("door_event")],
                         [("female", "exit")])

    def test_known_scanner_never_rate_limited(self):
        for _ in range(10):
            self.assertEqual(self.scan("192.168.10.101", token="nope").status_code, 403)
        self.assertEqual(self.scan("192.168.10.101").status_code, 200)

    def test_scanner_ip_not_exempt_on_facility_open(self):
        env = {"REMOTE_ADDR": "192.168.10.101"}
        for _ in range(3):
            self.c.post("/facility/open", json={"authCode": "x", "door": "male"}, environ_base=env)
        r = self.c.post("/facility/open", json={"authCode": "auth-code-123", "door": "male"},
                        environ_base=env)
        self.assertEqual(r.status_code, 429)

    def test_unknown_scanner_rate_limited(self):
        for _ in range(3):
            self.scan("192.168.10.50")
        self.assertEqual(self.scan("192.168.10.50").status_code, 429)

    def test_health_lists_doors(self):
        h = self.c.get("/health").json
        self.assertEqual(sorted(h["doors"]), ["female", "male"])
        self.assertEqual(h["doors"]["female"]["scanners"], ["192.168.10.102"])


class ScannerFormatTests(unittest.TestCase):
    """Every known scanner format reaches the same token check."""

    def setUp(self):
        self.doors, self.relay, self.up, _, self.store = make_doors(TWO_DOORS)
        uplink = Uplink(self.doors[0].s, self.store, session=FakeSession())
        self.c = create_app(self.doors[0].s, self.doors, self.store, uplink).test_client()
        self.env = {"REMOTE_ADDR": "192.168.10.102"}

    def test_json_token(self):
        r = self.c.post("/qr", json={"token": "good"}, environ_base=self.env)
        self.assertEqual(r.status_code, 200)

    def test_plain_text_body(self):
        r = self.c.post("/qr", data="good\r\n", content_type="text/plain", environ_base=self.env)
        self.assertEqual(r.status_code, 200)

    def test_form_field(self):
        r = self.c.post("/qr", data={"code": "good"}, environ_base=self.env)
        self.assertEqual(r.status_code, 200)

    def test_legacy_qrscanner_get(self):
        r = self.c.get("/qrscanner?cardid=good&cjihao=X&mjihao=1&status=1", environ_base=self.env)
        self.assertEqual((r.json["status"], r.json["access_granted"]), ("success", True))
        r = self.c.get("/qrscanner?cardid=bad", environ_base=self.env)
        self.assertEqual((r.status_code, r.json["access_granted"]), (200, False))

    def test_rakinda(self):
        r = self.c.post("/rakindaqrscanner", json={"SCode": "good"}, environ_base=self.env)
        self.assertEqual(r.json["ResultCode"], "1")
        r = self.c.post("/rakindaqrscanner", json={"SCode": "bad"}, environ_base=self.env)
        self.assertEqual(r.json["ResultCode"], "0")
        self.assertEqual(self.relay.fired, [2])

    def test_compat_routes_still_route_by_scanner_ip(self):
        r = self.c.post("/rakindaqrscanner", json={"SCode": "good"},
                        environ_base={"REMOTE_ADDR": "10.9.9.9"})
        self.assertEqual(r.json["ResultCode"], "0")
        self.assertEqual(self.relay.fired, [])

    def test_empty_scan_denied(self):
        r = self.c.post("/qr", data="", environ_base=self.env)
        self.assertEqual(r.status_code, 403)


class LocalCodeTests(unittest.TestCase):
    def test_local_code_opens_and_survives_pull(self):
        door, relay, up, clock, store = make_door()
        store.add_local_token("fn-local-test-code-123", "test", clock() + 3600)
        store.replace_tokens([])   # backend pull wipes backend tokens only
        self.assertEqual(door.qr("fn-local-test-code-123"), "opened")
        self.assertEqual(up.of("door_event")[0]["source"], "qr_local")

    def test_local_code_expires(self):
        door, _, _, clock, store = make_door()
        store.add_local_token("fn-local-test-code-123", "test", clock() + 60)
        clock.advance(61)
        self.assertEqual(door.qr("fn-local-test-code-123"), "denied")

    def test_backend_token_source_is_qr(self):
        door, _, up, _, _ = make_door()
        door.qr("good")
        self.assertEqual(up.of("door_event")[0]["source"], "qr")

    def test_qr_tool_create_list_revoke(self):
        import contextlib
        import io

        import qr_tool
        with tempfile.TemporaryDirectory() as d:
            db, out = os.path.join(d, "f.db"), os.path.join(d, "qr")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                qr_tool.main(["--db", db, "create", "--label", "cleaners", "--count", "3",
                              "--hours", "2", "--out", out])
                qr_tool.main(["--db", db, "create", "--label", "test", "--out", out])
            st = Store(db)
            rows = st.list_local_tokens()
            self.assertEqual(sorted(r[1] for r in rows), ["cleaners"] * 3 + ["test"])
            self.assertTrue(all(st.token_valid(r[0]) for r in rows))
            self.assertEqual(len([f for f in os.listdir(out) if f.endswith(".png")]), 4)
            st.close()
            with contextlib.redirect_stdout(io.StringIO()):
                qr_tool.main(["--db", db, "revoke", "--label", "cleaners"])
            st = Store(db)
            self.assertEqual([r[1] for r in st.list_local_tokens()], ["test"])
            st.close()

    def test_qr_image_decodes_to_token(self):
        # Round-trip: the PNG must contain exactly the token (skips if no decoder)
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not installed")
        import contextlib
        import io

        import qr_tool
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                path = qr_tool.write_qr("fn-roundtrip-token-0001", "t", d)
            text, _, _ = cv2.QRCodeDetector().detectAndDecode(cv2.imread(path))
            self.assertEqual(text, "fn-roundtrip-token-0001")


class DoorConfigTests(unittest.TestCase):
    def bad(self, **overrides):
        env = dict(TWO_DOORS, **overrides)
        with self.assertRaises(ConfigError):
            settings(env)

    def test_duplicate_relay_rejected(self):
        self.bad(DOOR_FEMALE_RELAY="1")

    def test_duplicate_scanner_rejected(self):
        self.bad(DOOR_FEMALE_SCANNER_IPS="192.168.10.101")

    def test_duplicate_reed_gpio_rejected(self):
        self.bad(DOOR_FEMALE_REED_GPIO="27")

    def test_missing_scanner_ips_rejected_for_multi_door(self):
        self.bad(DOOR_FEMALE_SCANNER_IPS="")

    def test_missing_relay_rejected(self):
        self.bad(DOOR_FEMALE_RELAY="")

    def test_bad_ip_rejected(self):
        with self.assertRaises(ValueError):
            settings(dict(TWO_DOORS, DOOR_MALE_SCANNER_IPS="192.168.10.999"))

    def test_optional_exit_and_reed(self):
        s = settings(dict(TWO_DOORS, DOOR_FEMALE_EXIT_INPUT="none", DOOR_FEMALE_REED_GPIO=""))
        self.assertEqual((s.doors[1].exit_input, s.doors[1].reed_gpio), (None, None))

    def test_single_door_fallback(self):
        s = settings()
        self.assertEqual(s.doors, (MAIN,))


class OdourTests(unittest.TestCase):
    def setUp(self):
        from odour import OdourMonitor
        self.s = settings(odour_tvoc_limit=1000, odour_aqi_limit=4, odour_nh3_limit=10,
                          odour_hold=600, odour_repeat=1800, odour_clear_hold=300)
        self.up, self.clock = FakeUplink(), Clock()
        self.m = OdourMonitor(self.s, self.up, clock=self.clock)

    def feed(self, minutes, **reading):
        for _ in range(minutes):
            self.m.update(dict(dict(tvoc=200, aqi=2, nh3_ppm=1), **reading))
            self.clock.advance(60)

    def alerts(self):
        return [(a["subtype"], a.get("repeat")) for a in self.up.of("alert")]

    def test_alert_after_hold_then_repeat_then_resolve(self):
        self.feed(10, tvoc=1500)                  # t=0..540: not yet 600 s
        self.assertEqual(self.alerts(), [])
        self.feed(1, tvoc=1500)                   # t=600 -> first alert
        self.assertEqual(self.alerts(), [("odour_high", False)])
        a = self.up.of("alert")[0]
        self.assertEqual((a["metrics"], a["readings"]["tvoc"], a["duration_s"]),
                         (["tvoc"], 1500, 600))
        self.feed(30, tvoc=1500)                  # t=2400 -> repeat
        self.assertEqual(self.alerts()[-1], ("odour_high", True))
        self.feed(5)                              # low 0..240 s: not cleared yet
        self.assertNotIn("odour_resolved", [x[0] for x in self.alerts()])
        self.feed(1)                              # low for 300 s -> resolved
        self.assertEqual(self.alerts()[-1], ("odour_resolved", None))
        self.assertFalse(self.m.alerting)

    def test_brief_spike_does_not_alert(self):
        self.feed(5, aqi=5)
        self.feed(1)
        self.feed(6, aqi=5)
        self.assertEqual(self.alerts(), [])

    def test_short_dip_while_alerting_does_not_resolve(self):
        self.feed(11, nh3_ppm=20)
        self.feed(2)                              # 2 min dip < clear hold
        self.feed(3, nh3_ppm=20)
        self.feed(4)
        self.assertEqual(self.alerts(), [("odour_high", False)])

    def test_missing_readings_hold_state(self):
        self.feed(11, tvoc=1500)
        for _ in range(20):                       # sensor warming up / failed
            self.m.update(dict(tvoc=None, aqi=None, nh3_ppm=None))
            self.clock.advance(60)
        self.assertTrue(self.m.alerting)
        self.assertEqual(self.alerts(), [("odour_high", False)])

    def test_disabled_metric_ignored(self):
        from odour import OdourMonitor
        m = OdourMonitor(replace(self.s, odour_tvoc_limit=None), self.up, clock=self.clock)
        for _ in range(20):
            m.update(dict(tvoc=99999, aqi=1, nh3_ppm=0))
            self.clock.advance(60)
        self.assertEqual(self.up.of("alert"), [])

    def test_no_limits_no_alerts(self):
        from odour import OdourMonitor
        s = replace(self.s, odour_tvoc_limit=None, odour_aqi_limit=None, odour_nh3_limit=None)
        m = OdourMonitor(s, self.up, clock=self.clock)
        m.update(dict(tvoc=99999, aqi=5))
        self.assertEqual(m.limits, {})


class MQTests(unittest.TestCase):
    def test_ads1115_config_bits(self):
        from mq import ads1115_config
        self.assertEqual(ads1115_config(0), 0xC383)
        self.assertEqual(ads1115_config(1), 0xD383)
        with self.assertRaises(ValueError):
            ads1115_config(4)

    def test_ads1115_volts(self):
        from mq import ads1115_volts
        self.assertAlmostEqual(ads1115_volts(0x40, 0x00), 2.048)
        self.assertAlmostEqual(ads1115_volts(0xFF, 0xFF), -0.000125)

    def test_rs_and_ppm(self):
        from mq import mq_ppm, mq_rs
        self.assertAlmostEqual(mq_rs(2.5, 5.0, 10), 10.0)
        self.assertIsNone(mq_rs(0, 5.0, 10))
        self.assertAlmostEqual(mq_ppm(10, 10, "mq135_nh3"), 102.2)
        self.assertIsNone(mq_ppm(10, None, "mq135_nh3"))
        # Lower Rs (more gas) -> more ppm
        self.assertGreater(mq_ppm(5, 10, "mq136_h2s"), mq_ppm(8, 10, "mq136_h2s"))

    class FakeADC:
        def __init__(self, volts):
            self.v = volts

        def volts(self, ch):
            return self.v[ch]

    def test_reader_applies_divider_and_r0(self):
        from mq import MQSensors, mq_ppm
        s = settings(mq135_r0=10.0, mq136_r0=None)
        # ADS sees 1.6667 V -> sensor 2.5 V after x1.5 divider -> Rs = 10 kOhm
        mq = MQSensors(s, adc=self.FakeADC({0: 2.5 / 1.5, 1: 1.0}), samples=3)
        out = mq.read()
        self.assertAlmostEqual(out["nh3_ppm"], round(mq_ppm(10, 10, "mq135_nh3"), 2))
        self.assertIsNone(out["h2s_ppm"], "no R0 -> null ppm")

    def test_reader_failure_returns_nulls(self):
        from mq import MQSensors

        class Broken:
            def volts(self, ch):
                raise OSError("i2c")

        mq = MQSensors(settings(mq135_r0=10.0, mq136_r0=10.0), adc=Broken())
        mq.adc = Broken()
        self.assertEqual(mq.read(), {"nh3_ppm": None, "h2s_ppm": None})

    def test_calibrate_divides_by_clean_air_ratio(self):
        from mq import calibrate
        adc = self.FakeADC({0: 2.5 / 1.5, 1: 2.5 / 1.5})   # Rs = 10 kOhm on both
        r0 = calibrate({"MQ135_CLEAN_RATIO": "3.6", "MQ136_CLEAN_RATIO": "2.0"},
                       seconds=0, adc=adc)
        self.assertEqual(r0, {"MQ135_R0": round(10 / 3.6, 3), "MQ136_R0": 5.0})

    def test_channel_none_skips_sensor(self):
        from mq import MQSensors
        mq = MQSensors(settings(env={"MQ136_CHANNEL": "none"}), adc=self.FakeADC({0: 1.0}))
        self.assertEqual(list(mq.read()), ["nh3_ppm"])


class SnapshotUploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = settings(snapshot_upload_path="/facility/snapshot", snapshot_dir=self.tmp.name)
        self.store = Store(":memory:")
        self.http = FakeSession()
        self.uplink = Uplink(self.s, self.store, session=self.http)

    def tearDown(self):
        self.tmp.cleanup()

    def jpeg(self, name="a.jpg", data=b"\xff\xd8jpeg"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_snapshot_uploaded_before_its_alert(self):
        from sensors_camera import make_snapshotter
        self.jpeg("snap1.jpg")

        class Cam:
            s = self.s

            def snapshot(self):
                return "snap1.jpg"

        door = DoorController(self.s, MAIN, self.store, FakeRelay(), self.uplink,
                              snapshot=make_snapshotter(Cam(), self.uplink), spawn=lambda fn: fn())
        door.door_opened()   # forced_open -> snapshot + alert
        self.assertEqual(self.uplink.flush(), 2)
        url, name, data, ctype, form, headers = self.http.uploads[0]
        self.assertTrue(url.endswith("/facility/snapshot"))
        self.assertEqual((name, data, ctype), ("snap1.jpg", b"\xff\xd8jpeg", "image/jpeg"))
        self.assertEqual(form["name"], "snap1.jpg")
        self.assertEqual(headers, {"Authorization": "Bearer admin-secret"})
        self.assertEqual(self.http.posts[0][1]["snapshot"], "snap1.jpg")

    def test_upload_retries_while_offline(self):
        self.uplink.queue_snapshot("a.jpg", self.jpeg(), "t")
        self.http.online = False
        self.assertEqual(self.uplink.flush(), 0)
        self.http.online = True
        self.assertEqual(self.uplink.flush(), 1)

    def test_missing_file_skipped(self):
        self.uplink.queue_snapshot("gone.jpg", os.path.join(self.tmp.name, "gone.jpg"), "t")
        self.uplink.enqueue("sensor", {"x": 1})
        self.assertEqual(self.uplink.flush(), 1)
        self.assertEqual(self.store.queue_size(), 0)

    def test_disabled_by_default(self):
        up = Uplink(settings(), self.store, session=self.http)
        up.queue_snapshot("a.jpg", self.jpeg(), "t")
        self.assertEqual(self.store.queue_size(), 0)

    def test_prune_old_snapshots(self):
        from sensors_camera import Camera
        old, new = self.jpeg("old.jpg"), self.jpeg("new.jpg")
        os.utime(old, (0, 0))
        cam = Camera(replace(self.s, snapshot_keep_days=7))
        self.assertEqual(cam.prune_snapshots(), 1)
        self.assertEqual(os.listdir(self.tmp.name), ["new.jpg"])


class StubQRSession:
    """Scripted QR API: reply(token) -> FakeResp, or raise for network errors."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def _do(self, method, url, data, headers, timeout):
        self.calls.append((method, url, data, headers, timeout))
        return self.reply(data)

    def post(self, url, json=None, headers=None, timeout=None):
        return self._do("POST", url, json, headers, timeout)

    def get(self, url, params=None, headers=None, timeout=None):
        return self._do("GET", url, params, headers, timeout)


def qr_settings(**kw):
    env = {"QR_API_URL": "/qr/validate"}
    env.update(kw.pop("env", {}))
    return settings(env, **kw)


class QRValidatorTests(unittest.TestCase):
    def check(self, resp, **kw):
        from qr_api import QRValidator
        sess = StubQRSession(lambda d: resp() if callable(resp) else resp)
        v = QRValidator(qr_settings(**kw), session=sess, auth_headers={"X": "k"})
        return v.check("TOKEN-123", "male"), sess

    def test_request_shape_post(self):
        verdict, sess = self.check(FakeResp(200, {"allowed": True}))
        self.assertEqual(verdict, "allow")
        method, url, data, headers, timeout = sess.calls[0]
        self.assertEqual((method, url), ("POST", "https://admin.test/api/qr/validate"))
        self.assertEqual(data, {"token": "TOKEN-123", "facility": "facility-001", "door": "male"})
        self.assertEqual((headers, timeout), ({"X": "k"}, 3.0))

    def test_request_shape_get_custom_fields(self):
        verdict, sess = self.check(FakeResp(200, {"data": {"access_granted": 1}}), env={
            "QR_API_URL": "https://other.test/api/user/accesses", "QR_API_METHOD": "get",
            "QR_API_TOKEN_FIELD": "card_id", "QR_API_FACILITY_FIELD": "",
            "QR_API_DOOR_FIELD": "gender", "QR_API_EXTRA": '{"device_token": "d1"}',
            "QR_API_ALLOW_FIELD": "data.access_granted"})
        self.assertEqual(verdict, "allow")
        method, url, data, _, _ = sess.calls[0]
        self.assertEqual((method, url), ("GET", "https://other.test/api/user/accesses"))
        self.assertEqual(data, {"card_id": "TOKEN-123", "gender": "male", "device_token": "d1"})

    def test_allow_values(self):
        for value, want in [(True, "allow"), ("granted", "allow"), ("YES", "allow"), (1, "allow"),
                            (False, "deny"), ("denied", "deny"), (0, "deny"), (None, "deny")]:
            self.assertEqual(self.check(FakeResp(200, {"allowed": value}))[0], want, value)

    def test_status_codes(self):
        self.assertEqual(self.check(FakeResp(403))[0], "deny")
        self.assertEqual(self.check(FakeResp(404))[0], "deny")
        self.assertEqual(self.check(FakeResp(500))[0], "unavailable")
        self.assertEqual(self.check(FakeResp(401))[0], "unavailable", "our key is wrong, not the code")
        self.assertEqual(self.check(FakeResp(200, None))[0], "unavailable", "non-JSON reply")

    def test_network_error_unavailable(self):
        def boom():
            raise requests.Timeout("slow")
        self.assertEqual(self.check(boom)[0], "unavailable")

    def test_dig(self):
        from qr_api import dig
        self.assertEqual(dig({"a": {"b": [5, {"c": 1}]}}, "a.b.1.c"), 1)
        self.assertIsNone(dig({"a": 1}, "a.b"))


class QRApiDoorTests(unittest.TestCase):
    """Door decisions with online validation in both orders."""

    def door(self, verdicts, order="local_first"):
        from qr_api import QRValidator
        s = qr_settings(env={"QR_API_ORDER": order})
        store = Store(":memory:")
        store.replace_tokens([("cached", None)])
        store.add_local_token("fn-pi-made-code-0001", "test", None)
        replies = iter(verdicts)

        def reply(data):
            v = next(replies)
            if v == "down":
                raise requests.ConnectionError("down")
            return FakeResp(200, {"allowed": v == "allow"})

        sess = StubQRSession(reply)
        relay, up = FakeRelay(), FakeUplink()
        d = DoorController(s, MAIN, store, relay, up, spawn=lambda fn: fn(),
                           qr_api=QRValidator(s, session=sess))
        return d, relay, up, sess

    def test_local_first_cached_token_skips_api(self):
        d, relay, up, sess = self.door([])
        self.assertEqual(d.qr("cached"), "opened")
        self.assertEqual((sess.calls, up.of("door_event")[0]["source"]), ([], "qr"))

    def test_local_first_unknown_token_asks_api(self):
        d, relay, up, sess = self.door(["allow", "deny", "down"])
        self.assertEqual(d.qr("new-ticket"), "opened")
        self.assertEqual(up.of("door_event")[0]["source"], "qr_api")
        self.assertEqual(d.qr("bad"), "denied")
        self.assertEqual(d.qr("whatever"), "denied")
        self.assertEqual([e.get("reason") for e in up.of("door_event")[1:]],
                         ["api_denied", "unknown"])
        self.assertEqual(relay.fired, [1])

    def test_api_first_backend_deny_overrides_cache(self):
        d, relay, up, _ = self.door(["deny"], order="api_first")
        self.assertEqual(d.qr("cached"), "denied", "revoked on the backend")
        self.assertEqual(relay.fired, [])

    def test_api_first_falls_back_to_cache_when_down(self):
        d, relay, up, _ = self.door(["down", "down"], order="api_first")
        self.assertEqual(d.qr("cached"), "opened", "offline: cache still opens")
        self.assertEqual(d.qr("unknown"), "denied")

    def test_api_first_allow(self):
        d, _, up, _ = self.door(["allow"], order="api_first")
        self.assertEqual(d.qr("fresh"), "opened")
        self.assertEqual(up.of("door_event")[0]["source"], "qr_api")

    def test_pi_made_codes_never_hit_api(self):
        for order in ("local_first", "api_first"):
            d, _, up, sess = self.door([], order=order)
            self.assertEqual(d.qr("fn-pi-made-code-0001"), "opened")
            self.assertEqual(sess.calls, [])

    def test_config_validation(self):
        with self.assertRaises(ConfigError):
            qr_settings(env={"QR_API_ORDER": "sometimes"})
        with self.assertRaises(ConfigError):
            qr_settings(env={"QR_API_EXTRA": "not json"})
        with self.assertRaises(ConfigError):
            qr_settings(env={"QR_API_METHOD": "PUT"})
        self.assertEqual(settings().qr_api_url, "", "off by default")


class DummyApiTests(unittest.TestCase):
    def test_dummy_api_end_to_end(self):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "dev"))
        from dummy_qr_api import create_app as dummy_app
        from qr_api import QRValidator
        client = dummy_app().test_client()

        class Bridge:   # route QRValidator's requests into the Flask test client
            def post(self, url, json=None, headers=None, timeout=None):
                r = client.post("/qr/validate", json=json)
                return FakeResp(r.status_code, r.get_json())

        s = qr_settings(env={"QR_API_URL": "http://127.0.0.1:8090/qr/validate"})
        v = QRValidator(s, session=Bridge())
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(v.check("DUMMY-ALLOW-0001", "male"), "allow")
            self.assertEqual(v.check("DUMMY-DENY-0001", "male"), "deny")


class DoctorTests(unittest.TestCase):
    def run_config_check(self, text):
        import contextlib
        import io

        import doctor
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, ".env")
            with open(path, "w") as f:
                f.write(text)
            os.chmod(path, 0o640)
            old = dict(os.environ)
            r = doctor.Report()
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    s = doctor.check_config(r, path)
            finally:
                os.environ.clear()
                os.environ.update(old)
        return r, s, out.getvalue()

    def test_good_env(self):
        text = "".join(f"{k}={v}\n" for k, v in ENV.items())
        r, s, out = self.run_config_check(text)
        self.assertEqual((r.failed, s.facility_id), (0, "facility-001"))
        self.assertIn("door main", out)

    def test_change_me_and_invalid_reported(self):
        text = "".join(f"{k}={v}\n" for k, v in ENV.items()).replace("admin-secret", "CHANGE_ME")
        r, s, out = self.run_config_check(text)
        self.assertIsNone(s)
        self.assertIn("ADMIN_KEY", out)
        self.assertGreaterEqual(r.failed, 2)

    def test_missing_env(self):
        import contextlib
        import io

        import doctor
        r = doctor.Report()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(doctor.check_config(r, "/nonexistent/.env"))
        self.assertEqual(r.failed, 1)


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
