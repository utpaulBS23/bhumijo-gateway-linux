"""Air sensor board for the facility node: Adafruit ESP32-S3 Feather + ENS160 + AHT20.

The sensors hang off the Feather's STEMMA QT port. The board reads them every
READ_INTERVAL seconds and serves the latest reading over WiFi:

    GET /sensor  ->  {"temperature": 24.1, "humidity": 55.2, "tvoc": 120, "eco2": 450,
                      "aqi": 1, "ens160_validity": 0, "age_s": 3, "uptime_s": 812}

The Pi polls it (SENSOR_URL in the node's .env), the same way it polls the
KC868 relay board, so the Pi opens no inbound port. A value the board doesn't
have is null, never a guess: gas values stay null while the ENS160 warms up,
and age_s tells the Pi how old the reading is.

WiFi settings live in board_secrets.py on the board (see board_secrets.py.example).
"""

import json
import socket
import time

import machine
import network

from board_secrets import WIFI_PASSWORD, WIFI_SSID

try:
    from board_secrets import IP, GATEWAY   # fixed address; omit both for DHCP
except ImportError:
    IP = GATEWAY = None

# Feather ESP32-S3 STEMMA QT: SDA 3, SCL 4, and GPIO 7 switches its power on.
try:
    from board_secrets import SDA_PIN, SCL_PIN, I2C_POWER_PIN
except ImportError:
    SDA_PIN, SCL_PIN, I2C_POWER_PIN = 3, 4, 7

READ_INTERVAL = 10      # seconds between sensor reads
PORT = 80

AHT20_ADDR = 0x38
ENS160_ADDR = 0x53
ENS160_OPMODE = 0x10
ENS160_TEMP_IN = 0x13
ENS160_STATUS = 0x20
ENS160_AQI = 0x21
ENS160_TVOC = 0x22
ENS160_ECO2 = 0x24
ENS160_MODE_STANDARD = 0x02

# Reboots the board if the loop ever hangs (WiFi stack, I2C lock-up).
wdt = machine.WDT(timeout=30000)


# ---- sensors (same register logic as the node's odour/sensors.py) --------

def i2c_start():
    if I2C_POWER_PIN is not None:
        machine.Pin(I2C_POWER_PIN, machine.Pin.OUT, value=1)
        time.sleep_ms(50)
    bus = machine.I2C(0, sda=machine.Pin(SDA_PIN), scl=machine.Pin(SCL_PIN), freq=100000)
    print("I2C devices:", [hex(a) for a in bus.scan()], "(want 0x38 AHT20, 0x53 ENS160)")
    return bus


def read_aht20(bus):
    status = bus.readfrom(AHT20_ADDR, 1)[0]
    if not status & 0x08:   # not calibrated -> init
        bus.writeto(AHT20_ADDR, b"\xbe\x08\x00")
        time.sleep_ms(10)
    bus.writeto(AHT20_ADDR, b"\xac\x33\x00")
    time.sleep_ms(80)
    raw = bus.readfrom(AHT20_ADDR, 7)
    if raw[0] & 0x80:
        raise OSError("AHT20 busy")
    hum = (raw[1] << 12) | (raw[2] << 4) | (raw[3] >> 4)
    tmp = ((raw[3] & 0x0F) << 16) | (raw[4] << 8) | raw[5]
    return round(tmp / 1048576 * 200 - 50, 2), round(hum / 1048576 * 100, 2)


class ENS160:
    def __init__(self, bus):
        self.bus = bus
        self.ready = False

    def read(self, temperature, humidity):
        """-> (tvoc, eco2, aqi, validity); gas values None until valid."""
        if not self.ready:
            self.bus.writeto_mem(ENS160_ADDR, ENS160_OPMODE, bytes([ENS160_MODE_STANDARD]))
            time.sleep_ms(50)
            self.ready = True
        if temperature is not None and humidity is not None:
            # Compensation inputs improve gas accuracy
            t = int((temperature + 273.15) * 64)
            h = int(humidity * 512)
            self.bus.writeto_mem(ENS160_ADDR, ENS160_TEMP_IN,
                                 bytes([t & 0xFF, t >> 8, h & 0xFF, h >> 8]))
        status = self.bus.readfrom_mem(ENS160_ADDR, ENS160_STATUS, 1)[0]
        validity = (status >> 2) & 0x03
        aqi = self.bus.readfrom_mem(ENS160_ADDR, ENS160_AQI, 1)[0] & 0x07
        tv = self.bus.readfrom_mem(ENS160_ADDR, ENS160_TVOC, 2)
        ec = self.bus.readfrom_mem(ENS160_ADDR, ENS160_ECO2, 2)
        # validity 1 warm-up, 2 initial start-up, 3 invalid. Just after the mode
        # switch it reads 0 with all-zero data while not measuring yet (STATAS,
        # bit 7, clear); AQI is 1-5 whenever it is.
        if validity != 0 or not status & 0x80 or not 1 <= aqi <= 5:
            return None, None, None, validity
        return tv[0] | (tv[1] << 8), ec[0] | (ec[1] << 8), aqi, validity


reading = {"temperature": None, "humidity": None, "tvoc": None, "eco2": None,
           "aqi": None, "ens160_validity": None}
read_at = None   # ticks_ms of the last read where at least one sensor answered


def read_sensors(bus, ens):
    global read_at
    out = {"temperature": None, "humidity": None, "tvoc": None, "eco2": None,
           "aqi": None, "ens160_validity": None}
    try:
        out["temperature"], out["humidity"] = read_aht20(bus)
    except OSError as e:
        print("AHT20 read failed:", e)
    try:
        out["tvoc"], out["eco2"], out["aqi"], out["ens160_validity"] = ens.read(
            out["temperature"], out["humidity"])
    except OSError as e:
        ens.ready = False
        print("ENS160 read failed:", e)
    reading.update(out)
    if out["temperature"] is not None or out["ens160_validity"] is not None:
        read_at = time.ticks_ms()


# ---- WiFi -------------------------------------------------------------------

wlan = network.WLAN(network.STA_IF)


def wifi_up():
    """Connect if needed; True when connected. Feeds the watchdog while waiting."""
    if wlan.isconnected():
        return True
    wlan.active(True)
    try:
        network.hostname("air-sensor")
    except (AttributeError, ValueError):
        pass
    try:
        wlan.config(pm=wlan.PM_NONE)   # power saving makes the board slow to answer
    except (AttributeError, ValueError):
        pass
    if IP:
        wlan.ifconfig((IP, "255.255.255.0", GATEWAY, GATEWAY))
    print("WiFi: connecting to", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    for _ in range(40):
        wdt.feed()
        if wlan.isconnected():
            print("WiFi: connected, IP", wlan.ifconfig()[0])
            return True
        time.sleep_ms(500)
    print("WiFi: not connected, retrying later")
    wlan.disconnect()
    return False


# ---- HTTP -------------------------------------------------------------------

def serve(server):
    """Answer at most one request; returns quickly when nobody is calling."""
    try:
        conn, _ = server.accept()
    except OSError:
        return
    try:
        conn.settimeout(2)
        request = conn.recv(512).decode("utf-8", "ignore")
        path = request.split(" ")[1] if request.count(" ") >= 2 else ""
        if path.split("?")[0] in ("/", "/sensor"):
            body = dict(reading)
            body["age_s"] = (None if read_at is None
                             else time.ticks_diff(time.ticks_ms(), read_at) // 1000)
            body["uptime_s"] = time.ticks_ms() // 1000
            status, payload = "200 OK", json.dumps(body)
        else:
            status, payload = "404 Not Found", '{"error":"not found"}'
        conn.sendall("HTTP/1.1 {}\r\nContent-Type: application/json\r\n"
                     "Content-Length: {}\r\nConnection: close\r\n\r\n{}".format(
                         status, len(payload), payload).encode())
    except OSError:
        pass
    finally:
        conn.close()


def main():
    bus = i2c_start()
    ens = ENS160(bus)
    wifi_up()
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", PORT))
    server.listen(2)
    server.settimeout(0.5)
    print("Serving GET /sensor on port", PORT)
    next_read = time.ticks_ms()
    next_wifi_check = time.ticks_add(time.ticks_ms(), 30000)
    while True:
        wdt.feed()
        now = time.ticks_ms()
        if time.ticks_diff(now, next_read) >= 0:
            read_sensors(bus, ens)
            next_read = time.ticks_add(now, READ_INTERVAL * 1000)
        if time.ticks_diff(now, next_wifi_check) >= 0:
            wifi_up()
            next_wifi_check = time.ticks_add(now, 30000)
        serve(server)


main()
