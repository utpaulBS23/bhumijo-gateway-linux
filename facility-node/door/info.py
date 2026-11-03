"""Each door's name and price, set in the admin panel.

The settings pull carries {"doors": {"<backend door id>": {"name", "price"}}};
uplink.pull_tokens stores it raw (so it survives a reboot while offline) and
this reads it back. A door is matched by its DOOR_<NAME>_ID. Nothing here is
in the unlock path: a door without a name or price opens just the same.
"""

import json
import logging
import math

log = logging.getLogger("door")

# Store key for the admin panel's door names and prices (written by uplink.pull_tokens)
INFO_KEY = "door_info"


def _price(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


class DoorInfo:
    def __init__(self, settings, store):
        self.ids = {d.door_id: d.name for d in settings.doors if d.door_id}
        self.store = store
        self._seen = None         # last raw value, so problems are logged once

    def all(self):
        """{door name: {"name": str | None, "price": number | None}} for linked doors."""
        raw = self.store.get_cred(INFO_KEY) if self.store is not None else None
        out = {}
        try:
            data = json.loads(raw) if raw else {}
            if not isinstance(data, dict):
                raise ValueError("doors is not an object")
        except ValueError as e:
            self._log_once(raw, f"admin panel door names/prices ignored: {e}")
            return out
        unknown = []
        for door_id, info in data.items():
            name = self.ids.get(str(door_id))
            if name is None:
                unknown.append(str(door_id))
                continue
            info = info if isinstance(info, dict) else {}
            label = info.get("name")
            out[name] = {"name": label.strip()[:64] if isinstance(label, str) and label.strip()
                         else None, "price": _price(info.get("price"))}
        if unknown:
            self._log_once(raw, f"door ids from the admin panel not on this Pi "
                                f"(DOOR_<NAME>_ID): {', '.join(unknown)}")
        else:
            self._seen = raw
        return out

    def get(self, door):
        return self.all().get(door, {"name": None, "price": None})

    def _log_once(self, raw, message):
        if raw != self._seen:
            log.warning(message)
        self._seen = raw
