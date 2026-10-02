"""ENS160 + AHT20 over I2C, and IP-camera health/snapshots.

Sensor reads use smbus2 (I2C bus 1). Camera checks shell out to ffmpeg with
hard timeouts so a hung RTSP stream can never stall the node.
"""

import logging
import os
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("sensors")

AHT20_ADDR = 0x38
ENS160_ADDR = 0x53

# ENS160 registers
ENS160_OPMODE = 0x10
ENS160_TEMP_IN = 0x13
ENS160_RH_IN = 0x15
ENS160_STATUS = 0x20
ENS160_AQI = 0x21
ENS160_TVOC = 0x22
ENS160_ECO2 = 0x24
ENS160_MODE_STANDARD = 0x02


# ---- pure decoders (unit-tested) -----------------------------------------

def aht20_decode(raw):
    """AHT20 measurement frame [status, 5 data bytes, (crc)] -> (temp_c, humidity_pct)."""
    hum = (raw[1] << 12) | (raw[2] << 4) | (raw[3] >> 4)
    tmp = ((raw[3] & 0x0F) << 16) | (raw[4] << 8) | raw[5]
    return round(tmp / 1048576 * 200 - 50, 2), round(hum / 1048576 * 100, 2)


def ens160_validity(status):
    """0 normal, 1 warm-up, 2 initial start-up, 3 invalid."""
    return (status >> 2) & 0x03


# ---- sensors -------------------------------------------------------------

class Sensors:
    def __init__(self, bus_no=1):
        self.bus_no = bus_no
        self._bus = None
        self._ens_ready = False

    def _open(self):
        if self._bus is None:
            from smbus2 import SMBus   # imported lazily: absent on dev machines
            self._bus = SMBus(self.bus_no)
        return self._bus

    def _read_aht20(self, bus):
        from smbus2 import i2c_msg
        status = bus.read_byte(AHT20_ADDR)
        if not status & 0x08:   # not calibrated -> init
            bus.write_i2c_block_data(AHT20_ADDR, 0xBE, [0x08, 0x00])
            time.sleep(0.01)
        bus.write_i2c_block_data(AHT20_ADDR, 0xAC, [0x33, 0x00])
        time.sleep(0.08)
        msg = i2c_msg.read(AHT20_ADDR, 7)
        bus.i2c_rdwr(msg)
        raw = list(msg)
        if raw[0] & 0x80:
            raise IOError("AHT20 busy")
        return aht20_decode(raw)

    def _read_ens160(self, bus, temperature, humidity):
        if not self._ens_ready:
            bus.write_byte_data(ENS160_ADDR, ENS160_OPMODE, ENS160_MODE_STANDARD)
            time.sleep(0.05)
            self._ens_ready = True
        if temperature is not None and humidity is not None:
            # Compensation inputs improve gas accuracy
            t = int((temperature + 273.15) * 64)
            h = int(humidity * 512)
            bus.write_i2c_block_data(ENS160_ADDR, ENS160_TEMP_IN,
                                     [t & 0xFF, t >> 8, h & 0xFF, h >> 8])
        status = bus.read_byte_data(ENS160_ADDR, ENS160_STATUS)
        validity = ens160_validity(status)
        aqi = bus.read_byte_data(ENS160_ADDR, ENS160_AQI) & 0x07
        tv = bus.read_i2c_block_data(ENS160_ADDR, ENS160_TVOC, 2)
        ec = bus.read_i2c_block_data(ENS160_ADDR, ENS160_ECO2, 2)
        if validity != 0:
            log.info("ENS160 not ready (validity=%d), sending nulls", validity)
            return None, None, None
        return tv[0] | (tv[1] << 8), ec[0] | (ec[1] << 8), aqi

    def read(self):
        """-> dict with tvoc, eco2, aqi, temperature, humidity (None on failure)."""
        out = dict(tvoc=None, eco2=None, aqi=None, temperature=None, humidity=None)
        try:
            bus = self._open()
        except Exception as e:
            log.error("I2C open failed: %s", e)
            return out
        try:
            out["temperature"], out["humidity"] = self._read_aht20(bus)
        except Exception as e:
            log.error("AHT20 read failed: %s", e)
        try:
            out["tvoc"], out["eco2"], out["aqi"] = self._read_ens160(
                bus, out["temperature"], out["humidity"])
        except Exception as e:
            self._ens_ready = False
            log.error("ENS160 read failed: %s", e)
        return out


# ---- camera --------------------------------------------------------------

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

    def run_probe_loop(self, stop):
        while not stop.is_set():
            self.probe()
            stop.wait(self.s.cam_probe_interval)

    def run_frame_loop(self, stop):
        while not stop.is_set():
            self.frame_check()
            stop.wait(self.s.cam_frame_interval)


def run_sensor_loop(settings, sensors, camera, uplink, stop):
    while not stop.is_set():
        reading = sensors.read()
        payload = {"facility": settings.facility_id,
                   "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                   **reading,
                   # Camera health piggybacks the 1-minute push (plan section 6, note)
                   "camera": camera.snapshot_status()}
        uplink.enqueue("sensor", payload)
        stop.wait(settings.sensor_interval)
