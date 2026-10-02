"""KC868-A4S relay board client (firmware/kc868/main.py).

- sw_ctl.cgi?postpwd=..&Relay01=ON  fires a relay; firmware auto-offs after 1s,
  so the Pi only ever sends ON.
- input_ctl.cgi?postpwd=..          returns "Relay01=OFF&...&Input01=ON&..."
"""

import logging

import requests

log = logging.getLogger("relay")


class RelayError(Exception):
    pass


class RelayAuthError(RelayError):
    pass


def parse_states(body):
    """'Relay01=ON&Input01=OFF' -> {'Relay01': True, 'Input01': False}"""
    states = {}
    for part in body.strip().split("&"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        states[key.strip()] = value.strip().upper() == "ON"
    return states


class Relay:
    def __init__(self, base_url, password, session=None, timeout=2.0):
        self.base_url = base_url.rstrip("/")
        self.password = password
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path, params):
        try:
            resp = self.session.get(
                f"{self.base_url}/{path}", params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise RelayError(f"{path}: {e}") from e
        if resp.status_code == 401:
            raise RelayAuthError(f"{path}: wrong relay password")
        if resp.status_code != 200:
            raise RelayError(f"{path}: HTTP {resp.status_code}")
        return resp.text

    def fire(self, channel=1):
        """Pulse relay `channel` (1-4). Raises RelayError on failure."""
        if not 1 <= channel <= 4:
            raise ValueError(f"relay channel {channel} out of range")
        self._get("sw_ctl.cgi", {"postpwd": self.password, f"Relay0{channel}": "ON"})
        log.info("relay %d fired", channel)

    def states(self):
        return parse_states(self._get("input_ctl.cgi", {"postpwd": self.password}))

    def read_inputs(self):
        """[bool x4] for Input01..04, or None if firmware does not report inputs."""
        states = self.states()
        keys = [f"Input0{i}" for i in range(1, 5)]
        if not all(k in states for k in keys):
            return None
        return [states[k] for k in keys]
