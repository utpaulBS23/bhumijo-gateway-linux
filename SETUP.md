# Bhumijo Gateway Service - Complete Setup Guide

## Overview

Linux implementation of Bhumijo Gateway Service. Acts as a bridge between QR scanners, WiFi buttons, environmental sensors, and KC868-A4S relay controllers, syncing with Bhumijo backend server.

## Architecture

```
┌─────────────┐
│ QR Scanner  │
└──────┬──────┘
       │
       ├─────────────────────────┐
       │                         │
    ┌──▼──────────────────────┐ │
    │  Bhumijo Gateway        │ │
    │  (Linux Service)        │ │
    │  Port: 5454             │ │
    └──┬──────────────────────┘ │
       │                         │
    ┌──▼──────────────┐    ┌────▼────────────┐
    │ KC868-A4S Relay │    │ Bhumijo Server  │
    │ (4 Relays)      │    │ (Backend API)   │
    └─────────────────┘    └─────────────────┘
       │
    ┌──▼──────────────┐
    │ WiFi Buttons    │
    │ Environment     │
    │ Sensors         │
    └─────────────────┘
```

## Components

### 1. Main Service
- **File**: `gateway_service.py`
- **Type**: Flask HTTP server
- **Port**: 5454
- **Function**: Receives requests, controls relays, syncs with backend

### 2. Configuration
- **File**: `gateway.conf`
- **Location**: `/etc/bhumijo-gateway/gateway.conf`
- **Contains**: Relay IP, backend URL, device token

### 3. Systemd Unit
- **File**: `bhumijo-gateway.service`
- **Location**: `/etc/systemd/system/bhumijo-gateway.service`
- **Runs as**: User `bhumijo-gateway`

### 4. Admin CLI
- **File**: `gateway_admin.py`
- **Purpose**: Manage service, control relays, view logs

### 5. Testing
- **File**: `test_gateway.sh`
- **Purpose**: Comprehensive endpoint testing

## Installation Methods

### Method 1: Bare Metal (Recommended)

#### Prerequisites
```bash
sudo apt update
sudo apt install -y python3 python3-pip curl
```

#### Install
```bash
# Download/clone
git clone https://github.com/Bhumijo/bhumijo-gateway-linux.git
cd bhumijo-gateway-linux

# Run installer
sudo bash install.sh

# Configure
sudo nano /etc/bhumijo-gateway/gateway.conf

# Start
sudo systemctl start bhumijo-gateway
sudo systemctl enable bhumijo-gateway
```

### Method 2: Docker

#### Prerequisites
```bash
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER
```

#### Install
```bash
git clone https://github.com/Bhumijo/bhumijo-gateway-linux.git
cd bhumijo-gateway-linux

# Configure environment
cp gateway.conf .env
nano .env  # Edit settings

# Start
docker-compose up -d
docker-compose logs -f
```

### Method 3: Manual

```bash
# 1. Create user
sudo useradd -r -s /bin/false bhumijo-gateway

# 2. Create directories
sudo mkdir -p /opt/bhumijo-gateway
sudo mkdir -p /etc/bhumijo-gateway
sudo mkdir -p /var/log/bhumijo

# 3. Install dependencies
pip3 install flask requests gunicorn

# 4. Copy files
sudo cp gateway_service.py /opt/bhumijo-gateway/
sudo cp gateway.conf /etc/bhumijo-gateway/
sudo cp bhumijo-gateway.service /etc/systemd/system/

# 5. Set permissions
sudo chown -R bhumijo-gateway:bhumijo-gateway /opt/bhumijo-gateway
sudo chown -R bhumijo-gateway:bhumijo-gateway /var/log/bhumijo
chmod 755 /opt/bhumijo-gateway/gateway_service.py

# 6. Enable
sudo systemctl daemon-reload
sudo systemctl enable bhumijo-gateway
sudo systemctl start bhumijo-gateway
```

## Configuration

### Edit Config
```bash
sudo nano /etc/bhumijo-gateway/gateway.conf
```

### Key Settings

| Variable | Description | Example |
|----------|-------------|---------|
| `RELAY_IP` | KC868-A4S IP | `192.168.1.100` |
| `RELAY_PASSWORD` | Relay password | `12345` |
| `SERVER_URL` | Backend server | `http://localhost:8000` |
| `DEVICE_TOKEN` | Device ID | `gateway-123` |
| `GATEWAY_PORT` | Listen port | `5454` |

### Example Config
```
RELAY_IP=192.168.1.100
RELAY_PASSWORD=12345
SERVER_URL=http://api.bhumijo.local:8000
DEVICE_TOKEN=building-a-gateway
GATEWAY_PORT=5454
```

## Service Management

### Using Systemctl
```bash
# Start/stop/restart
sudo systemctl start bhumijo-gateway
sudo systemctl stop bhumijo-gateway
sudo systemctl restart bhumijo-gateway

# Check status
sudo systemctl status bhumijo-gateway

# Enable/disable at boot
sudo systemctl enable bhumijo-gateway
sudo systemctl disable bhumijo-gateway

# View logs
sudo journalctl -u bhumijo-gateway -f
```

### Using Make
```bash
make start      # Start service
make stop       # Stop service
make restart    # Restart service
make status     # Check status
make logs       # View logs
```

### Using Admin CLI
```bash
python3 gateway_admin.py status
python3 gateway_admin.py start
python3 gateway_admin.py stop
python3 gateway_admin.py logs 100
```

## API Endpoints

### Health Check
```bash
GET /health
```
Response: `{"status": "healthy", "relay_connected": true, ...}`

### Relay Status
```bash
GET /relay/status
```

### Relay Control
```bash
POST /relay/control
{"relay": 1, "state": 1, "duration": 3600}
```

### QR Scanner
```bash
GET /qrscanner?cardid=ABC123&cjihao=X&mjihao=1&status=1&time=2024-01-01
POST /qrscanner
```

### Service Button
```bash
GET /service?button_id=1&gender=male
POST /service
```

### Air Quality
```bash
POST /airquality
{"air_quality": 50, "temperature": 25.5, "humidity": 60, "section": "building_a"}
```

### Manager Event
```bash
POST /manager
{"card_id": "ABC123", "gender": "male", "button_id": 1}
```

### WiFi Button
```bash
POST /wifibutton
{"button_id": 1}
```

## Testing

### Quick Test
```bash
curl http://localhost:5454/health
```

### Full Test Suite
```bash
bash test_gateway.sh
```

### Admin CLI
```bash
python3 gateway_admin.py status
python3 gateway_admin.py relay-pulse 1 2
python3 gateway_admin.py test-qr ABC123
```

## Troubleshooting

### Service won't start
```bash
# Check logs
sudo journalctl -u bhumijo-gateway -n 50

# Check syntax
python3 gateway_service.py  # Run directly to see errors
```

### Relay not responding
```bash
# Ping relay
ping 192.168.1.100

# Test direct connection
curl http://192.168.1.100/input.cgi

# Check configuration
cat /etc/bhumijo-gateway/gateway.conf
```

### Port already in use
```bash
# Find process using port
sudo lsof -i :5454

# Kill if necessary
sudo kill -9 <PID>

# Or change GATEWAY_PORT in config
```

### Backend not accessible
```bash
# Test connectivity
curl -v http://localhost:8000/api/user/accesses

# Check SERVER_URL in config
sudo nano /etc/bhumijo-gateway/gateway.conf
```

### Permission denied
```bash
# Fix ownership
sudo chown -R bhumijo-gateway:bhumijo-gateway /opt/bhumijo-gateway
sudo chown -R bhumijo-gateway:bhumijo-gateway /var/log/bhumijo

# Fix permissions
chmod 755 /opt/bhumijo-gateway/gateway_service.py
chmod 644 /etc/bhumijo-gateway/gateway.conf
```

## Monitoring

### Real-time Logs
```bash
sudo journalctl -u bhumijo-gateway -f
```

### Log File
```bash
tail -f /var/log/bhumijo-gateway.log
```

### Metrics
```bash
python3 gateway_admin.py status
```

### Health Endpoint
```bash
curl http://localhost:5454/health
```

## Firewall Rules

### Allow Port 5454
```bash
# UFW
sudo ufw allow 5454

# Firewalld
sudo firewall-cmd --permanent --add-port=5454/tcp
sudo firewall-cmd --reload
```

## Upgrade

```bash
cd /opt/bhumijo-gateway
sudo git pull origin main
sudo systemctl restart bhumijo-gateway
```

## Uninstall

```bash
# Using make
make uninstall

# Manual
sudo systemctl stop bhumijo-gateway
sudo systemctl disable bhumijo-gateway
sudo rm -rf /opt/bhumijo-gateway
sudo rm -rf /etc/bhumijo-gateway
sudo rm -f /etc/systemd/system/bhumijo-gateway.service
sudo systemctl daemon-reload
sudo userdel bhumijo-gateway
```

## File Locations

```
/opt/bhumijo-gateway/
├── gateway_service.py
├── requirements.txt
├── Dockerfile
└── docker-compose.yml

/etc/bhumijo-gateway/
└── gateway.conf

/etc/systemd/system/
└── bhumijo-gateway.service

/var/log/bhumijo-gateway.log
```

## Production Checklist

- [ ] Configure RELAY_IP (KC868-A4S)
- [ ] Configure RELAY_PASSWORD
- [ ] Configure SERVER_URL (backend)
- [ ] Configure DEVICE_TOKEN
- [ ] Test relay connection
- [ ] Test backend connection
- [ ] Run test_gateway.sh
- [ ] Enable systemd auto-start
- [ ] Configure firewall
- [ ] Set up log rotation
- [ ] Monitor with journalctl

## Support

- Logs: `sudo journalctl -u bhumijo-gateway`
- Tests: `bash test_gateway.sh`
- Admin: `python3 gateway_admin.py --help`