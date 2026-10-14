"""Odour alert: raise when air readings stay above a limit, repeat, then clear.

Works like the propped-open alert, on the 1-minute sensor readings:

  any metric >= its limit continuously for ODOUR_HOLD s  -> odour_high (repeat:false)
  still high, every ODOUR_REPEAT s after that             -> odour_high (repeat:true)
  all metrics below their limits for ODOUR_CLEAR_HOLD s   -> odour_resolved

Metrics: tvoc (ppb), tvoc_score (0-100, see tvoc_score()), aqi (1-5), nh3_ppm,
h2s_ppm. A limit left empty in .env turns that metric off. Missing readings
(sensor warming up or failed) neither start nor clear an alert.
"""

import logging
import threading
import time

from door import iso

log = logging.getLogger("odour")

# TVOC as a 0-100 score. Each 20 points is one level of the German UBA TVOC
# guidance, the same scale the ENS160's own AQI uses, so "dangerous" means what
# health guidance says rather than an arbitrary cut. Straight line inside a
# level, capped at 100.
TVOC_SCORE_STEPS = ((0, 0), (220, 20), (660, 40), (2200, 60), (5500, 80), (11000, 100))
TVOC_LEVELS = ((20, "very_good"), (40, "good"), (60, "moderate"), (80, "dangerous"),
               (100, "very_dangerous"))


def tvoc_score(ppb):
    """TVOC ppb -> 0-100 (None stays None)."""
    if ppb is None:
        return None
    for (x0, y0), (x1, y1) in zip(TVOC_SCORE_STEPS, TVOC_SCORE_STEPS[1:]):
        if ppb <= x1:
            return round(y0 + (max(ppb, 0) - x0) * (y1 - y0) / (x1 - x0))
    return 100


def tvoc_level(score):
    """0-20 very_good, 21-40 good, 41-60 moderate, 61-80 dangerous, 81-100 very_dangerous."""
    if score is None:
        return None
    return next(name for top, name in TVOC_LEVELS if score <= top)


def add_tvoc_score(reading):
    """Add tvoc_score / tvoc_level to a sensor reading, in place."""
    reading["tvoc_score"] = tvoc_score(reading.get("tvoc"))
    reading["tvoc_level"] = tvoc_level(reading["tvoc_score"])
    return reading


class OdourMonitor:
    def __init__(self, settings, uplink, clock=time.time):
        self.s = settings
        self.uplink = uplink
        self.clock = clock
        self.limits = {k: v for k, v in (
            ("tvoc", settings.odour_tvoc_limit),
            ("tvoc_score", settings.odour_tvoc_score_limit),
            ("aqi", settings.odour_aqi_limit),
            ("nh3_ppm", settings.odour_nh3_limit),
            ("h2s_ppm", settings.odour_h2s_limit),
        ) if v is not None}
        self._lock = threading.Lock()
        self._high_since = None   # start of the current continuous high spell
        self._low_since = None    # start of the current low spell while alerting
        self._alerts = 0          # odour_high alerts sent in this episode
        self._latest = {}         # last TVOC reading, for /health

    @property
    def alerting(self):
        return self._alerts > 0

    def state(self):
        return {"alerting": self.alerting, "limits": self.limits, **self._latest}

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
        self._latest = {k: reading.get(k) for k in ("tvoc", "tvoc_score", "tvoc_level")}
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
