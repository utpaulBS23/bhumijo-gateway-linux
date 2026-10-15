"""IP camera: reachability, frozen-feed check and snapshots for door alerts.

Every check shells out to ffmpeg with a hard timeout, so a hung RTSP stream
can never stall the node. Nothing here is in the unlock path: a dead camera
only means alerts go out without a picture.
"""

import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("camera")


def tcp_probe(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _grab_gray_frame(rtsp, timeout=10):
    """One tiny grayscale frame as raw bytes, or None."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-i", rtsp, "-frames:v", "1", "-vf", "scale=64:36,format=gray",
           "-f", "rawvideo", "-"]
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log.warning("frame grab failed: %s", e)
        return None
    if res.returncode != 0 or len(res.stdout) != 64 * 36:
        return None
    return res.stdout


def frames_differ(a, b, threshold=1.0):
    """Mean absolute pixel difference above threshold -> feed is live."""
    diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return diff > threshold


class Camera:
    def __init__(self, settings):
        self.s = settings
        self._lock = threading.Lock()
        self.status = {"reachable": None, "streaming": None, "frozen": None,
                       "checked_at": None}

    def _update(self, **kw):
        with self._lock:
            self.status.update(kw)
            self.status["checked_at"] = datetime.now(timezone.utc).isoformat()

    def snapshot_status(self):
        with self._lock:
            return dict(self.status)

    def probe(self):
        if not self.s.cam_host:
            return
        self._update(reachable=tcp_probe(self.s.cam_host, self.s.cam_port))

    def frame_check(self, gap=3.0):
        if not self.s.cam_rtsp:
            return
        first = _grab_gray_frame(self.s.cam_rtsp)
        if first is None:
            self._update(streaming=False, frozen=None)
            return
        time.sleep(gap)
        second = _grab_gray_frame(self.s.cam_rtsp)
        if second is None:
            self._update(streaming=False, frozen=None)
            return
        self._update(streaming=True, frozen=not frames_differ(first, second))

    def snapshot(self):
        """Save a JPEG; return its filename as the snapshot ref, or None."""
        if not self.s.cam_rtsp:
            return None
        os.makedirs(self.s.snapshot_dir, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".jpg"
        path = os.path.join(self.s.snapshot_dir, name)
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
               "-i", self.s.cam_rtsp, "-frames:v", "1", "-q:v", "3", "-y", path]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning("snapshot failed: %s", e)
            return None
        if res.returncode != 0:
            log.warning("snapshot failed: %s", res.stderr.decode(errors="replace")[-200:])
            return None
        return name

    def prune_snapshots(self, now=None):
        """Delete snapshots older than SNAPSHOT_KEEP_DAYS (SD card space)."""
        if not os.path.isdir(self.s.snapshot_dir):
            return 0
        cutoff = (now or time.time()) - self.s.snapshot_keep_days * 86400
        removed = 0
        for name in os.listdir(self.s.snapshot_dir):
            path = os.path.join(self.s.snapshot_dir, name)
            if name.endswith(".jpg") and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        return removed

    def run_probe_loop(self, stop):
        while not stop.is_set():
            self.probe()
            stop.wait(self.s.cam_probe_interval)

    def run_frame_loop(self, stop):
        while not stop.is_set():
            self.frame_check()
            self.prune_snapshots()
            stop.wait(self.s.cam_frame_interval)


def make_snapshotter(camera, uplink):
    """snapshot() for the doors: take a JPEG, queue its upload, return its name."""
    def snapshot():
        name = camera.snapshot()
        if name:
            uplink.queue_snapshot(name, os.path.join(camera.s.snapshot_dir, name),
                                  datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
        return name
    return snapshot
