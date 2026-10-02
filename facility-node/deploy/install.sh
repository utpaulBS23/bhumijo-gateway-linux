#!/bin/bash
# Facility node installer for Raspberry Pi OS (64-bit, Bookworm) on a Pi 5.
# Run from the facility-node folder:  sudo bash deploy/install.sh
set -euo pipefail

[ "$EUID" -eq 0 ] || { echo "Run as root (sudo)"; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/facility-node
DATA=/var/lib/facility
LAN_CIDR="${LAN_CIDR:-192.168.10.0/24}"

echo "== packages"
apt-get update
apt-get install -y python3 python3-venv python3-gpiozero python3-lgpio ffmpeg i2c-tools ufw

echo "== service user"
id -u facility >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin facility
usermod -aG gpio,i2c facility

echo "== code"
mkdir -p "$DEST" "$DATA/snapshots"
cp "$SRC"/*.py "$SRC"/requirements.txt "$DEST"/
# --system-site-packages: use apt's gpiozero/lgpio inside the venv
[ -d "$DEST/venv" ] || python3 -m venv --system-site-packages "$DEST/venv"
"$DEST/venv/bin/pip" install -q -r "$DEST/requirements.txt"
if [ ! -f "$DEST/.env" ]; then
    cp "$SRC/.env.example" "$DEST/.env"
    echo "!! Edit $DEST/.env (CHANGE_ME values) before starting"
fi
chown -R root:facility "$DEST"
chmod 640 "$DEST/.env"
chown -R facility:facility "$DATA"
chmod 750 "$DATA"

echo "== I2C"
raspi-config nonint do_i2c 0 || true

echo "== firewall: SSH + port 5454 from the facility LAN only"
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow from "$LAN_CIDR" to any port 5454 proto tcp
ufw --force enable

echo "== hardware watchdog (reboot if the Pi hangs)"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'CONF'
[Manager]
RuntimeWatchdogSec=15
CONF

echo "== systemd"
cp "$SRC/deploy/facility-node.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable facility-node

echo
echo "Done. Next:"
echo "  1. sudo nano $DEST/.env"
echo "  2. cd $DEST && sudo -u facility ./venv/bin/python test_door.py"
echo "  3. sudo systemctl start facility-node && journalctl -u facility-node -f"
echo "  4. Reboot once to activate the watchdog"
