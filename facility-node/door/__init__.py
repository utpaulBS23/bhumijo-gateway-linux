"""Door lock / unlock: who may open, the relay, exit buttons, reed switches.

    controller.py  per-door unlock decisions, classification, forced/propped alerts
    relay.py       KC868-A4S relay board HTTP client
    inputs.py      reed switches (gpiozero), exit-input poller, door ticker
    qr_api.py      optional online QR validation
    qr_tool.py     Pi-made unlock QR codes (CLI: facility qr)

Switched by ENABLE_DOORS. Unlock decisions use only the local cache, so the
doors open offline.
"""

from door.controller import DoorController

__all__ = ["DoorController"]
