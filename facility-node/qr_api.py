"""Online QR validation: ask a backend API whether a scanned code may enter.

Everything about the call is configurable in .env (QR_API_*), so it can match
whatever the backend exposes:

    QR_API_URL=/qr/validate              # path under ADMIN_URL, or a full URL
    QR_API_METHOD=POST                   # POST -> JSON body, GET -> query string
    QR_API_TOKEN_FIELD=token             # name of the scanned-code field
    QR_API_FACILITY_FIELD=facility       # empty = don't send
    QR_API_DOOR_FIELD=door               # empty = don't send
    QR_API_EXTRA={"device":"pi-01"}      # extra static fields (JSON)
    QR_API_ALLOW_FIELD=allowed           # dot path in the JSON reply, e.g. data.access_granted
    QR_API_ALLOW_VALUES=true,1,yes,granted,allow,allowed,success
    QR_API_DENY_STATUS=400,403,404,410,422

Result of a check:
    "allow"        2xx and the allow field holds an allow value
    "deny"         2xx with any other value, or a QR_API_DENY_STATUS code
    "unavailable"  timeout, network error, 5xx, 401, or an unreadable reply
                   -> the door falls back to the local token cache
"""

import logging
import time

import requests

log = logging.getLogger("qr_api")


def dig(obj, path):
    """dig({'data': {'ok': 1}}, 'data.ok') -> 1; missing -> None."""
    for part in path.split("."):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def is_allow(value, allow_values):
    if isinstance(value, bool):
        return value and "true" in allow_values
    if value is None:
        return False
    return str(value).strip().lower() in allow_values


class QRValidator:
    def __init__(self, settings, session=None, auth_headers=None):
        self.s = settings
        self.session = session or requests.Session()
        self.auth_headers = auth_headers or {}
        self.last_ok = None
        self.last_error = None

    def _request(self, token, door):
        fields = dict(self.s.qr_api_extra)
        fields[self.s.qr_api_token_field] = token
        if self.s.qr_api_facility_field:
            fields[self.s.qr_api_facility_field] = self.s.facility_id
        if self.s.qr_api_door_field and door:
            fields[self.s.qr_api_door_field] = door
        kw = dict(headers=self.auth_headers, timeout=self.s.qr_api_timeout)
        if self.s.qr_api_method == "GET":
            return self.session.get(self.s.qr_api_url, params=fields, **kw)
        return self.session.post(self.s.qr_api_url, json=fields, **kw)

    def check(self, token, door=None):
        """-> 'allow' | 'deny' | 'unavailable'."""
        try:
            resp = self._request(token, door)
        except requests.Timeout:
            return self._unavailable(f"no reply within {self.s.qr_api_timeout}s")
        except requests.ConnectionError:
            return self._unavailable("cannot connect")
        except requests.RequestException as e:
            return self._unavailable(type(e).__name__)
        code = resp.status_code
        if code in self.s.qr_api_deny_status:
            self.last_ok = time.time()
            return "deny"
        if not 200 <= code < 300:
            return self._unavailable(f"HTTP {code}")
        try:
            body = resp.json()
        except ValueError:
            return self._unavailable("reply is not JSON")
        self.last_ok = time.time()
        value = dig(body, self.s.qr_api_allow_field)
        if value is None:
            log.warning("QR API reply has no %r field: %s", self.s.qr_api_allow_field,
                        str(body)[:200])
        return "allow" if is_allow(value, self.s.qr_api_allow_values) else "deny"

    def _unavailable(self, why):
        self.last_error = f"{time.strftime('%H:%M:%S')} {why}"
        log.warning("QR API unavailable (%s), using local cache", why)
        return "unavailable"

    def state(self):
        return {"order": self.s.qr_api_order, "method": self.s.qr_api_method,
                "last_ok": self.last_ok, "last_error": self.last_error}
