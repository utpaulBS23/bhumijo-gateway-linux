"""Settings for the facility node, read from environment variables.

Production loads /opt/facility-node/.env via systemd EnvironmentFile, so one
code image serves every site. Secrets have no defaults.
"""

import ipaddress
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

PLACEHOLDER = "CHANGE_ME"


class ConfigError(RuntimeError):
    pass


def _env(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _required(name):
    value = _env(name)
    if value is None or value == PLACEHOLDER:
        raise ConfigError(f"{name} is required (set it in .env)")
    return value


def _bool(name, default=False):
    value = _env(name)
    if value is None:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _int(name, default):
    return int(_env(name, default))


def _float(name, default):
    return float(_env(name, default))


def _optional_float(name):
    value = _env(name)
    if value is None or value.lower() == "none":
        return None
    return float(value)


@dataclass(frozen=True)
class DoorSpec:
    """One physical door: its relay channel, exit input, reed pin and scanners."""
    name: str                     # section, e.g. "male" / "female"
    relay: int                    # KC868 channel 1-4 wired to this door's timer
    exit_input: Optional[int]     # KC868 input index 0-3 (0 = Input01), None = no exit tap
    reed_gpio: Optional[int]      # Pi BCM pin for this door's MC-38, None = no reed
    scanner_ips: Tuple[str, ...]  # QR scanners whose scans open this door


def _optional_int(name):
    value = _env(name)
    if value is None or value.lower() == "none":
        return None
    return int(value)


def _ips(name):
    raw = _env(name, "")
    ips = tuple(p.strip() for p in raw.split(",") if p.strip())
    for ip in ips:
        ipaddress.ip_address(ip)   # ValueError on typos
    return ips


def _parse_doors():
    """DOORS=male,female + DOOR_<NAME>_{RELAY,EXIT_INPUT,REED_GPIO,SCANNER_IPS}.

    Without DOORS: a single door named "main" from DOOR_RELAY / EXIT_INPUT_INDEX /
    REED_GPIO / SCANNER_IPS (empty SCANNER_IPS = accept scans from any IP).
    """
    names = _env("DOORS")
    if names is None:
        doors = (DoorSpec("main", _int("DOOR_RELAY", 1), _int("EXIT_INPUT_INDEX", 0),
                          _int("REED_GPIO", 27), _ips("SCANNER_IPS")),)
    else:
        doors = []
        for name in [n.strip().lower() for n in names.split(",") if n.strip()]:
            if not re.fullmatch(r"[a-z0-9_]+", name):
                raise ConfigError(f"bad door name {name!r} in DOORS")
            pre = f"DOOR_{name.upper()}_"
            relay = _env(pre + "RELAY")
            if relay is None:
                raise ConfigError(f"{pre}RELAY is required")
            doors.append(DoorSpec(name, int(relay), _optional_int(pre + "EXIT_INPUT"),
                                  _optional_int(pre + "REED_GPIO"), _ips(pre + "SCANNER_IPS")))
        doors = tuple(doors)
    _validate_doors(doors)
    return doors


def _validate_doors(doors):
    if not doors:
        raise ConfigError("DOORS is empty")

    def unique(label, values):
        values = [v for v in values if v is not None]
        dupes = {v for v in values if values.count(v) > 1}
        if dupes:
            raise ConfigError(f"{label} used by more than one door: {sorted(dupes)}")

    unique("door name", [d.name for d in doors])
    unique("relay channel", [d.relay for d in doors])
    unique("exit input", [d.exit_input for d in doors])
    unique("reed GPIO", [d.reed_gpio for d in doors])
    unique("scanner IP", [ip for d in doors for ip in d.scanner_ips])
    for d in doors:
        if not 1 <= d.relay <= 4:
            raise ConfigError(f"door {d.name}: relay {d.relay} not in 1-4")
        if d.exit_input is not None and not 0 <= d.exit_input <= 3:
            raise ConfigError(f"door {d.name}: exit input {d.exit_input} not in 0-3")
    if len(doors) > 1 and any(not d.scanner_ips for d in doors):
        raise ConfigError("with several doors, every door needs SCANNER_IPS")


@dataclass(frozen=True)
class Settings:
    facility_id: str

    # KC868-A4S
    relay_url: str
    relay_pwd: str
    doors: Tuple[DoorSpec, ...]
    exit_poll_interval: float  # seconds between input_ctl.cgi polls

    # Admin backend
    admin_url: str
    admin_key: str
    # PENDING: confirm header + paths with the backend team.
    admin_auth_header: str     # e.g. "Authorization" or "X-API-Key"
    admin_auth_scheme: str     # e.g. "Bearer"; empty = raw key
    sensor_path: str
    door_event_path: str
    alert_path: str
    tokens_path: str

    # Camera
    cam_host: str
    cam_port: int
    cam_rtsp: str
    snapshot_dir: str
    snapshot_upload_path: str  # PENDING backend: multipart upload endpoint; "" = off
    snapshot_keep_days: int

    # Local store
    db_path: str
    max_queue: int

    # Reed switch (MC-38)
    reed: bool                 # enable reed watching on doors that have a REED_GPIO
    reed_active_high: bool
    propped_threshold: int
    propped_repeat: int
    classify_window: float     # seconds a trigger "owns" a following door-open

    # Odour alert: limits (None = metric off) and timing in seconds
    odour_tvoc_limit: Optional[float]
    odour_aqi_limit: Optional[float]
    odour_nh3_limit: Optional[float]
    odour_h2s_limit: Optional[float]
    odour_hold: int
    odour_repeat: int
    odour_clear_hold: int

    # MQ-135 / MQ-136 through ADS1115
    mq: bool
    ads1115_addr: int
    mq135_channel: Optional[int]
    mq136_channel: Optional[int]
    mq135_r0: Optional[float]
    mq136_r0: Optional[float]
    mq_vc: float
    mq_rl_kohm: float
    mq_divider: float

    # Job intervals (seconds)
    sensor_interval: int
    pull_interval: int
    flush_interval: int
    cam_probe_interval: int
    cam_frame_interval: int

    # Local HTTP
    host: str
    port: int
    door_test_endpoints: bool  # expose POST /door/opened|closed for bench tests

    @classmethod
    def from_env(cls):
        return cls(
            facility_id=_required("FACILITY_ID"),
            relay_url=_required("RELAY_URL").rstrip("/"),
            relay_pwd=_required("RELAY_PWD"),
            doors=_parse_doors(),
            exit_poll_interval=_float("EXIT_POLL_INTERVAL", 0.2),
            admin_url=_required("ADMIN_URL").rstrip("/"),
            admin_key=_required("ADMIN_KEY"),
            admin_auth_header=_env("ADMIN_AUTH_HEADER", "Authorization"),
            admin_auth_scheme=_env("ADMIN_AUTH_SCHEME", "Bearer"),
            sensor_path=_env("SENSOR_PATH", "/facility/sensor"),
            door_event_path=_env("DOOR_EVENT_PATH", "/facility/door-event"),
            alert_path=_env("ALERT_PATH", "/facility/alert"),
            tokens_path=_env("TOKENS_PATH", "/facility/tokens"),
            cam_host=_env("CAM_HOST", ""),
            cam_port=_int("CAM_PORT", 554),
            cam_rtsp=_env("CAM_RTSP", ""),
            snapshot_dir=_env("SNAPSHOT_DIR", "/var/lib/facility/snapshots"),
            snapshot_upload_path=_env("SNAPSHOT_UPLOAD_PATH", ""),
            snapshot_keep_days=_int("SNAPSHOT_KEEP_DAYS", 7),
            db_path=_env("DB_PATH", "/var/lib/facility/facility.db"),
            max_queue=_int("MAX_QUEUE", 200000),
            reed=_bool("REED", False),
            reed_active_high=_bool("REED_ACTIVE_HIGH", False),
            propped_threshold=_int("PROPPED_THRESHOLD", 300),
            propped_repeat=_int("PROPPED_REPEAT", 180),
            classify_window=_float("CLASSIFY_WINDOW", 15),
            odour_tvoc_limit=_optional_float("ODOUR_TVOC_LIMIT"),
            odour_aqi_limit=_optional_float("ODOUR_AQI_LIMIT"),
            odour_nh3_limit=_optional_float("ODOUR_NH3_LIMIT"),
            odour_h2s_limit=_optional_float("ODOUR_H2S_LIMIT"),
            odour_hold=_int("ODOUR_HOLD", 600),
            odour_repeat=_int("ODOUR_REPEAT", 1800),
            odour_clear_hold=_int("ODOUR_CLEAR_HOLD", 300),
            mq=_bool("MQ", False),
            ads1115_addr=int(_env("ADS1115_ADDR", "0x48"), 0),
            mq135_channel=_optional_int("MQ135_CHANNEL") if _env("MQ135_CHANNEL") else 0,
            mq136_channel=_optional_int("MQ136_CHANNEL") if _env("MQ136_CHANNEL") else 1,
            mq135_r0=_optional_float("MQ135_R0"),
            mq136_r0=_optional_float("MQ136_R0"),
            mq_vc=_float("MQ_VC", 5.0),
            mq_rl_kohm=_float("MQ_RL_KOHM", 10.0),
            mq_divider=_float("MQ_DIVIDER", 1.5),
            sensor_interval=_int("SENSOR_INTERVAL", 60),
            pull_interval=_int("PULL_INTERVAL", 300),
            flush_interval=_int("FLUSH_INTERVAL", 15),
            cam_probe_interval=_int("CAM_PROBE_INTERVAL", 30),
            cam_frame_interval=_int("CAM_FRAME_INTERVAL", 300),
            host=_env("HOST", "0.0.0.0"),
            port=_int("PORT", 5454),
            door_test_endpoints=_bool("DOOR_TEST_ENDPOINTS", False),
        )
