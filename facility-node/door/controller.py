"""Door authority: decides who may open, fires the relay, classifies openings.

One DoorController per physical door (section). Each has its own relay
channel, exit input and reed state; the store and uplink are shared.

Unlock decisions use only the local store, so the door opens offline. Every
event is queued via the uplink; nothing here blocks on the network.

Door-event types: entry | attendant | exit | denied | door_closed | relay_failed
Alert subtypes:   forced_open | propped_open | propped_resolved
"""

import logging
import threading
import time
import uuid

from door.relay import RelayError
from uplink import iso

log = logging.getLogger("door")


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
        self._recent_scans = {}     # token -> (ts, result, why): scanner repeats, not new scans

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
            self.relay.fire(self.door.relay, hold=self.door.unlock_seconds)
            return True
        except RelayError as e:
            log.error("[%s] relay failed: %s", self.name, e)
            return False

    # ---- unlock paths -----------------------------------------------------

    def qr(self, token):
        """QR scanner. Returns 'opened' | 'denied' | 'relay_error'.

        Sources in the access log: qr (cached backend token), qr_api (approved
        online), qr_local (Pi-made code from door/qr_tool.py).

        With online validation, a scanner repeating the same code within
        QR_DEDUP_SECONDS is the same scan: no second API call (no second
        charge) and no second event; an open door just stays open longer.
        """
        return self.qr_detail(token)[0]

    def qr_detail(self, token):
        """-> (result, why). why says what the scanner should tell the person:
        None (opened), "empty", "unknown", "offline", "relay_error", or the
        backend's refusal reason (e.g. "insufficient_balance", else "api_denied")."""
        if not token:
            return self._deny_qr("empty"), "empty"
        if self.qr_api is not None and self.s.qr_dedup_seconds > 0:
            repeat = self._repeat_scan(token)
            if repeat is not None:
                return repeat
        result, why = self._qr(token)
        if self.qr_api is not None:
            with self._lock:
                self._recent_scans[token] = (self.clock(), result, why)
        return result, why

    def _repeat_scan(self, token):
        now = self.clock()
        with self._lock:
            for t, (ts, *_) in list(self._recent_scans.items()):
                if now - ts > self.s.qr_dedup_seconds:
                    del self._recent_scans[t]
            seen = self._recent_scans.get(token)
        if seen is None:
            return None
        if seen[1] == "opened":
            log.info("[%s] same QR again within %gs: keeping the door open, no new charge",
                     self.name, self.s.qr_dedup_seconds)
            return ("opened", None) if self._fire() else ("relay_error", "relay_error")
        return seen[1], seen[2]

    def _qr(self, token):
        """-> (result, why), see qr_detail."""
        kind = self.store.token_kind(token, self.clock())
        if kind == "local":
            # The Pi issued it; the backend doesn't know it
            return self._opened(self._open("entry", "qr_local"))
        if self.qr_api is None:
            if kind:
                return self._opened(self._open("entry", "qr"))
            return self._deny_qr("unknown"), "unknown"

        order = self.s.qr_api_order
        if order == "local_first" and kind == "backend":
            return self._opened(self._open("entry", "qr"))
        scan_id = str(uuid.uuid4())
        verdict, why = self.qr_api.check_detail(token, self.name, scan_id)
        if verdict == "allow":
            return self._opened(self._open("entry", "qr_api", scan_id=scan_id))
        if verdict == "deny":
            return (self._deny_qr("api_denied", scan_id=scan_id, api_reason=why),
                    why or "api_denied")
        # unavailable
        if order == "api_only":
            # Paid entry: no internet = no check of the balance = no entry
            return self._deny_qr("offline", scan_id=scan_id), "offline"
        if order == "api_first" and kind:
            return self._opened(self._open("entry", "qr"))
        return self._deny_qr("unknown"), "unknown"

    @staticmethod
    def _opened(result):
        return result, (None if result == "opened" else result)

    def _deny_qr(self, reason, **extra):
        extra = {k: v for k, v in extra.items() if v is not None}
        log.info("[%s] QR denied (%s%s)", self.name, reason,
                 f": {extra['api_reason']}" if "api_reason" in extra else "")
        self._door_event("denied", source="qr", reason=reason, **extra)
        return "denied"

    def facility_open(self, auth_code, use=None, purpose=None, amount=None, attendant=None):
        """Facility app (attendant's button), authenticated by the per-facility Auth Code.

        use = "customer" (a customer paid cash at the counter: logged as a cash
        sale, amount optional) or "personal" (the attendant's own use, with a
        purpose such as cleaning or bath). None = an older app that sends neither.
        Never needs the internet: the backend gets the event from the queue.
        """
        if not self.store.auth_code_valid(auth_code):
            log.info("[%s] Facility app denied", self.name)
            self._door_event("denied", source="facility_app")
            return "denied"
        details = {k: v for k, v in (
            ("use", use), ("purpose", purpose),
            ("payment", "cash" if use == "customer" else None),
            ("amount", amount), ("attendant", attendant)) if v is not None}
        if use:
            log.info("[%s] attendant open: %s%s", self.name, use,
                     f" ({purpose})" if purpose else "")
        return self._open("attendant", "facility_app", **details)

    def _open(self, kind, source, scan_id=None, **details):
        extra = {"scan_id": scan_id} if scan_id else {}
        extra.update(details)
        if not self._fire():
            if scan_id or details.get("payment"):
                # Paid (online charge, or cash taken at the counter) but nobody
                # got in: tell the backend, so it can refund / void the sale
                self._door_event("relay_failed", source=source, **extra)
            return "relay_error"
        with self._lock:
            self._last_trigger = (kind, self.clock())
        self._door_event(kind, source=source, **extra)
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
            legit = at_boot or (trig is not None and now - trig[1] <= self.classify_window)
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
    def classify_window(self):
        """How long after a trigger an opening still counts as legit: at least
        the unlock time, so a long UNLOCK_SECONDS never looks like forced_open."""
        return max(self.s.classify_window, self.door.unlock_seconds + 5)

    @property
    def is_open(self):
        return self._opened_at is not None
