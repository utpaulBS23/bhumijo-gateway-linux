#!/bin/bash
# Remove the facility node from this Pi.
#   sudo bash deploy/uninstall.sh            # keeps data (/var/lib/facility) and .env backup
#   sudo bash deploy/uninstall.sh --purge    # also deletes the database, snapshots and QR images
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Run as root (sudo)"; exit 1; }

PURGE=0; [ "${1:-}" = "--purge" ] && PURGE=1

systemctl disable --now facility-node 2>/dev/null || true
rm -f /etc/systemd/system/facility-node.service /usr/local/bin/facility
systemctl daemon-reload

if [ -f /opt/facility-node/.env ]; then
    cp /opt/facility-node/.env "/root/facility-node.env.$(date +%Y%m%d%H%M%S)"
    echo "saved .env copy in /root/"
fi
rm -rf /opt/facility-node

if [ "$PURGE" = 1 ]; then
    rm -rf /var/lib/facility
    echo "deleted /var/lib/facility"
else
    echo "kept /var/lib/facility (database, snapshots, QR images); use --purge to delete"
fi

echo "Left in place: the watchdog setting (/etc/systemd/system.conf.d/watchdog.conf),"
echo "the firewall rules (sudo ufw status), and the 'facility' system user."
echo "facility-node removed."
