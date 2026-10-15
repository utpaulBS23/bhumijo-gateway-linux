"""Air sensors: ENS160 (TVOC, eCO2, AQI) + AHT20 (temperature, humidity).

Read over the Pi's own I2C with smbus2 (bus 1), or over HTTP when the sensors
sit on the separate WiFi board (SENSOR_URL, firmware/air-sensor). Also the
1-minute sensor push, which carries the camera health along.
"""

import logging
import time
from datetime import datetime, timezone

from odour.monitor import add_tvoc_score

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


def ens160_usable(status, aqi):
    """True when the gas values are real readings.

    Right after the switch to standard mode the chip still reports validity 0
    with all-zero data; it isn't measuring yet (STATAS, bit 7, clear). AQI is
    1-5 whenever it is, so a 0 means "no data", not clean air.
    """
    return bool(status & 0x80) and ens160_validity(status) == 0 and 1 <= aqi <= 5


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
        if not ens160_usable(status, aqi):
            log.info("ENS160 not ready (status=0x%02x, validity=%d), sending nulls",
                     status, validity)
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


class HttpSensors:
    """The same readings from a networked sensor board (firmware/air-sensor) at SENSOR_URL.

    The Pi polls the board, as it does the relay board, so the Pi opens no
    inbound port. A board that is down or stale gives nulls and a log line,
    never a stall: the readings are only ever used for alerts, not the doors.
    """

    KEYS = ("tvoc", "eco2", "aqi", "temperature", "humidity")

    def __init__(self, url, timeout=3.0, max_age=120, session=None):
        import requests
        self.url = url
        self.timeout = timeout
        self.max_age = max_age          # seconds; older board readings count as missing
        self.session = session or requests.Session()
        self._errors = requests.RequestException

    def read(self):
        out = dict.fromkeys(self.KEYS)
        try:
            r = self.session.get(self.url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except (self._errors, ValueError) as e:
            log.error("sensor board %s: %s", self.url, e)
            return out
        if not isinstance(data, dict):
            log.error("sensor board %s: unexpected reply %r", self.url, data)
            return out
        age = data.get("age_s")
        if not isinstance(age, (int, float)) or age > self.max_age:
            log.warning("sensor board reading stale or missing (age_s=%r)", age)
            return out
        for k in self.KEYS:
            v = data.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = v
        if out["tvoc"] is None and data.get("ens160_validity") not in (None, 0):
            log.info("ENS160 on the sensor board not ready (validity=%s), sending nulls",
                     data.get("ens160_validity"))
        return out


def make_sensors(settings):
    """HTTP sensor board when SENSOR_URL is set, otherwise the Pi's own I2C."""
    if settings.sensor_url:
        return HttpSensors(settings.sensor_url, timeout=settings.sensor_timeout,
                           max_age=max(120, 3 * settings.sensor_interval))
    return Sensors()


def run_sensor_loop(settings, sensors, camera, uplink, stop, mq=None, odour=None):
    """The 1-minute "sensor" push. sensors=None (odour off) or camera=None
    (camera off) send nulls for that part, so the backend sees the same fields."""
    while not stop.is_set():
        reading = sensors.read() if sensors is not None else dict.fromkeys(HttpSensors.KEYS)
        if mq is not None:
            reading.update(mq.read())
        add_tvoc_score(reading)
        if odour is not None:
            odour.update(reading)
        payload = {"facility": settings.facility_id,
                   "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                   **reading,
                   # Camera health piggybacks the 1-minute push (plan section 6, note)
                   "camera": camera.snapshot_status() if camera is not None else None}
        uplink.enqueue("sensor", payload)
        stop.wait(settings.sensor_interval)
