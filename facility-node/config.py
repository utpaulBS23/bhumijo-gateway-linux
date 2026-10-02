"""Settings for the facility node, read from environment variables.

Production loads /opt/facility-node/.env via systemd EnvironmentFile, so one
code image serves every site. Secrets have no defaults.
"""

import os
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Settings:
    facility_id: str

    # KC868-A4S
    relay_url: str
    relay_pwd: str
    door_relay: int            # 1-based relay channel wired to the timer module
    exit_input_index: int      # 0 = Input01
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

    # Local store
    db_path: str
    max_queue: int

    # Reed switch (MC-38)
    reed: bool
    reed_gpio: int
    reed_active_high: bool
    propped_threshold: int
    propped_repeat: int
    classify_window: float     # seconds a trigger "owns" a following door-open

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
            door_relay=_int("DOOR_RELAY", 1),
            exit_input_index=_int("EXIT_INPUT_INDEX", 0),
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
            db_path=_env("DB_PATH", "/var/lib/facility/facility.db"),
            max_queue=_int("MAX_QUEUE", 200000),
            reed=_bool("REED", False),
            reed_gpio=_int("REED_GPIO", 27),
            reed_active_high=_bool("REED_ACTIVE_HIGH", False),
            propped_threshold=_int("PROPPED_THRESHOLD", 300),
            propped_repeat=_int("PROPPED_REPEAT", 180),
            classify_window=_float("CLASSIFY_WINDOW", 15),
            sensor_interval=_int("SENSOR_INTERVAL", 60),
            pull_interval=_int("PULL_INTERVAL", 300),
            flush_interval=_int("FLUSH_INTERVAL", 15),
            cam_probe_interval=_int("CAM_PROBE_INTERVAL", 30),
            cam_frame_interval=_int("CAM_FRAME_INTERVAL", 300),
            host=_env("HOST", "0.0.0.0"),
            port=_int("PORT", 5454),
            door_test_endpoints=_bool("DOOR_TEST_ENDPOINTS", False),
        )
