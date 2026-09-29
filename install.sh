#!/bin/bash
set -e

echo "=== Bhumijo Gateway Service Installer ==="

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Error: Must run as root (use sudo)"
    exit 1
fi

# Create user
echo "Creating service user..."
if ! id -u bhumijo-gateway > /dev/null 2>&1; then
    useradd -r -s /bin/false bhumijo-gateway
    echo "✓ User created"
else
    echo "✓ User exists"
fi

# Install Python dependencies
echo "Installing Python packages..."
pip3 install -r requirements.txt

# Create directories
echo "Creating directories..."
mkdir -p /opt/bhumijo-gateway
mkdir -p /etc/bhumijo-gateway
mkdir -p /var/log/bhumijo

# Copy files
echo "Installing service files..."
cp gateway_service.py /opt/bhumijo-gateway/
chmod 755 /opt/bhumijo-gateway/gateway_service.py

cp gateway.conf /etc/bhumijo-gateway/
chmod 644 /etc/bhumijo-gateway/gateway.conf

# Copy systemd service
cp bhumijo-gateway.service /etc/systemd/system/
chmod 644 /etc/systemd/system/bhumijo-gateway.service

# Set permissions
echo "Setting permissions..."
chown -R bhumijo-gateway:bhumijo-gateway /opt/bhumijo-gateway
chown -R bhumijo-gateway:bhumijo-gateway /var/log/bhumijo
chmod 750 /var/log/bhumijo

# Reload systemd
echo "Reloading systemd..."
systemctl daemon-reload

echo ""
echo "=== Installation Complete ==="
echo ""
echo "Configuration file: /etc/bhumijo-gateway/gateway.conf"
echo "Service file: /etc/systemd/system/bhumijo-gateway.service"
echo "Log file: /var/log/bhumijo-gateway.log"
echo ""
echo "Next steps:"
echo "1. Edit configuration: sudo nano /etc/bhumijo-gateway/gateway.conf"
echo "2. Start service: sudo systemctl start bhumijo-gateway"
echo "3. Enable on boot: sudo systemctl enable bhumijo-gateway"
echo "4. Check status: sudo systemctl status bhumijo-gateway"
echo "5. View logs: sudo journalctl -u bhumijo-gateway -f"
echo ""