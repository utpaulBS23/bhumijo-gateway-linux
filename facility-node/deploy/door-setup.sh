#!/bin/bash
# Door unlock time (how long a QR / app unlock keeps the door open). Run
# through the `facility` command:
#
#   facility door                     each door's relay and unlock time
#   facility door delay male 8        male door: 8 s (1-30 s), restart, show it
#   facility door delay all 5         every door: 5 s
#
# The KC868 holds the door's relay for that time and releases it by itself.
# The exit button is separate: it opens for its own timer module's delay.
# Never fires a relay: test with a real scan afterwards.
set -euo pipefail

ENV=/opt/facility-node/.env
SVC=facility-node
DEFAULT=5

[ "$EUID" -eq 0 ] || { echo "Run as root: facility door (or sudo bash $0)"; exit 1; }
[ -f "$ENV" ] || { echo "$ENV not found - install the node first"; exit 1; }

env_get() { grep -m1 -E "^$1=" "$ENV" | cut -d= -f2- || true; }

doors() {   # door names, as config.py reads them
    local d; d=$(env_get DOORS)
    if [ -n "$d" ]; then echo "$d" | tr ',' '\n' | tr -d ' ' | tr 'A-Z' 'a-z' | grep .
    else echo main; fi
}

unlock_of() {   # name -> seconds
    local v=""
    [ "$1" != main ] && v=$(env_get "DOOR_${1^^}_UNLOCK_SECONDS")
    [ -n "$v" ] || v=$(env_get UNLOCK_SECONDS)
    echo "${v:-$DEFAULT}"
}

show() {
    printf "  %-8s %-7s %s\n" DOOR RELAY UNLOCK
    for d in $(doors); do
        local r
        if [ "$d" = main ]; then r=$(env_get DOOR_RELAY); r=${r:-1}; else r=$(env_get "DOOR_${d^^}_RELAY"); fi
        printf "  %-8s %-7s %s s\n" "$d" "Relay0$r" "$(unlock_of "$d")"
    done
    echo "(exit buttons: the timer module's own delay)"
}

set_key() {   # key value
    ENVFILE="$ENV" KEY="$1" VAL="$2" python3 - <<'PY'
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
}

case "${1:-show}" in
    show) show ;;
    delay)
        [ $# -eq 3 ] || { echo "usage: facility door delay <door|all> <seconds 1-30>"; exit 1; }
        door="${2,,}" secs="$3"
        python3 -c "import sys; v=float(sys.argv[1]); sys.exit(0 if 1 <= v <= 30 else 1)" "$secs" 2>/dev/null \
            || { echo "unlock time must be 1-30 seconds, got '$secs'"; exit 1; }
        if [ "$door" != all ] && ! doors | grep -qx "$door"; then
            echo "unknown door '$door'; doors: $(doors | tr '\n' ' ')"; exit 1
        fi
        cp -p "$ENV" "$ENV.bak-door"
        if [ "$door" = all ] || [ "$door" = main ]; then
            set_key UNLOCK_SECONDS "$secs"
            # per-door values would override the default: set them too
            for d in $(doors); do
                [ "$d" != main ] && [ -n "$(env_get "DOOR_${d^^}_UNLOCK_SECONDS")" ] \
                    && set_key "DOOR_${d^^}_UNLOCK_SECONDS" "$secs"
            done
        else
            set_key "DOOR_${door^^}_UNLOCK_SECONDS" "$secs"
        fi
        chown root:facility "$ENV" "$ENV.bak-door"
        chmod 640 "$ENV" "$ENV.bak-door"
        echo "Saved (previous .env kept as $ENV.bak-door)"
        systemctl restart "$SVC"
        sleep 3
        if systemctl is-active -q "$SVC"; then
            journalctl -u "$SVC" -n 40 --no-pager -o cat | grep -o 'door [a-z0-9_]*: relay [0-9]*, unlock [0-9.]* s' || true
            echo "Service restarted. Test it: scan a valid QR and time the door."
        else
            echo "!! service did not start: facility logs"; exit 1
        fi
        ;;
    *) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
