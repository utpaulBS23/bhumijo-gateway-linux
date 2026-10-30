"""IP cameras: reachability, frozen-feed check and snapshots for door alerts.

Every check shells out to ffmpeg with a hard timeout, so a hung RTSP stream
can never stall the node. Nothing here is in the unlock path: a dead camera
only means alerts go out without a picture.
"""

import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("camera")


def hide_login(text):
    """Blank the user:password in any rtsp:// URL. ffmpeg's errors and
    TimeoutExpired both repeat the full URL, and logs must never hold it."""
    return re.sub(r"(rtsp://)[^@\s/]+@", r"\1***:***@", str(text))


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
        log.warning("frame grab failed: %s", hide_login(e))
        return None
    if res.returncode != 0 or len(res.stdout) != 64 * 36:
        return None
    return res.stdout


def frames_differ(a, b, threshold=1.0):
    """Mean absolute pixel difference above threshold -> feed is live."""
    diff = sum(abs(x - y) for x, y in zip(a, b)) / len(a)
    return diff > threshold


def prune_snapshots(snapshot_dir, keep_days, now=None):
    """Delete snapshots older than keep_days (SD card space). Shared by every camera."""
    if not os.path.isdir(snapshot_dir):
        return 0
    cutoff = (now or time.time()) - keep_days * 86400
    removed = 0
    for name in os.listdir(snapshot_dir):
        path = os.path.join(snapshot_dir, name)
        if name.endswith(".jpg") and os.path.getmtime(path) < cutoff:
            os.remove(path)
            removed += 1
    return removed


def run_prune_loop(settings, stop, interval=3600):
    """Runs even with CAM_MONITOR=0: alert snapshots still fill the card."""
    while not stop.is_set():
        prune_snapshots(settings.snapshot_dir, settings.snapshot_keep_days)
        stop.wait(interval)


class Camera:
    """One camera (config.CameraSpec): its health, and pictures for its doors' alerts."""

    def __init__(self, settings, spec):
        self.s = settings
        self.spec = spec
        self.name = spec.name
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
        if not self.spec.host:
            return
        self._update(reachable=tcp_probe(self.spec.host, self.spec.port))

    def frame_check(self, gap=3.0):
        if not self.spec.rtsp:
            return
        first = _grab_gray_frame(self.spec.rtsp)
        if first is None:
            self._update(streaming=False, frozen=None)
            return
        time.sleep(gap)
        second = _grab_gray_frame(self.spec.rtsp)
        if second is None:
            self._update(streaming=False, frozen=None)
            return
        self._update(streaming=True, frozen=not frames_differ(first, second))

    def snapshot(self):
        """Save a JPEG; return its filename as the snapshot ref, or None.
        The camera name is in the filename: two cameras can fire the same second."""
        if not self.spec.rtsp:
            return None
        os.makedirs(self.s.snapshot_dir, exist_ok=True)
        name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{self.name}.jpg"
        path = os.path.join(self.s.snapshot_dir, name)
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
               "-i", self.spec.rtsp, "-frames:v", "1", "-q:v", "3", "-y", path]
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=10)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            log.warning("[%s] snapshot failed: %s", self.name, hide_login(e))
            return None
        if res.returncode != 0:
            log.warning("[%s] snapshot failed: %s", self.name,
                        hide_login(res.stderr.decode(errors="replace")[-300:]))
            return None
        return name

    def run_probe_loop(self, stop):
        while not stop.is_set():
            self.probe()
            stop.wait(self.s.cam_probe_interval)

    def run_frame_loop(self, stop):
        while not stop.is_set():
            self.frame_check()
            stop.wait(self.s.cam_frame_interval)


def camera_fields(cameras):
    """{"camera": first camera's status, "cameras": {name: status}}; nulls with none.
    "camera" stays for backends that predate several cameras."""
    if not cameras:
        return {"camera": None, "cameras": None}
    status = {c.name: c.snapshot_status() for c in cameras}
    return {"camera": next(iter(status.values())), "cameras": status}


def make_snapshotter(camera, uplink):
    """snapshot() for the doors: take a JPEG, queue its upload, return its name."""
    def snapshot():
        name = camera.snapshot()
        if name:
            uplink.queue_snapshot(name, os.path.join(camera.s.snapshot_dir, name),
                                  datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                                  camera=camera.name)
        return name
    return snapshot


# Store key for the admin panel's camera -> doors links (written by uplink.pull_tokens)
LINKS_KEY = "camera_doors"


class CameraLinks:
    """Which camera pictures each door's alerts.

    The admin panel's choice when the backend has sent one (`cameraDoors` in the
    token pull, kept in the store so it survives a reboot while offline), else
    the .env links (CAM_<NAME>_DOORS). Office staff change it without touching
    the Pi; camera logins stay in .env.
    """

    def __init__(self, settings, cameras, store, uplink):
        self.cameras = {c.name: c for c in cameras}
        self.by_host = {c.spec.host: c.name for c in cameras if c.spec.host}
        self.doors = [d.name for d in settings.doors]
        self.default = {d.name: d.camera for d in settings.doors}
        self.store = store
        self.shooters = {c.name: make_snapshotter(c, uplink) for c in cameras}
        self._seen = None         # last raw value, so a bad one is logged once

    def _from_backend(self):
        """{door: camera} from the stored admin panel links, or None (use .env).
        Keys may be a camera name or its IP. Anything invalid -> .env, logged."""
        raw = self.store.get_cred(LINKS_KEY) if self.store is not None else None
        if not raw:
            return None
        try:
            links = json.loads(raw)
            result = dict.fromkeys(self.doors)   # a door the panel leaves out: no camera
            for key, doors in links.items():
                cam = key.strip().lower() if key.strip().lower() in self.cameras \
                    else self.by_host.get(key.strip())
                if cam is None:
                    raise ValueError(f"camera {key!r} is not set up on this Pi")
                for door in doors:
                    door = str(door).strip().lower()
                    if door not in result:
                        raise ValueError(f"door {door!r} is not on this Pi")
                    if result[door] is not None:
                        raise ValueError(f"door {door} given to two cameras")
                    result[door] = cam
        except (ValueError, AttributeError, TypeError) as e:
            if raw != self._seen:
                log.error("admin panel camera links ignored, using .env: %s", e)
            self._seen = raw
            return None
        if raw != self._seen:
            log.info("camera links from the admin panel: %s", result)
        self._seen = raw
        return result

    def links(self):
        """({door: camera or None}, "admin" | "env")."""
        backend = self._from_backend()
        return (backend, "admin") if backend is not None else (dict(self.default), "env")

    def snapshotter(self, door):
        """snapshot() for one door: the camera linked right now takes the picture.
        Returns {"snapshot": name, "camera": camera} or None (no camera / failed)."""
        def snapshot():
            cam = self.links()[0].get(door)
            if cam is None:
                return None
            name = self.shooters[cam]()
            return {"snapshot": name, "camera": cam} if name else None
        return snapshot

    def state(self):
        links, source = self.links()
        return {"source": source, "doors": links}
