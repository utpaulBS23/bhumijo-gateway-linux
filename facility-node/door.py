"""Door authority: decides who may open, fires the relay, classifies openings.

One DoorController per physical door (section). Each has its own relay
channel, exit input and reed state; the store and uplink are shared.

Unlock decisions use only the local store, so the door opens offline. Every
event is queued via the uplink; nothing here blocks on the network.

Door-event types: entry | attendant | exit | denied | door_closed
Alert subtypes:   forced_open | propped_open | propped_resolved
"""

import logging
import threading
import time
from datetime import datetime, timezone

from relay import RelayError

log = logging.getLogger("door")


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class DoorController:
    def __init__(self, settings, door, store, relay, uplink, snapshot=None,
                 clock=time.time, spawn=None, qr_api=None):
        self.s = settings
        self.door = door            # config.DoorSpec
        self.name = door.name
        self.store = store
        self.relay = relay
        self.uplink = uplink
        self.snapshot = snapshot or (lambda: None)
        self.qr_api = qr_api        # qr_api.QRValidator or None (cache only)
        self.clock = clock
        # Snapshots can take seconds; run alert work off the caller's thread
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._lock = threading.Lock()
        self._last_trigger = None   # (kind, ts) of the latest legit open
        self._opened_at = None      # ts door physically opened (reed), else None
        self._propped_alerts = 0    # propped_open alerts sent for current opening
        self._exit_held = False

    # ---- events -----------------------------------------------------------

    def _door_event(self, type_, source=None, **extra):
        payload = {"facility": self.s.facility_id, "section": self.name,
                   "type": type_, "ts": iso(self.clock())}
        if source:
            payload["source"] = source
        payload.update(extra)
        self.uplink.enqueue("door_event", payload)

    def _alert(self, subtype, with_snapshot=False, **extra):
        ts = self.clock()

        def send():
            payload = {"facility": self.s.facility_id, "section": self.name,
                       "type": "anomaly", "subtype": subtype, "ts": iso(ts)}
            payload.update(extra)
            if with_snapshot:
                ref = self.snapshot()
                if ref:
                    payload["snapshot"] = ref
            self.uplink.enqueue("alert", payload)

        self.spawn(send)

    def _fire(self):
        try:
            self.relay.fire(self.door.relay)
            return True
        except RelayError as e:
            log.error("[%s] relay failed: %s", self.name, e)
            return False

    # ---- unlock paths -----------------------------------------------------

    def qr(self, token):
        """QR scanner. Returns 'opened' | 'denied' | 'relay_error'.

        Sources in the access log: qr (cached backend token), qr_api (approved
        online), qr_local (Pi-made code from qr_tool.py).
        """
        if not token:
            return self._deny_qr("empty")
        kind = self.store.token_kind(token, self.clock())
        if kind == "local":
            # The Pi issued it; the backend doesn't know it
            return self._open("entry", "qr_local")
        if self.qr_api is None:
            return self._open("entry", "qr") if kind else self._deny_qr("unknown")

        if self.s.qr_api_order == "local_first":
            if kind == "backend":
                return self._open("entry", "qr")
            verdict = self.qr_api.check(token, self.name)
            if verdict == "allow":
                return self._open("entry", "qr_api")
            return self._deny_qr("api_denied" if verdict == "deny" else "unknown")

        # api_first: the backend's answer wins; cache only if it can't answer
        verdict = self.qr_api.check(token, self.name)
        if verdict == "allow":
            return self._open("entry", "qr_api")
        if verdict == "deny":
            return self._deny_qr("api_denied")
        return self._open("entry", "qr") if kind else self._deny_qr("unknown")

    def _deny_qr(self, reason):
        log.info("[%s] QR denied (%s)", self.name, reason)
        self._door_event("denied", source="qr", reason=reason)
        return "denied"

    def facility_open(self, auth_code):
        """Facility app, authenticated by the per-facility Auth Code."""
        if not self.store.auth_code_valid(auth_code):
            log.info("[%s] Facility app denied", self.name)
            self._door_event("denied", source="facility_app")
            return "denied"
        return self._open("attendant", "facility_app")

    def _open(self, kind, source):
        if not self._fire():
            return "relay_error"
        with self._lock:
            self._last_trigger = (kind, self.clock())
        self._door_event(kind, source=source)
        return "opened"

    # ---- exit button (KC868 input) ----------------------------------------

    def exit_input(self, pressed):
        """Fed by the input poller. Logs one exit per press (rising edge).

        The exit button opens the door in hardware; the Pi only records it.
        """
        with self._lock:
            rising = pressed and not self._exit_held
            self._exit_held = pressed
            if rising:
                self._last_trigger = ("exit", self.clock())
        if rising:
            self._door_event("exit", source="exit_button")

    # ---- reed switch ------------------------------------------------------

    def door_opened(self, at_boot=False):
        """at_boot: door was already open at startup; track it, no forced alert."""
        now = self.clock()
        with self._lock:
            if self._opened_at is not None:
                return
            self._opened_at = now
            self._propped_alerts = 0
            trig = self._last_trigger
            legit = at_boot or (trig is not None and now - trig[1] <= self.s.classify_window)
            if legit:
                self._last_trigger = None   # one trigger covers one opening
        if not legit:
            log.warning("[%s] door opened with no trigger: forced_open", self.name)
            self._alert("forced_open", with_snapshot=True)

    def door_closed(self):
        now = self.clock()
        with self._lock:
            if self._opened_at is None:
                return
            duration = int(now - self._opened_at)
            propped = self._propped_alerts > 0
            self._opened_at = None
            self._propped_alerts = 0
        self._door_event("door_closed", duration_s=duration)
        if propped:
            self._alert("propped_resolved", duration_s=duration)

    def tick(self):
        """Call periodically. Raises propped_open, then repeats until closed."""
        now = self.clock()
        with self._lock:
            if self._opened_at is None:
                return
            elapsed = now - self._opened_at
            due = self.s.propped_threshold + self._propped_alerts * self.s.propped_repeat
            if elapsed < due:
                return
            repeat = self._propped_alerts > 0
            self._propped_alerts += 1
        self._alert("propped_open", with_snapshot=not repeat,
                    duration_s=int(elapsed), repeat=repeat)

    @property
    def is_open(self):
        return self._opened_at is not None
