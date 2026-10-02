# Bhumijo Facility System

| Folder / file | What it is |
|---|---|
| **[`facility-node/`](facility-node/)** | **Current system.** Offline-first node for one Raspberry Pi 5 per facility: two doors (male/female), QR + Facility-app unlock, exit tracking, reed alerts, sensors, camera health. **Setup: [`facility-node/SETUP.md`](facility-node/SETUP.md)** |
| `facility-node/firmware/kc868/` | Patched KC868-A4S relay firmware (required by the node) |
| `gateway_service.py`, `gateway_admin.py`, `install.sh`, `gateway.conf`, Docker files | Legacy single-door gateway, kept until the switchover |
| `FLUTTER_*.md` | Facility (attendant) app design, updated for the node's `POST /facility/open` |

### Facility network (per site)

| Device | IP |
|---|---|
| Router | 192.168.10.100 |
| QR scanner, male / female | 192.168.10.101 / .102 |
| Raspberry Pi 5 (facility node) | 192.168.10.104:5454 |
| KC868-A4S relay | 192.168.10.174 |
| IP camera | 192.168.10.180 |

---

## Legacy: Bhumijo Gateway Service (Linux)

> **Legacy gateway.** This section covers `gateway_service.py`, the old single-door, online-only gateway.
> It stays here only until the switchover. **New installs use the facility node:** see
> [`facility-node/SETUP.md`](facility-node/SETUP.md).


Linux implementation of Bhumijo Gateway. Runs as systemd service, controls KC868-A4S relay, exposes HTTP API for QR scanners, WiFi buttons, and environmental sensors.

## Features

- **Relay Control**: KC868-A4S 4-relay controller
- **HTTP API**: Port 5454 endpoints matching Android gateway
- **Backend Sync**: Posts to Bhumijo server
- **QR Scanner Support**: Card-based access control
- **Environmental Data**: Temperature, humidity, air quality logging
- **Service Buttons**: WiFi button event handling
- **Logging**: Systemd journal + file logging
- **Health Checks**: `/health` endpoint

## Requirements

- Linux (Ubuntu 20.04+, Debian 11+)
- Python 3.9+
- KC868-A4S relay controller on network
- Bhumijo backend server accessible

## Installation

```bash
sudo bash install.sh
```

## Configuration

Edit `/etc/bhumijo-gateway/gateway.conf`:

```
RELAY_IP=192.168.1.100          # KC868-A4S IP
RELAY_PASSWORD=12345             # Relay password
SERVER_URL=http://localhost:8000  # Backend server
DEVICE_TOKEN=gateway-token-123    # Device ID
GATEWAY_PORT=5454                 # Listen port
```

## Usage

### Start Service
```bash
sudo systemctl start bhumijo-gateway
sudo systemctl enable bhumijo-gateway  # Boot on startup
```

### Check Status
```bash
sudo systemctl status bhumijo-gateway
sudo journalctl -u bhumijo-gateway -f  # Follow logs
```

### Stop Service
```bash
sudo systemctl stop bhumijo-gateway
```

## API Endpoints

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

### Manager Event
```bash
POST /manager
{"card_id": "ABC123", "gender": "male", "button_id": 1}
```

### Environmental Quality
```bash
POST /airquality
{"air_quality": 50, "temperature": 25.5, "humidity": 60, "section": "building_a"}
```

### WiFi Button
```bash
POST /wifibutton
{"button_id": 1}
```

### Rakinda QR Scanner
```bash
POST /rakindaqrscanner
{"SCode": "ABC123"}
```

### Relay Status
```bash
GET /relay/status
```

Response:
```json
{
  "success": true,
  "relay_count": 4,
  "relays": {
    "1": true,
    "2": false,
    "3": false,
    "4": false
  }
}
```

### Relay Control
```bash
POST /relay/control
{"relay": 1, "state": 1, "duration": 3600}
```

Parameters:
- `relay`: 1-4
- `state`: 1=ON, 0=OFF
- `duration`: seconds (for timing)

### Health Check
```bash
GET /health
```

## Testing

### Check relay status
```bash
curl http://localhost:5454/relay/status
```

### Turn on relay 1
```bash
curl -X POST http://localhost:5454/relay/control \
  -H "Content-Type: application/json" \
  -d '{"relay": 1, "state": 1}'
```

### Test QR scan
```bash
curl "http://localhost:5454/qrscanner?cardid=TEST123&cjihao=X&mjihao=1&status=1&time=2024-01-01"
```

### Post air quality
```bash
curl -X POST http://localhost:5454/airquality \
  -H "Content-Type: application/json" \
  -d '{"air_quality": 50, "temperature": 25.5, "humidity": 60, "section": "building_a"}'
```

## Logs

View logs:
```bash
sudo journalctl -u bhumijo-gateway -f
```

Log file:
```bash
tail -f /var/log/bhumijo-gateway.log
```

## Troubleshooting

### Service won't start
```bash
sudo journalctl -u bhumijo-gateway -n 50
```

### Relay not responding
```bash
ping 192.168.1.100  # Check network
curl http://192.168.1.100/input.cgi  # Test direct
```

### Backend not responding
Check `SERVER_URL` in config, test connectivity

### Permission denied
```bash
sudo chown -R bhumijo-gateway:bhumijo-gateway /opt/bhumijo-gateway
```

## Architecture

```
QR Scanner → Gateway Service → Relay Controller
   ↓
WiFi Button
   ↓
Environment Sensor ↓ → Bhumijo Server
   ↓
Manager System
```

## Files

- `/opt/bhumijo-gateway/gateway_service.py` - Main service
- `/etc/bhumijo-gateway/gateway.conf` - Configuration
- `/etc/systemd/system/bhumijo-gateway.service` - Systemd unit
- `/var/log/bhumijo-gateway.log` - Application log

## Updates

```bash
cd /opt/bhumijo-gateway
git pull origin main
sudo systemctl restart bhumijo-gateway
```

## License

Bhumijo