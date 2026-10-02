"""Admin backend client: queue-first pushes and the token/credential pull.

Every outbound item gets a unique "id" (UUID) when queued, sent in the body
and as the Idempotency-Key header. A retry after a lost reply resends the
same id, so the backend can drop duplicates.

Every outbound item is written to the SQLite outbox first, then drained
oldest-first. A network error or 5xx stops the drain (backend unreachable,
retry later); a 4xx means the backend rejected that item, which is retried a
few times and then dropped so one bad row cannot block the queue forever.
"""

import logging
import threading
import time
import uuid
from datetime import datetime

import requests

log = logging.getLogger("uplink")

MAX_REJECTS = 5


def parse_expiry(value):
    """expiresAt as unix seconds/ms or ISO 8601 -> unix seconds, None = no expiry."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e12 else float(value)
    text = str(value).strip()
    if text.replace(".", "", 1).isdigit():
        return parse_expiry(float(text))
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class Uplink:
    def __init__(self, settings, store, session=None, timeout=10.0):
        self.s = settings
        self.store = store
        self.session = session or requests.Session()
        self.timeout = timeout
        self.paths = {
            "sensor": settings.sensor_path,
            "door_event": settings.door_event_path,
            "alert": settings.alert_path,
            "snapshot": settings.snapshot_upload_path,
        }
        self._wake = threading.Event()
        self._flush_lock = threading.Lock()
        self.last_pull_ok = None
        self.last_flush_ok = None

    def auth_headers(self):
        return self._headers()

    def _headers(self):
        key = self.s.admin_key
        if self.s.admin_auth_scheme:
            key = f"{self.s.admin_auth_scheme} {key}"
        return {self.s.admin_auth_header: key}

    def _url(self, path):
        return f"{self.s.admin_url}{path}"

    # ---- push -------------------------------------------------------------

    def enqueue(self, kind, payload):
        # Assigned once here, so every retry of this item carries the same id
        payload = {"id": str(uuid.uuid4()), **payload}
        self.store.push(kind, payload)
        self._wake.set()

    @property
    def uploads_snapshots(self):
        return bool(self.s.snapshot_upload_path)

    def queue_snapshot(self, name, path, ts):
        """Queue a JPEG for upload. The alert refers to it by `name`."""
        if self.uploads_snapshots:
            self.enqueue("snapshot", {"facility": self.s.facility_id, "name": name,
                                      "path": path, "ts": ts})

    def _post(self, kind, payload):
        """POST one outbox row. Returns a response, or None if the row is moot."""
        url = self._url(self.paths[kind])
        headers = self._headers()
        if payload.get("id"):
            headers["Idempotency-Key"] = payload["id"]
        if kind != "snapshot":
            return self.session.post(url, json=payload, headers=headers,
                                     timeout=self.timeout)
        # Multipart: field "file" + form fields id/facility/name/ts
        try:
            fh = open(payload["path"], "rb")
        except OSError:
            log.warning("snapshot %s gone from disk, skipping upload", payload["name"])
            return None
        with fh:
            return self.session.post(
                url, files={"file": (payload["name"], fh, "image/jpeg")},
                data={k: payload[k] for k in ("id", "facility", "name", "ts") if k in payload},
                headers=headers, timeout=max(self.timeout, 30))

    def flush(self, batch=50):
        """Drain the outbox. Returns number of items delivered."""
        if not self._flush_lock.acquire(blocking=False):
            return 0
        sent = 0
        try:
            while True:
                rows = self.store.peek(batch)
                if not rows:
                    return sent
                for row_id, kind, payload, attempts in rows:
                    if not self.paths.get(kind):
                        self.store.ack(row_id)   # endpoint since disabled in .env
                        continue
                    try:
                        resp = self._post(kind, payload)
                    except requests.RequestException as e:
                        log.info("backend unreachable, %d queued: %s",
                                 self.store.queue_size(), e)
                        return sent
                    if resp is None:
                        self.store.ack(row_id)
                        continue
                    if resp.status_code < 300:
                        self.store.ack(row_id)
                        sent += 1
                        self.last_flush_ok = time.time()
                    elif resp.status_code >= 500 or resp.status_code in (401, 403, 408, 429):
                        # Server or auth trouble: not this row's fault, retry later
                        log.warning("backend HTTP %d on %s, pausing drain",
                                    resp.status_code, kind)
                        return sent
                    else:
                        self.store.bump_attempts(row_id)
                        if attempts + 1 >= MAX_REJECTS:
                            log.error("dropping %s after %d rejects (HTTP %d): %s",
                                      kind, MAX_REJECTS, resp.status_code, payload)
                            self.store.ack(row_id)
                        else:
                            return sent
        finally:
            self._flush_lock.release()

    # ---- pull -------------------------------------------------------------

    def pull_tokens(self):
        """Refresh token cache + Auth Code. On any failure the cache is kept."""
        try:
            resp = self.session.get(
                self._url(self.s.tokens_path), params={"facility": self.s.facility_id},
                headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
            if body.get("facilityId") not in (None, self.s.facility_id):
                log.error("pull returned facilityId %r, expected %r; ignoring",
                          body.get("facilityId"), self.s.facility_id)
                return False
            tokens = [(t["token"], parse_expiry(t.get("expiresAt")))
                      for t in body.get("tokens", [])]
        except (requests.RequestException, ValueError, KeyError, TypeError) as e:
            log.warning("token pull failed, keeping cache: %s", e)
            return False
        self.store.replace_tokens(tokens)
        # PENDING backend: field carrying the Facility-app Auth Code
        auth_code = body.get("authCode")
        if auth_code:
            self.store.set_auth_code(auth_code)
        self.store.set_cred("last_pull", str(time.time()))
        self.last_pull_ok = time.time()
        log.info("token cache refreshed: %d tokens", len(tokens))
        return True

    # ---- background loops -------------------------------------------------

    def run_flush_loop(self, stop):
        while not stop.is_set():
            self.flush()
            self._wake.wait(self.s.flush_interval)
            self._wake.clear()

    def run_pull_loop(self, stop):
        while not stop.is_set():
            self.pull_tokens()
            stop.wait(self.s.pull_interval)
