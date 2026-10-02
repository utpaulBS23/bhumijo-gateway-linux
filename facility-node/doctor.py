#!/usr/bin/env python3
"""Check a facility node installation end to end and say what's wrong.

    facility doctor                 (or: sudo -u facility ./venv/bin/python doctor.py)

Checks .env, the database folder, the relay board (password + exit inputs),
the camera, the admin backend, the QR API, the I2C sensors, and the running
service. Read-only: it never fires a relay or changes anything.
Exit code 0 = all required checks passed.
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys

OK, WARN, FAIL = "✓", "!", "✗"


class Report:
    def __init__(self):
        self.failed = 0
        self.warned = 0

    def line(self, mark, what, detail=""):
        if mark == FAIL:
            self.failed += 1
        elif mark == WARN:
            self.warned += 1
        print(f"  {mark} {what}" + (f" - {detail}" if detail else ""))

    def section(self, title):
        print(f"\n{title}")


def tcp_open(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_config(r, env_path):
    from config import ConfigError, Settings, load_env_file
    r.section("Configuration")
    if not os.path.exists(env_path):
        r.line(FAIL, f"{env_path} missing", "run deploy/install.sh")
        return None
    try:
        env = load_env_file(env_path)
    except PermissionError:
        r.line(FAIL, f"cannot read {env_path}", "run as the facility user: facility doctor")
        return None
    left = [k for k, v in env.items() if v == "CHANGE_ME"]
    if left:
        r.line(FAIL, "values still CHANGE_ME", ", ".join(left))
    os.environ.update(env)
    try:
        s = Settings.from_env()
    except (ConfigError, ValueError) as e:
        r.line(FAIL, ".env invalid", str(e))
        return None
    r.line(OK, f"facility {s.facility_id}")
    for d in s.doors:
        r.line(OK, f"door {d.name}", f"relay {d.relay}, exit input {d.exit_input}, "
               f"reed GPIO {d.reed_gpio}, scanners {', '.join(d.scanner_ips) or 'any'}")
    if s.door_test_endpoints:
        r.line(WARN, "DOOR_TEST_ENDPOINTS=1", "turn off before handover")
    if any(h in s.qr_api_url for h in ("127.0.0.1", "localhost")):
        r.line(WARN, "QR_API_URL points at this machine", "dummy API? not for production")
    if (os.stat(env_path).st_mode & 0o007) != 0:
        r.line(WARN, ".env readable by everyone", "sudo chmod 640 " + env_path)
    return s


def check_storage(r, s):
    r.section("Storage")
    folder = os.path.dirname(s.db_path)
    if os.access(folder, os.W_OK):
        r.line(OK, f"{folder} writable")
    else:
        r.line(FAIL, f"{folder} not writable", "run as the facility user")
    free_mb = shutil.disk_usage(folder if os.path.isdir(folder) else "/").free // 2**20
    r.line(OK if free_mb > 500 else WARN, f"{free_mb} MB free on SD card")
    if os.path.exists(s.db_path):
        from store import Store
        st = Store(s.db_path)
        r.line(OK, f"database: {st.token_count()} tokens cached, {st.queue_size()} queued, "
               f"{len(st.list_local_tokens())} Pi-made codes")
        if not st.get_cred("auth_code_sha256"):
            r.line(WARN, "no Facility-app Auth Code cached yet", "backend pull must send authCode")
        st.close()
    else:
        r.line(WARN, "database not created yet", "start the service once")


def check_relay(r, s):
    from relay import Relay, RelayAuthError, RelayError
    r.section(f"Relay board ({s.relay_url})")
    relay = Relay(s.relay_url, s.relay_pwd, timeout=3)
    try:
        inputs = relay.read_inputs()
    except RelayAuthError:
        r.line(FAIL, "wrong relay password", "RELAY_PWD must equal POST_PASSWORD in board_secrets.py")
        return
    except RelayError as e:
        r.line(FAIL, "not reachable", str(e))
        return
    r.line(OK, "reachable, password accepted")
    if inputs is None:
        r.line(WARN, "firmware doesn't report inputs", "flash firmware/kc868/main.py; exit tracking off")
    else:
        held = [f"Input0{i + 1}" for i, v in enumerate(inputs) if v]
        r.line(OK, "inputs reported", "pressed now: " + (", ".join(held) or "none"))


def check_camera(r, s):
    r.section("Camera")
    if not s.cam_host:
        r.line(WARN, "CAM_HOST not set", "no camera health or snapshots")
        return
    if tcp_open(s.cam_host, s.cam_port):
        r.line(OK, f"{s.cam_host}:{s.cam_port} reachable")
    else:
        r.line(FAIL, f"{s.cam_host}:{s.cam_port} not reachable")
    if shutil.which("ffmpeg"):
        r.line(OK, "ffmpeg installed")
    else:
        r.line(FAIL, "ffmpeg missing", "re-run deploy/install.sh")
    if not s.cam_rtsp:
        r.line(WARN, "CAM_RTSP not set", "no frozen-feed check or snapshots")


def check_backend(r, s):
    import requests
    from uplink import Uplink
    r.section(f"Admin backend ({s.admin_url})")
    up = Uplink(s, store=None)
    try:
        resp = requests.get(up._url(s.tokens_path), params={"facility": s.facility_id},
                            headers=up.auth_headers(), timeout=8)
    except requests.RequestException as e:
        r.line(WARN, "not reachable", f"{type(e).__name__}; doors still open from cache")
        return
    if resp.status_code in (401, 403):
        r.line(FAIL, f"token pull HTTP {resp.status_code}", "check ADMIN_KEY / ADMIN_AUTH_HEADER")
    elif resp.status_code >= 400:
        r.line(FAIL, f"token pull HTTP {resp.status_code}", f"check TOKENS_PATH={s.tokens_path}")
    else:
        r.line(OK, f"token pull HTTP {resp.status_code}")

    if s.qr_api_url:
        from qr_api import QRValidator
        v = QRValidator(s, auth_headers=up.auth_headers() if s.qr_api_auth else {})
        verdict = v.check("DOCTOR-PROBE-NOT-A-REAL-CODE", None)
        if verdict == "unavailable":
            r.line(FAIL, f"QR API {s.qr_api_url}", v.last_error)
        else:
            r.line(OK, f"QR API answers ({verdict} for a fake code)")


def check_i2c(r, s):
    r.section("I2C sensors")
    try:
        from smbus2 import SMBus
    except ImportError:
        r.line(FAIL, "smbus2 not installed", "re-run deploy/install.sh")
        return
    wanted = {0x38: "AHT20 (temp/humidity)", 0x53: "ENS160 (air quality)"}
    if s.mq:
        wanted[s.ads1115_addr] = "ADS1115 (MQ gas sensors)"
    try:
        bus = SMBus(1)
    except (OSError, PermissionError) as e:
        r.line(FAIL, "I2C bus 1 not available", f"{e}; enable I2C (raspi-config) and reboot")
        return
    for addr, name in wanted.items():
        try:
            bus.read_byte(addr)
            r.line(OK, f"0x{addr:02x} {name}")
        except OSError:
            r.line(WARN if addr != s.ads1115_addr else FAIL, f"0x{addr:02x} {name} not found",
                   "check wiring")
    bus.close()
    if s.mq and not (s.mq135_r0 or s.mq136_r0):
        r.line(WARN, "MQ sensors not calibrated", "facility mq calibrate")


def check_service(r, s):
    import requests
    r.section("Service")
    try:
        state = subprocess.run(["systemctl", "is-active", "facility-node"],
                               capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        state = "unknown"
    r.line(OK if state == "active" else FAIL, f"facility-node {state}",
           "" if state == "active" else "sudo systemctl start facility-node; facility logs")
    try:
        h = requests.get(f"http://127.0.0.1:{s.port}/health", timeout=3).json()
    except (requests.RequestException, ValueError):
        if state == "active":
            r.line(FAIL, f"/health on port {s.port} not answering")
        return
    r.line(OK, f"/health ok: queue {h.get('queue')}, tokens {h.get('tokens')}")
    if h.get("queue", 0) > 500:
        r.line(WARN, f"{h['queue']} events waiting", "backend unreachable for a while?")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=os.environ.get("FACILITY_ENV", "/opt/facility-node/.env"))
    p.add_argument("--offline", action="store_true", help="skip network checks")
    args = p.parse_args(argv)

    print("Facility node doctor")
    r = Report()
    s = check_config(r, args.env)
    if s is not None:
        check_storage(r, s)
        if not args.offline:
            check_relay(r, s)
            check_camera(r, s)
            check_backend(r, s)
        check_i2c(r, s)
        check_service(r, s)

    print(f"\n{r.failed} problem(s), {r.warned} warning(s)")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
