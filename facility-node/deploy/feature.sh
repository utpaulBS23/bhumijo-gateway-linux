#!/bin/bash
# The node's three functions, each switched on or off in .env. Run through the
# `facility` command:
#
#   facility feature                    show which functions are on
#   facility feature camera off         switch one off (door | camera | odour)
#   facility feature camera on          ... or back on, then restart + check
#
# Off means no background jobs, checks or endpoints for that function; its
# settings stay in .env untouched, so switching back on needs no re-setup.
set -euo pipefail

ENV=/opt/facility-node/.env
DIR=/opt/facility-node
SVC=facility-node

[ "$EUID" -eq 0 ] || { echo "Run as root: facility feature (or sudo bash $0)"; exit 1; }
[ -f "$ENV" ] || { echo "$ENV not found - install the node first"; exit 1; }

key() {
    case "${1,,}" in
        door|doors) echo ENABLE_DOORS ;;
        camera|cam) echo ENABLE_CAMERA ;;
        odour|odor|sensor|sensors) echo ENABLE_ODOUR ;;
        *) echo "unknown function '$1': use door, camera or odour" >&2; exit 1 ;;
    esac
}

# Same rule as config.py: missing or empty = on
state() {
    local v; v=$(grep -m1 -E "^$1=" "$ENV" | cut -d= -f2- || true)
    case "${v,,}" in ""|1|true|yes|on) echo on ;; *) echo off ;; esac
}

show() {
    printf "  %-7s %-4s %s\n" door "$(state ENABLE_DOORS)" "lock/unlock: relay board, QR scanners, Facility app"
    printf "  %-7s %-4s %s\n" camera "$(state ENABLE_CAMERA)" "camera monitoring: reachable, live feed, snapshots"
    printf "  %-7s %-4s %s\n" odour "$(state ENABLE_ODOUR)" "odour monitoring: TVOC score, temperature, humidity, alert"
}

if [ $# -eq 0 ]; then show; exit 0; fi
[ $# -eq 2 ] || { echo "usage: facility feature [door|camera|odour on|off]"; exit 1; }
k=$(key "$1")
case "${2,,}" in on) val=1 ;; off) val=0 ;; *) echo "say on or off"; exit 1 ;; esac

others=0
for o in ENABLE_DOORS ENABLE_CAMERA ENABLE_ODOUR; do
    [ "$o" != "$k" ] && [ "$(state "$o")" = on ] && others=1
done
if [ "$val" = 0 ] && [ "$others" = 0 ]; then
    echo "That would switch off all three functions; the node needs at least one."
    exit 1
fi

cp -p "$ENV" "$ENV.bak-feature"
ENVFILE="$ENV" KEY="$k" VAL="$val" python3 - <<'PY'
import os, re
path, key, val = os.environ["ENVFILE"], os.environ["KEY"], os.environ["VAL"]
lines = open(path).read().splitlines()
for i, line in enumerate(lines):
    if re.match(rf"^{key}=", line):
        lines[i] = f"{key}={val}"
        break
else:
    lines += [f"{key}={val}"]
open(path, "w").write("\n".join(lines) + "\n")
PY
chown root:facility "$ENV" "$ENV.bak-feature"
chmod 640 "$ENV" "$ENV.bak-feature"
echo "$k=$val (previous .env kept as $ENV.bak-feature)"
systemctl restart "$SVC"
echo "Service restarted."
show
sleep 3
fn=$(echo "$k" | sed 's/ENABLE_//; s/DOORS/door/; s/CAMERA/camera/; s/ODOUR/odour/')
if [ "$val" = 1 ]; then
    echo
    cd "$DIR" && sudo -u facility "$DIR/venv/bin/python" doctor.py "$fn" || true
fi
