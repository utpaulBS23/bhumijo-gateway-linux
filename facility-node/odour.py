"""Odour alert: raise when air readings stay above a limit, repeat, then clear.

Works like the propped-open alert, on the 1-minute sensor readings:

  any metric >= its limit continuously for ODOUR_HOLD s  -> odour_high (repeat:false)
  still high, every ODOUR_REPEAT s after that             -> odour_high (repeat:true)
  all metrics below their limits for ODOUR_CLEAR_HOLD s   -> odour_resolved

Metrics: tvoc (ppb), aqi (1-5), nh3_ppm, h2s_ppm. A limit left empty in .env
turns that metric off. Missing readings (sensor warming up or failed) neither
start nor clear an alert.
"""

import logging
import threading
import time

from door import iso

log = logging.getLogger("odour")


class OdourMonitor:
    def __init__(self, settings, uplink, clock=time.time):
        self.s = settings
        self.uplink = uplink
        self.clock = clock
        self.limits = {k: v for k, v in (
            ("tvoc", settings.odour_tvoc_limit),
            ("aqi", settings.odour_aqi_limit),
            ("nh3_ppm", settings.odour_nh3_limit),
            ("h2s_ppm", settings.odour_h2s_limit),
        ) if v is not None}
        self._lock = threading.Lock()
        self._high_since = None   # start of the current continuous high spell
        self._low_since = None    # start of the current low spell while alerting
        self._alerts = 0          # odour_high alerts sent in this episode

    @property
    def alerting(self):
        return self._alerts > 0

    def state(self):
        return {"alerting": self.alerting, "limits": self.limits}

    def _send(self, subtype, now, reading, over=None, **extra):
        payload = {"facility": self.s.facility_id, "type": "anomaly", "subtype": subtype,
                   "ts": iso(now),
                   "readings": {k: reading.get(k) for k in self.limits}}
        if over is not None:
            payload["metrics"] = over
        payload.update(extra)
        self.uplink.enqueue("alert", payload)
        log.warning("%s %s", subtype, payload["readings"])

    def update(self, reading):
        if not self.limits:
            return
        now = self.clock()
        known = [k for k in self.limits if reading.get(k) is not None]
        if not known:
            return   # no usable data: hold the current state
        over = [k for k in known if reading[k] >= self.limits[k]]

        with self._lock:
            if over:
                self._low_since = None
                if self._high_since is None:
                    self._high_since = now
                elapsed = now - self._high_since
                due = self.s.odour_hold + max(self._alerts, 0) * self.s.odour_repeat
                if elapsed < due:
                    return
                repeat = self._alerts > 0
                self._alerts += 1
                send = ("odour_high", dict(over=over, duration_s=int(elapsed), repeat=repeat))
            else:
                if not self.alerting:
                    self._high_since = None
                    return
                if self._low_since is None:
                    self._low_since = now
                if now - self._low_since < self.s.odour_clear_hold:
                    return
                duration = int(now - self._high_since)
                self._high_since = self._low_since = None
                self._alerts = 0
                send = ("odour_resolved", dict(duration_s=duration))

        self._send(send[0], now, reading, **send[1])
