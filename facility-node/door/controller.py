"""Door authority: decides who may open, fires the relay, classifies openings.

One DoorController per physical door (section). Each has its own relay
channel, exit input and reed state; the store and uplink are shared.

Unlock decisions use only the local store, so the door opens offline. Every
event is queued via the uplink; nothing here blocks on the network.

Door-event types: entry | attendant | exit | denied | door_closed | relay_failed
Alert subtypes:   forced_open | propped_open | propped_resolved

Events and alerts name the door by the backend's device id (DOOR_<NAME>_ID,
e.g. BHU-001; the Pi's door name until it is set); paid entries also carry the
door's price from the admin panel (door/info.py).
"""

import hmac
import logging
import threading
import time
import uuid

from door.relay import RelayError
from uplink import iso

log = logging.getLogger("door")


class DoorController:
    def __init__(self, settings, door, store, relay, uplink, snapshot=None,
                 clock=time.time, spawn=None, qr_api=None, info=None):
        self.s = settings
        self.door = door            # config.DoorSpec
        self.name = door.name
        self.store = store
        self.relay = relay
        self.uplink = uplink
        self.snapshot = snapshot or (lambda: None)
        self.qr_api = qr_api        # qr_api.QRValidator or None (cache only)
        self.info = info            # door.info.DoorInfo or None (no names / prices)
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

    def details(self):
        """{"name", "price"} from the admin panel; both None until it sends them."""
        return self.info.get(self.name) if self.info is not None else {"name": None, "price": None}

    @property
    def door_key(self):
        """How the backend knows this door: its device id, else the Pi's name."""
        return self.door.door_id or self.name

    def _door_event(self, type_, **extra):
        payload = {"facility": self.s.facility_id, "door": self.door_key,
                   "type": type_, "ts": iso(self.clock())}
        payload.update(extra)
        self.uplink.enqueue("door_event", payload)

    def _alert(self, subtype, with_snapshot=False, **extra):
        ts = self.clock()

        def send():
            payload = {"facility": self.s.facility_id, "door": self.door_key,
                       "type": "anomaly", "subtype": subtype, "ts": iso(ts)}
            payload.update(extra)
            if with_snapshot:
                shot = self.snapshot()   # {"snapshot", "camera"} from the door's camera
                if shot:
                    payload.update(shot)
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
        None (opened), "empty", "unknown", "offline", "relay_error", "setup_error"
        (the backend refused the Pi's auth code or door id), or the
        backend's refusal reason (e.g. "insufficient_balance", else "api_denied")."""
        return self.qr_scan(token)[:2]

    def qr_scan(self, token):
        """-> (result, why, price): price is what the backend just charged for
        this door (admin panel), for the scanner screen; None = no charge / unknown."""
        if not token:
            return self._deny_qr("empty"), "empty", None
        if self.qr_api is not None and self.s.qr_dedup_seconds > 0:
            repeat = self._repeat_scan(token)
            if repeat is not None:
                return (*repeat, None)       # the same scan: not charged again
        result, why, price = self._qr(token)
        if self.qr_api is not None:
            with self._lock:
                self._recent_scans[token] = (self.clock(), result, why)
        return result, why, price

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
        """-> (result, why, price), see qr_scan."""
        kind = self.store.token_kind(token, self.clock())
        if kind == "local":
            # The Pi issued it; the backend doesn't know it
            return self._opened(self._open("entry"))
        if self.qr_api is None:
            if kind:
                return self._opened(self._open("entry"))
            return self._deny_qr("unknown"), "unknown", None

        order = self.s.qr_api_order
        if order == "local_first" and kind == "backend":
            return self._opened(self._open("entry"))
        scan_id = str(uuid.uuid4())
        verdict, why = self.qr_api.check_detail(token, self.door_key, scan_id)
        if verdict == "allow":
            price = self.details()["price"]
            extra = {"price": price} if price is not None else {}
            return self._opened(self._open("entry", scan_id=scan_id, **extra), price)
        if verdict == "deny":
            return (self._deny_qr("api_denied", scan_id=scan_id, api_reason=why),
                    why or "api_denied", None)
        # unavailable, or fault: the backend refused the Pi's setup (auth code, door id)
        if order == "api_only":
            # Paid entry: no check of the balance = no entry
            if verdict == "fault":
                return (self._deny_qr("setup_error", scan_id=scan_id, api_reason=why),
                        "setup_error", None)
            return self._deny_qr("offline", scan_id=scan_id), "offline", None
        if order == "api_first" and kind:
            return self._opened(self._open("entry"))
        return self._deny_qr("unknown"), "unknown", None

    @staticmethod
    def _opened(result, price=None):
        opened = result == "opened"
        return result, (None if opened else result), (price if opened else None)

    def _deny_qr(self, reason, **extra):
        extra = {k: v for k, v in extra.items() if v is not None}
        log.info("[%s] QR denied (%s%s)", self.name, reason,
                 f": {extra['api_reason']}" if "api_reason" in extra else "")
        self._door_event("denied", reason=reason, **extra)
        return "denied"

    def facility_open(self, auth_code, use=None, purpose=None, amount=None, attendant=None,
                      attendant_id=None):
        """Facility app (attendant's button), authenticated by the per-facility Auth Code.

        use = "customer" (a customer paid cash at the counter: logged as a cash
        sale, amount optional) or "personal" (the attendant's own use, with a
        purpose such as cleaning or bath). None = an older app that sends neither.
        attendant_id is the attendant's backend user id, so the backend files
        the access under that user rather than matching the name.
        Never needs the internet: the backend gets the event from the queue.
        """
        if not self.auth_code_ok(auth_code):
            log.info("[%s] Facility app denied", self.name)
            self._door_event("denied")
            return "denied"
        details = {k: v for k, v in (
            ("use", use), ("purpose", purpose),
            ("payment", "cash" if use == "customer" else None),
            ("amount", amount), ("attendant", attendant),
            ("attendant_id", attendant_id)) if v is not None}
        if use == "customer":
            details.update(self._cash_price(amount))
        if use:
            log.info("[%s] attendant open: %s%s", self.name, use,
                     f" ({purpose})" if purpose else "")
        return self._open("attendant", **details)

    def _cash_price(self, amount):
        """A cash sale against the door's admin-panel price: no amount = the
        price; a different amount still opens (a discount, change) but is
        flagged price_mismatch for the office. No price known = as typed."""
        price = self.details()["price"]
        if price is None:
            return {}
        if amount is None:
            return {"amount": price, "price": price}
        if abs(amount - price) > 0.005:
            log.info("[%s] cash amount %g differs from the price %g", self.name, amount, price)
            return {"price": price, "price_mismatch": True}
        return {"price": price}

    def auth_code_ok(self, presented):
        """The backend's Auth Code (token pull) once the Pi has one; until then
        DEVICE_AUTH_CODE from .env, the same per-facility code, so the Facility
        app works before the backend's settings pull exists."""
        if self.store.get_cred("auth_code_sha256") is not None:
            return self.store.auth_code_valid(presented)
        fallback = self.s.device_auth_code
        if not (fallback and isinstance(presented, str) and presented):
            return False
        return hmac.compare_digest(presented.encode(), fallback.encode())

    def _open(self, kind, scan_id=None, **details):
        extra = {"scan_id": scan_id} if scan_id else {}
        extra.update(details)
        if not self._fire():
            if scan_id or details.get("payment"):
                # Paid (online charge, or cash taken at the counter) but nobody
                # got in: tell the backend, so it can refund / void the sale
                self._door_event("relay_failed", **extra)
            return "relay_error"
        with self._lock:
            self._last_trigger = (kind, self.clock())
        self._door_event(kind, **extra)
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
            self._door_event("exit")

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
        elif at_boot:
            log.info("[%s] door open at start", self.name)
        else:
            log.info("[%s] door opened (%s %.0f s before)", self.name, trig[0], now - trig[1])

    def door_closed(self):
        now = self.clock()
        with self._lock:
            if self._opened_at is None:
                return
            duration = int(now - self._opened_at)
            propped = self._propped_alerts > 0
            self._opened_at = None
            self._propped_alerts = 0
        log.info("[%s] door closed after %d s", self.name, duration)
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
        log.warning("[%s] door open %d s: propped_open%s", self.name, elapsed,
                    " (repeat)" if repeat else "")
        # A picture every time: shows what happens while the door stays open
        self._alert("propped_open", with_snapshot=True,
                    duration_s=int(elapsed), repeat=repeat)

    @property
    def classify_window(self):
        """How long after a trigger an opening still counts as legit: at least
        the unlock time, so a long UNLOCK_SECONDS never looks like forced_open."""
        return max(self.s.classify_window, self.door.unlock_seconds + 5)

    @property
    def is_open(self):
        return self._opened_at is not None
