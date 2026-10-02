#!/bin/bash
# Facility node installer / updater for Raspberry Pi OS (64-bit, Bookworm) on a Pi 5.
#
#   cd facility-node && sudo bash deploy/install.sh
#
# Safe to re-run: that's how you update. Keeps .env, the database, snapshots
# and QR codes. Set LAN_CIDR to change which network may reach port 5454.
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "Run as root: sudo bash deploy/install.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/facility-node
DATA=/var/lib/facility
LAN_CIDR="${LAN_CIDR:-192.168.10.0/24}"
FIRST_INSTALL=0; [ -f "$DEST/.env" ] || FIRST_INSTALL=1

step() { echo; echo "== $*"; }

step "checks"
[ "$(uname -m)" = "aarch64" ] || echo "!! not 64-bit ARM ($(uname -m)); expected Raspberry Pi OS 64-bit"
grep -q "bookworm\|trixie" /etc/os-release || echo "!! not Raspberry Pi OS Bookworm; packages may differ"
grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null \
    || echo "!! not a Raspberry Pi 5 ($(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown))"
[ -f "$SRC/app.py" ] || { echo "run from the facility-node folder"; exit 1; }

step "packages"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-gpiozero python3-lgpio \
    ffmpeg i2c-tools ufw curl less

step "service user"
id -u facility >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin facility
usermod -aG gpio,i2c facility

step "code -> $DEST"
systemctl stop facility-node 2>/dev/null || true
mkdir -p "$DEST/docs" "$DATA/snapshots" "$DATA/qr"
cp "$SRC"/*.py "$SRC"/requirements.txt "$SRC"/.env.example "$DEST"/
cp "$SRC"/README.md "$SRC"/INSTALL.md "$SRC"/SETUP.md "$DEST/docs/"
rm -rf "$DEST/dev" "$DEST/firmware" "$DEST/deploy"
cp -r "$SRC/dev" "$SRC/firmware" "$SRC/deploy" "$DEST"/
rm -f "$DEST/firmware/kc868/board_secrets.py"   # never ship a board password
echo "$SRC" > "$DEST/SOURCE"   # where `facility update` pulls from
if git -C "$SRC" rev-parse --short HEAD >/dev/null 2>&1; then
    echo "$(git -C "$SRC" describe --always --dirty) ($(date -u +%Y-%m-%dT%H:%MZ))" > "$DEST/VERSION"
else
    echo "unversioned copy ($(date -u +%Y-%m-%dT%H:%MZ))" > "$DEST/VERSION"
fi

step "python environment"
# --system-site-packages: use apt's gpiozero/lgpio inside the venv
[ -d "$DEST/venv" ] || python3 -m venv --system-site-packages "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"

step "configuration"
if [ "$FIRST_INSTALL" = 1 ]; then
    cp "$SRC/.env.example" "$DEST/.env"
    echo "created $DEST/.env from the template"
else
    echo "kept existing $DEST/.env"
    # Report settings that are new in this version
    missing=$(comm -23 <(grep -oE '^[A-Z0-9_]+' "$DEST/.env.example" | sort -u) \
                       <(grep -oE '^[A-Z0-9_]+' "$DEST/.env" | sort -u) || true)
    [ -n "$missing" ] && echo "new optional settings (see .env.example): $(echo $missing)"
fi
chown -R root:facility "$DEST"
chmod 640 "$DEST/.env"
chown -R facility:facility "$DATA"
chmod 750 "$DATA"

step "facility command"
install -m 755 "$SRC/deploy/facility" /usr/local/bin/facility

step "I2C"
raspi-config nonint do_i2c 0 || echo "!! enable I2C manually: sudo raspi-config -> Interface Options"

step "firewall: SSH + port 5454 from $LAN_CIDR only"
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow from "$LAN_CIDR" to any port 5454 proto tcp
ufw --force enable

step "hardware watchdog (reboots the Pi if it hangs)"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'CONF'
[Manager]
RuntimeWatchdogSec=15
CONF

step "systemd service"
cp "$SRC/deploy/facility-node.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable facility-node

echo
echo "Installed $(cat "$DEST/VERSION")"
if git -C "$SRC" rev-parse >/dev/null 2>&1 \
        && [ "$(git -C "$SRC" config core.hooksPath || true)" != "facility-node/deploy/githooks" ]; then
    echo "Tip: sudo bash $SRC/deploy/bootstrap.sh  -> every 'git pull' then reinstalls automatically"
fi
if [ "$FIRST_INSTALL" = 1 ] || grep -q CHANGE_ME "$DEST/.env"; then
    cat <<NEXT

Next:
  1. facility config      # fill in every CHANGE_ME, save -> service starts
  2. facility doctor      # everything should be ✓
  3. facility health
  4. sudo reboot          # once, to switch on the watchdog
Docs on this Pi: facility docs install | setup | readme
NEXT
else
    systemctl start facility-node
    echo "Service restarted. Check: facility doctor"
fi
