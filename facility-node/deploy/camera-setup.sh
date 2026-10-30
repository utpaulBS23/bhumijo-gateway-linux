#!/bin/bash
# Camera setup for the facility node. Run through the `facility` command:
#
#   facility camera            set up or change the camera (asks, tests, saves, restarts)
#   facility camera test       test the saved stream: codec, resolution, fps
#   facility camera show       saved camera settings, password hidden
#   facility camera find       look for cameras on the LAN (RTSP port + Dahua/Imou discovery)
#
# Several cameras (CAMERAS=entry,male,female in .env): add the camera's name,
#   facility camera setup male   saves CAM_MALE_HOST / _PORT / _RTSP, adds it to CAMERAS
#   facility camera test male    facility camera show   (shows every camera)
# A door's alert snapshot comes from the camera responsible for it: the admin
# panel's choice, else CAM_<NAME>_DOORS, else the camera with the door's name.
#
# The stream is tested BEFORE anything is saved. Dahua/Imou cameras lock the
# login for ~30 min after ~5 wrong passwords, and the service retries the saved
# URL every CAM_FRAME_INTERVAL, so a wrong password must never reach .env.
# The password is read hidden, %-encoded in the URL (ffmpeg decodes it), and
# never printed: every URL shown goes through hide().
set -euo pipefail

ENV=/opt/facility-node/.env
SVC=facility-node
LAN_PREFIX=192.168.10

[ "$EUID" -eq 0 ] || { echo "Run as root: facility camera (or sudo bash $0)"; exit 1; }
[ -f "$ENV" ] || { echo "$ENV not found - install the node first"; exit 1; }

hide() { sed -E 's#rtsp://[^ ]*@#rtsp://***:***@#g'; }
env_get() { grep -m1 -E "^$1=" "$ENV" | cut -d= -f2- || true; }

# Which camera: a name (CAM_<NAME>_*) or the single camera (CAM_*).
NAME=$(echo "${2:-}" | tr '[:upper:]' '[:lower:]')
CAMERAS=$(env_get CAMERAS | tr -d ' ' | tr '[:upper:]' '[:lower:]')
if [ -n "$NAME" ]; then
    [[ "$NAME" =~ ^[a-z0-9_]+$ ]] || { echo "bad camera name '$NAME' (letters, digits, _)"; exit 1; }
    P="CAM_${NAME^^}_"
elif [ -n "$CAMERAS" ] && [ "${1:-setup}" != show ] && [ "${1:-setup}" != find ]; then
    echo "This site has several cameras ($CAMERAS): name one, e.g. facility camera ${1:-setup} ${CAMERAS%%,*}"
    exit 1
else
    P="CAM_"
fi
LABEL=${NAME:-camera}

# Stream paths by brand: main stream | sub stream
PRESETS=(
    "Dahua / Imou|/cam/realmonitor?channel=1&subtype=0|/cam/realmonitor?channel=1&subtype=1"
    "Hikvision|/Streaming/Channels/101|/Streaming/Channels/102"
    "TP-Link Tapo / Vigi|/stream1|/stream2"
    "Reolink|/h264Preview_01_main|/h264Preview_01_sub"
)

# Split an RTSP URL into user, host, port, path (password left out on purpose).
url_parts() {
    URL="$1" python3 -c '
import os, urllib.parse as u
p = u.urlsplit(os.environ["URL"])
path = p.path + ("?" + p.query if p.query else "")
print(u.unquote(p.username or ""), p.hostname or "", p.port or 554, path, sep="\t")'
}

build_url() {   # user pass host port path
    U="$1" P="$2" H="$3" PORT="$4" PTH="$5" python3 -c '
import os, urllib.parse as u
e = os.environ
q = lambda s: u.quote(s, safe="!*+,;=-._~")
path = e["PTH"] if e["PTH"].startswith("/") else "/" + e["PTH"]
print("rtsp://" + q(e["U"]) + ":" + q(e["P"]) + "@" + e["H"] + ":" + e["PORT"] + path)'
}

# Test one URL: one login attempt, prints codec/resolution/fps or the reason.
probe() {
    local out rc
    out=$(timeout 20 ffprobe -v error -rtsp_transport tcp -select_streams v:0 \
            -show_entries stream=codec_name,width,height,avg_frame_rate \
            -of default=nw=1:nk=0 "$1" 2>&1) && rc=0 || rc=$?
    if [ $rc -eq 0 ] && echo "$out" | grep -q '^width='; then
        echo "$out" | awk -F= '{v[$1]=$2} END {
            split(v["avg_frame_rate"], f, "/"); fps = (f[2] > 0) ? f[1] / f[2] : f[1]
            printf "  OK: %s %sx%s @ %.0f fps\n", v["codec_name"], v["width"], v["height"], fps }'
        return 0
    fi
    echo "  FAILED:"
    if [ $rc -eq 124 ]; then echo "    no answer in 20 s"
    else echo "$out" | hide | tail -2 | sed 's/^/    /'; fi
    if echo "$out" | grep -q 401; then
        echo "  -> wrong username or password. Imou: user admin, password = Safety Code (SC)"
        echo "     on the label, or the Device Password set in the Imou app (not the app login)."
        echo "     Stop after 2-3 tries: the camera locks for ~30 min."
    elif echo "$out" | grep -q 404; then
        echo "  -> login OK but wrong stream path: pick another brand or enter the path."
    elif [ $rc -eq 124 ] || echo "$out" | grep -qiE 'refused|timed out|no route'; then
        echo "  -> camera not answering on that IP/port: check power, cable and IP (facility camera find)."
    fi
    return 1
}

save() {   # host port url
    cp -p "$ENV" "$ENV.bak-camera"
    ENVFILE="$ENV" P="$P" NAME="$NAME" H="$1" PORT="$2" URL="$3" python3 - <<'PY'
import os, re
path = os.environ["ENVFILE"]
p, name = os.environ["P"], os.environ["NAME"]
want = {p + "HOST": os.environ["H"], p + "PORT": os.environ["PORT"], p + "RTSP": os.environ["URL"]}
lines = open(path).read().splitlines()
# CAM_PORT defaults to 554; only write it when needed
if want[p + "PORT"] == "554" and not any(l.startswith(p + "PORT=") for l in lines):
    del want[p + "PORT"]
# A named camera must be listed in CAMERAS to be used
if name:
    cams = next((l.split("=", 1)[1] for l in lines if l.startswith("CAMERAS=")), "")
    names = [n.strip().lower() for n in cams.split(",") if n.strip()]
    if name not in names:
        want["CAMERAS"] = ",".join(names + [name])
for key, val in want.items():
    for i, line in enumerate(lines):
        if re.match(rf"^{key}=", line):
            lines[i] = f"{key}={val}"
            break
    else:
        lines.append(f"{key}={val}")
open(path, "w").write("\n".join(lines) + "\n")
PY
    chown root:facility "$ENV" "$ENV.bak-camera"
    chmod 640 "$ENV" "$ENV.bak-camera"
    echo "Saved $LABEL settings (previous .env kept as $ENV.bak-camera)"
    if [ -n "$NAME" ] && [ -z "$CAMERAS" ] && [ -n "$(env_get CAM_HOST)" ]; then
        echo "  Note: CAMERAS is now set, so the single camera (CAM_HOST / CAM_RTSP) is no"
        echo "  longer used. Add it under a name too: facility camera setup <name>"
    fi
}

# After a restart the service checks the camera at once; wait for that result.
wait_health() {
    local port; port=$(env_get PORT); port=${port:-5454}
    echo "Waiting for the service's camera check (up to 60 s)..."
    for _ in $(seq 12); do
        sleep 5
        st=$(curl -s -m4 "http://127.0.0.1:$port/health" | NAME="$NAME" python3 -c '
import os, sys, json
h = json.load(sys.stdin)
c = (h.get("cameras") or {}).get(os.environ["NAME"]) if os.environ["NAME"] else h.get("camera")
c = c or {}
print(c.get("reachable"), c.get("streaming"), c.get("frozen"))' 2>/dev/null) || continue
        read -r reach stream frozen <<<"$st"
        if [ "$stream" != "None" ]; then
            echo "  reachable: $reach   streaming: $stream   frozen: $frozen"
            [ "$stream" = "True" ] && return 0 || return 1
        fi
    done
    echo "  no result yet - check later with: facility health"
}

show_one() {   # prefix
    echo "${1}HOST=$(env_get "${1}HOST")"
    echo "${1}PORT=$(env_get "${1}PORT")"
    echo "${1}RTSP=$(env_get "${1}RTSP" | hide)"
}

cmd_show() {
    if [ -n "$NAME" ]; then
        show_one "$P"
    elif [ -n "$CAMERAS" ]; then
        echo "CAMERAS=$CAMERAS"
        echo "CAM_MONITOR=$(env_get CAM_MONITOR)"
        for n in ${CAMERAS//,/ }; do echo; show_one "CAM_${n^^}_"; done
        echo
        grep -E '^CAM_[A-Z0-9_]+_DOORS=' "$ENV" || echo "(no CAM_*_DOORS: each door uses the camera with its name)"
        echo "The admin panel's links win when the backend sends them: facility health -> camera_links"
    else
        show_one CAM_
    fi
    [ -f "$ENV.bak-camera" ] && echo "backup:  $ENV.bak-camera ($(date -r "$ENV.bak-camera" '+%F %H:%M'))"
    return 0
}

cmd_test() {
    local url; url=$(env_get "${P}RTSP")
    [ -n "$url" ] || { echo "${P}RTSP is empty: run  facility camera setup $NAME"; exit 1; }
    echo "Testing $(echo "$url" | hide)"
    probe "$url"
}

cmd_find() {
    echo "Looking for cameras on $LAN_PREFIX.0/24 (about 10 s)..."
    local hosts
    hosts=$(for i in $(seq 1 254); do
        ( timeout 1 bash -c "</dev/tcp/$LAN_PREFIX.$i/554" 2>/dev/null && echo "$LAN_PREFIX.$i" ) &
    done; wait)
    # Dahua/Imou answer a LAN discovery query with model and serial number.
    local info
    info=$(python3 - <<'PY' 2>/dev/null || true
import socket, struct, json, time
body = json.dumps({"method": "DHDiscover.search", "params": {"mac": "", "uni": 1}}).encode()
pkt = struct.pack("<4s4sIIIIII", b"\x20\x00\x00\x00", b"DHIP", 0, 0, len(body), 0, len(body), 0) + body
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
# Replies go to the multicast group on port 37810, so listen there.
s.bind(("", 37810)); s.settimeout(0.5)
s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
             struct.pack("4s4s", socket.inet_aton("239.255.255.251"), socket.inet_aton("0.0.0.0")))
for dst in (("239.255.255.251", 37810), ("255.255.255.255", 37810)):
    try: s.sendto(pkt, dst)
    except OSError: pass
end = time.time() + 3
while time.time() < end:
    try: data, addr = s.recvfrom(65535)
    except socket.timeout: continue
    try:   # the JSON can be followed by padding bytes
        d, _ = json.JSONDecoder().raw_decode(data[data.index(b"{"):].decode(errors="replace"))
        d = d["params"]["deviceInfo"]
    except Exception: continue
    print(addr[0], d.get("DeviceType", "?"), "SN " + d.get("SerialNo", "?"), sep="\t")
PY
)
    if [ -z "$hosts" ] && [ -z "$info" ]; then
        echo "No camera found. Check power and cable, and that it is on $LAN_PREFIX.x"
        return 1
    fi
    printf "%-16s %-19s %s\n" IP MAC "MODEL / SERIAL"
    for ip in $(printf "%s\n%s\n" "$hosts" "$(echo "$info" | cut -f1)" | grep . | sort -u -t. -k4 -n); do
        mac=$(ip neigh show "$ip" | awk '{print $5}')
        printf "%-16s %-19s %s\n" "$ip" "${mac:--}" "$(echo "$info" | awk -F'\t' -v ip="$ip" '$1==ip {print $2"  "$3}')"
    done
    echo "Tip: reserve the camera's IP for its MAC in the router so it never changes."
}

cmd_setup() {
    local cur user host port path pass url choice sub
    cur=$(env_get "${P}RTSP")
    IFS=$'\t' read -r user host port path < <(url_parts "${cur:-rtsp://admin@x/}") || true
    host=$(env_get "${P}HOST"); [ -n "$host" ] || host="$LAN_PREFIX.149"
    port=$(env_get "${P}PORT"); port=${port:-554}
    [ -n "$user" ] || user=admin
    echo "Camera setup${NAME:+ for '$NAME'}. Press Enter to keep the value in [brackets]."
    echo "Current: $(echo "${cur:-not set}" | hide)"
    echo

    read -rp "Camera IP [$host]: " v; host=${v:-$host}
    timeout 2 bash -c "</dev/tcp/$host/$port" 2>/dev/null \
        || echo "  !! nothing answers on $host:$port - check the IP (facility camera find)"
    read -rp "RTSP port [$port]: " v; port=${v:-$port}
    read -rp "Username [$user]: " v; user=${v:-$user}
    read -rsp "Password (hidden): " pass; echo
    [ -n "$pass" ] || { echo "Empty password, nothing changed."; exit 1; }

    echo "Camera brand:"
    for i in "${!PRESETS[@]}"; do echo "  $((i + 1))) ${PRESETS[$i]%%|*}"; done
    echo "  5) other - type the stream path"
    [ -n "$cur" ] && echo "  0) keep current path ($path)"
    read -rp "Choice [1]: " choice; choice=${choice:-1}
    case "$choice" in
        0) [ -n "$cur" ] || { echo "no current path"; exit 1; } ;;
        [1-4])
            IFS='|' read -r _ main subp <<<"${PRESETS[$((choice - 1))]}"
            read -rp "Stream: 1) main, full quality  2) sub, lower load [1]: " sub
            [ "${sub:-1}" = 2 ] && path=$subp || path=$main ;;
        5) read -rp "Stream path (e.g. /live/ch0): " path
           [ -n "$path" ] || { echo "Empty path, nothing changed."; exit 1; } ;;
        *) echo "Invalid choice, nothing changed."; exit 1 ;;
    esac

    url=$(build_url "$user" "$pass" "$host" "$port" "$path")
    unset pass
    echo
    echo "Testing $(echo "$url" | hide)"
    if ! probe "$url"; then
        read -rp "Save it anyway? [y/N]: " v
        [ "${v,,}" = y ] || { echo "Nothing changed."; exit 1; }
    fi
    save "$host" "$port" "$url"
    systemctl restart "$SVC"
    echo "Service restarted."
    wait_health || true
}

case "${1:-setup}" in
    setup) cmd_setup ;;
    test)  cmd_test ;;
    show)  cmd_show ;;
    find)  cmd_find ;;
    *) echo "usage: facility camera [setup|test|show|find] [camera name]"; exit 1 ;;
esac
