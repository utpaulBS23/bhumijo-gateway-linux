#!/bin/bash
# Air sensor board (ESP32-S3 Feather + ENS160 + AHT20 over WiFi). Run through
# the `facility` command, as the normal user (not sudo):
#
#   facility sensor flash      new board on USB: install MicroPython, then `upload`
#   facility sensor upload     WiFi settings + firmware onto the board (also: new WiFi password)
#   facility sensor enable     point the node at the board (SENSOR_URL), restart
#   facility sensor disable    back to sensors wired to the Pi's own I2C
#   facility sensor test       read the board now: temperature, humidity, TVOC + score
#
# Flashing needs the board on USB and internet once (tools + MicroPython).
# The WiFi password is read hidden, written to a private temp file, copied to
# the board and deleted: it is never stored on the Pi or printed.
set -euo pipefail

NODE="$(cd "$(dirname "$0")/.." && pwd)"   # repo checkout or /opt/facility-node
FW="$NODE/firmware/air-sensor"
ENV=/opt/facility-node/.env
SVC=facility-node
VENV="${FACILITY_ESP_VENV:-$HOME/.facility-esp}"
MP_URL="${MICROPYTHON_URL:-https://micropython.org/resources/firmware/ESP32_GENERIC_S3-20260824-v1.29.0.bin}"
DEFAULT_IP=192.168.10.105
GATEWAY=192.168.10.1

say() { echo; echo "== $*"; }
die() { echo "!! $*"; exit 1; }

board_url() {   # the URL the service logged at start (no sudo needed), else the default
    local u
    u=$(journalctl -u "$SVC" -g 'air sensors: http' -n 1 -o cat --no-pager 2>/dev/null \
        | grep -o 'http[^ ]*' | tail -1 || true)
    echo "${u:-http://$DEFAULT_IP/sensor}"
}

port() {
    local p="${SENSOR_PORT:-}"
    [ -n "$p" ] || p=$(ls /dev/ttyACM* 2>/dev/null | head -1 || true)
    [ -n "$p" ] || die "no board on USB (/dev/ttyACM*). Plug the Feather in with a USB-C data cable."
    [ -r "$p" ] && [ -w "$p" ] || die "no access to $p: add yourself to dialout (sudo usermod -aG dialout $USER), log in again"
    echo "$p"
}

tools() {
    if [ ! -x "$VENV/bin/mpremote" ] || [ ! -x "$VENV/bin/esptool" ]; then
        say "installing esptool + mpremote into $VENV (needs internet, once)"
        python3 -m venv "$VENV"
        "$VENV/bin/pip" install -q esptool mpremote
    fi
}

mp() { "$VENV/bin/mpremote" connect "$(port)" "$@"; }

# Wait until MicroPython answers on USB (the port vanishes during resets).
wait_mp() {
    for _ in $(seq 20); do
        mp exec "print('ok')" 2>/dev/null | grep -q ok && return 0
        sleep 1.5
    done
    die "board not answering on USB after reset; unplug and replug it, then: facility sensor upload"
}

check_i2c() {
    local found
    found=$(mp exec "
import machine, time
machine.Pin(7, machine.Pin.OUT, value=1); time.sleep_ms(50)
print([hex(a) for a in machine.I2C(0, sda=machine.Pin(3), scl=machine.Pin(4)).scan()])")
    echo "I2C devices: $found  (want 0x38 AHT20, 0x53 ENS160)"
    if [[ "$found" != *0x38* || "$found" != *0x53* ]]; then
        echo "!! a sensor is missing: push the STEMMA QT cables fully home (ENS160 -> AHT20)."
        read -rp "Continue anyway? [y/N]: " v; [ "${v,,}" = y ] || exit 1
    fi
}

cmd_flash() {
    tools
    local p bin; p=$(port)
    bin="$VENV/$(basename "$MP_URL")"
    [ -s "$bin" ] || { say "downloading MicroPython"; curl -fsSL -o "$bin" "$MP_URL"; }
    say "checking the board on $p"
    # esptool resets the Feather into its bootloader by itself; if that fails
    # it needs the buttons.
    if ! "$VENV/bin/esptool" --chip esp32s3 --port "$p" flash-id >/dev/null 2>&1; then
        sleep 3; p=$(port)
        "$VENV/bin/esptool" --chip esp32s3 --port "$p" flash-id >/dev/null 2>&1 || die \
            "can't reach the ESP32-S3 bootloader. Hold BOOT, press and release RESET, release BOOT, then run this again."
    fi
    echo "ESP32-S3 found"
    say "erasing and flashing MicroPython (about 1 min)"
    "$VENV/bin/esptool" --chip esp32s3 --port "$(port)" erase-flash | tail -1
    "$VENV/bin/esptool" --chip esp32s3 --port "$(port)" --baud 460800 write-flash 0 "$bin" \
        | grep -E 'Hash of data verified|rror' || die "flashing failed"
    sleep 3
    wait_mp
    echo "MicroPython: $(mp exec 'import sys; print(sys.version)')"
    cmd_upload
}

cmd_upload() {
    tools
    wait_mp
    say "checking the sensors"
    check_i2c

    say "WiFi settings (2.4 GHz only)"
    nmcli -t -f SSID,FREQ dev wifi list 2>/dev/null | awk -F: '$1!="" && $2 ~ /^24/ {print "  seen: " $1}' | sort -u || true
    local ssid pass ip
    read -rp "WiFi name (exactly, case-sensitive): " ssid
    [ -n "$ssid" ] || die "empty WiFi name, nothing changed"
    read -rsp "WiFi password (hidden): " pass; echo
    [ ${#pass} -ge 8 ] || die "a WPA2 password has at least 8 characters, nothing changed"
    read -rp "Board IP [$DEFAULT_IP]: " ip; ip=${ip:-$DEFAULT_IP}

    local tmp; tmp=$(mktemp); chmod 600 "$tmp"
    trap 'rm -f "$tmp"' EXIT
    # repr() quotes any character in the name or password safely
    SSID="$ssid" PASS="$pass" IP="$ip" GW="$GATEWAY" python3 -c '
import os
e = os.environ
print("WIFI_SSID = %r\nWIFI_PASSWORD = %r\nIP = %r\nGATEWAY = %r" % (e["SSID"], e["PASS"], e["IP"], e["GW"]))' > "$tmp"
    unset pass

    say "uploading firmware + settings"
    mp cp "$tmp" :board_secrets.py + cp "$FW/main.py" :main.py + reset >/dev/null
    rm -f "$tmp"
    echo "uploaded; the board restarts and joins the WiFi"

    say "waiting for http://$ip/sensor (up to 60 s)"
    for _ in $(seq 20); do
        if curl -sf -m3 "http://$ip/sensor" >/dev/null; then
            show "http://$ip/sensor"
            echo
            echo "Next: facility sensor enable   (if SENSOR_URL isn't set yet)"
            return 0
        fi
        sleep 3
    done
    echo "!! no answer from $ip. Likely a wrong WiFi name/password, or a 5 GHz-only network."
    echo "   See the board's own log:  $VENV/bin/mpremote connect $(port) repl   (Ctrl-] to leave)"
    echo "   Then fix it with:          facility sensor upload"
    return 1
}

# Print one board reading, with the node's TVOC score.
show() {
    local py=python3; [ -x /opt/facility-node/venv/bin/python ] && py=/opt/facility-node/venv/bin/python
    local body; body=$(curl -sf -m5 "$1") || return 1
    echo "$body" | PYTHONPATH="$NODE" "$py" -c '
import json, sys
d = json.load(sys.stdin)
try:
    from odour import tvoc_level, tvoc_score
    score = tvoc_score(d.get("tvoc"))
    lvl = tvoc_level(score)
except ImportError:
    score = lvl = None
state = {0: "ready", 1: "warming up (~3 min)", 2: "initial start-up (~1 h when new)",
         3: "invalid"}.get(d.get("ens160_validity"), "not answering")
print("  board        up %ss, reading %ss old" % (d.get("uptime_s"), d.get("age_s")))
print("  temperature  %s C   humidity %s %%" % (d.get("temperature"), d.get("humidity")))
print("  ENS160       %s" % state)
if d.get("tvoc") is not None:
    print("  TVOC         %s ppb  -> score %s (%s)" % (d["tvoc"], score, lvl))
    print("  eCO2 / AQI   %s ppm / %s" % (d.get("eco2"), d.get("aqi")))'
}

cmd_test() {
    local url="${1:-$(board_url)}"
    echo "Reading $url"
    show "$url" || die "no answer: check the board's power and WiFi (facility sensor upload to redo WiFi)"
}

set_sensor_url() {   # value ("" = Pi I2C)
    sudo cp -p "$ENV" "$ENV.bak-sensor"
    sudo env ENVFILE="$ENV" VAL="$1" python3 - <<'PY'
import os, re
path, val = os.environ["ENVFILE"], os.environ["VAL"]
lines = open(path).read().splitlines()
for i, line in enumerate(lines):
    if re.match(r"^SENSOR_URL=", line):
        lines[i] = "SENSOR_URL=" + val
        break
else:
    lines += ["", "# Air sensors on the ESP32 WiFi board (SETUP.md 4a); empty = Pi I2C",
              "SENSOR_URL=" + val]
open(path, "w").write("\n".join(lines) + "\n")
PY
    sudo chown root:facility "$ENV" "$ENV.bak-sensor"
    sudo chmod 640 "$ENV" "$ENV.bak-sensor"
    sudo systemctl restart "$SVC"
    sleep 3
    journalctl -u "$SVC" -n 30 --no-pager 2>/dev/null | grep -o 'air sensors: .*' | tail -1 \
        || echo "service restarted"
}

cmd_enable() {
    local url="${1:-http://$DEFAULT_IP/sensor}"
    echo "Checking $url first..."
    show "$url" || die "the board doesn't answer at $url, nothing changed"
    set_sensor_url "$url"
    echo "Enabled. The node reads the board every SENSOR_INTERVAL; check with: facility doctor"
}

cmd_disable() {
    set_sensor_url ""
    echo "Disabled: the node reads ENS160/AHT20 on the Pi's own I2C again."
}

case "${1:-help}" in
    flash)   cmd_flash ;;
    upload)  cmd_upload ;;
    enable)  shift; cmd_enable "$@" ;;
    disable) cmd_disable ;;
    test)    shift; cmd_test "$@" ;;
    *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
