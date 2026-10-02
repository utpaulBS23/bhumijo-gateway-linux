"""Local SQLite store: token cache, Facility-app credentials, outbound queue.

Everything the door needs to decide lives here, so unlocking never waits on
the network. All methods are thread-safe.
"""

import hashlib
import hmac
import json
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    token      TEXT PRIMARY KEY,
    expires_at REAL            -- unix seconds; NULL = no expiry
);
-- Codes created on the Pi (qr_tool.py): test / commissioning / staff QRs.
-- Separate table so the backend pull never overwrites them.
CREATE TABLE IF NOT EXISTS local_tokens (
    token      TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    expires_at REAL,           -- unix seconds; NULL = no expiry
    created    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS creds (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind     TEXT NOT NULL,    -- sensor | door_event | alert
    payload  TEXT NOT NULL,
    created  REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


class Store:
    def __init__(self, path, max_queue=200000):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        if path != ":memory:":
            # WAL + synchronous=NORMAL: survives power cuts without a full fsync per write
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self.max_queue = max_queue

    def close(self):
        with self._lock:
            self._db.close()

    # ---- tokens ---------------------------------------------------------

    def replace_tokens(self, tokens):
        """Atomically swap the cache. tokens: iterable of (token, expires_at|None)."""
        with self._lock:
            self._db.execute("BEGIN")
            try:
                self._db.execute("DELETE FROM tokens")
                self._db.executemany(
                    "INSERT OR REPLACE INTO tokens (token, expires_at) VALUES (?, ?)",
                    list(tokens))
                self._db.execute("COMMIT")
            except Exception:
                self._db.execute("ROLLBACK")
                raise

    def token_kind(self, token, now=None):
        """'backend' | 'local' if the token is valid now, else None."""
        if not token:
            return None
        now = time.time() if now is None else now
        with self._lock:
            for kind, table in (("backend", "tokens"), ("local", "local_tokens")):
                row = self._db.execute(
                    f"SELECT expires_at FROM {table} WHERE token = ?", (token,)).fetchone()
                if row is not None and (row[0] is None or row[0] > now):
                    return kind
        return None

    def token_valid(self, token, now=None):
        return self.token_kind(token, now) is not None

    # ---- local tokens (qr_tool.py) --------------------------------------

    def add_local_token(self, token, label, expires_at, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._db.execute(
                "INSERT INTO local_tokens (token, label, expires_at, created) "
                "VALUES (?, ?, ?, ?)", (token, label, expires_at, now))

    def list_local_tokens(self):
        """[(token, label, expires_at, created)] newest first."""
        with self._lock:
            return self._db.execute(
                "SELECT token, label, expires_at, created FROM local_tokens "
                "ORDER BY created DESC").fetchall()

    def revoke_local_tokens(self, token=None, label=None, everything=False, expired_before=None):
        """Delete by token, by label, expired ones, or all. Returns rows removed."""
        with self._lock:
            if everything:
                cur = self._db.execute("DELETE FROM local_tokens")
            elif token:
                cur = self._db.execute("DELETE FROM local_tokens WHERE token = ?", (token,))
            elif label:
                cur = self._db.execute("DELETE FROM local_tokens WHERE label = ?", (label,))
            elif expired_before is not None:
                cur = self._db.execute(
                    "DELETE FROM local_tokens WHERE expires_at IS NOT NULL AND expires_at <= ?",
                    (expired_before,))
            else:
                return 0
            return cur.rowcount

    def token_count(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM tokens").fetchone()[0]

    # ---- creds ----------------------------------------------------------

    def set_cred(self, key, value):
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO creds (key, value) VALUES (?, ?)", (key, value))

    def get_cred(self, key):
        with self._lock:
            row = self._db.execute(
                "SELECT value FROM creds WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_auth_code(self, auth_code):
        """Store only a hash of the Facility-app Auth Code."""
        self.set_cred("auth_code_sha256", _hash(auth_code))

    def auth_code_valid(self, presented):
        stored = self.get_cred("auth_code_sha256")
        if not stored or not presented:
            return False
        return hmac.compare_digest(stored, _hash(presented))

    # ---- outbox ---------------------------------------------------------

    def push(self, kind, payload, now=None):
        now = time.time() if now is None else now
        with self._lock:
            self._db.execute(
                "INSERT INTO outbox (kind, payload, created) VALUES (?, ?, ?)",
                (kind, json.dumps(payload), now))
            self._trim()

    def _trim(self):
        # Over capacity: shed oldest sensor readings first, keep access log + alerts
        count = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        excess = count - self.max_queue
        if excess <= 0:
            return
        self._db.execute(
            "DELETE FROM outbox WHERE id IN "
            "(SELECT id FROM outbox WHERE kind = 'sensor' ORDER BY id LIMIT ?)", (excess,))
        count = self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
        excess = count - self.max_queue
        if excess > 0:
            self._db.execute(
                "DELETE FROM outbox WHERE id IN "
                "(SELECT id FROM outbox ORDER BY id LIMIT ?)", (excess,))

    def peek(self, limit=50):
        """Oldest-first batch: list of (id, kind, payload_dict, attempts)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, kind, payload, attempts FROM outbox ORDER BY id LIMIT ?",
                (limit,)).fetchall()
        return [(r[0], r[1], json.loads(r[2]), r[3]) for r in rows]

    def ack(self, row_id):
        with self._lock:
            self._db.execute("DELETE FROM outbox WHERE id = ?", (row_id,))

    def bump_attempts(self, row_id):
        with self._lock:
            self._db.execute(
                "UPDATE outbox SET attempts = attempts + 1 WHERE id = ?", (row_id,))

    def queue_size(self):
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
