# Quick Start

## Bare Metal (Ubuntu/Debian)

```bash
# 1. Clone/download service
cd /tmp
git clone <repo> bhumijo-gateway-service
cd bhumijo-gateway-service

# 2. Install
sudo bash install.sh

# 3. Configure
sudo nano /etc/bhumijo-gateway/gateway.conf
# Edit RELAY_IP, SERVER_URL, etc

# 4. Start
sudo systemctl start bhumijo-gateway
sudo systemctl enable bhumijo-gateway

# 5. Verify
sudo systemctl status bhumijo-gateway
curl http://localhost:5454/health
```

## Docker

```bash
# 1. Clone
git clone <repo> bhumijo-gateway-service
cd bhumijo-gateway-service

# 2. Configure
cp gateway.conf .env
nano .env  # Edit variables

# 3. Run
docker-compose up -d

# 4. Verify
curl http://localhost:5454/health
docker-compose logs -f gateway
```

## systemd Commands

```bash
# Start/stop
sudo systemctl start bhumijo-gateway
sudo systemctl stop bhumijo-gateway
sudo systemctl restart bhumijo-gateway

# Status
sudo systemctl status bhumijo-gateway
sudo systemctl is-active bhumijo-gateway

# Logs
sudo journalctl -u bhumijo-gateway -f          # Follow
sudo journalctl -u bhumijo-gateway --since "5 min ago"

# Boot on startup
sudo systemctl enable bhumijo-gateway
sudo systemctl disable bhumijo-gateway
```

## Test

```bash
bash test_gateway.sh
```

## Common Issues

**Port already in use:**
```bash
sudo lsof -i :5454
sudo netstat -tlnp | grep 5454
```

**Can't connect to relay:**
```bash
ping 192.168.1.100
curl http://192.168.1.100/input.cgi
```

**Backend unreachable:**
Check SERVER_URL, verify network connectivity

**Permission denied:**
```bash
sudo usermod -aG bhumijo-gateway $USER
sudo chown -R bhumijo-gateway:bhumijo-gateway /opt/bhumijo-gateway
```

## File Locations

| File | Location |
|------|----------|
| Service | `/opt/bhumijo-gateway/gateway_service.py` |
| Config | `/etc/bhumijo-gateway/gateway.conf` |
| Systemd | `/etc/systemd/system/bhumijo-gateway.service` |
| Logs | `/var/log/bhumijo-gateway.log` |
| Journal | `journalctl -u bhumijo-gateway` |

## Configuration

Key variables in `/etc/bhumijo-gateway/gateway.conf`:

```
RELAY_IP=192.168.1.100              # KC868-A4S IP address
RELAY_PASSWORD=12345                 # Relay password (default)
SERVER_URL=http://localhost:8000     # Bhumijo backend
DEVICE_TOKEN=gateway-device-token    # Device ID for tracking
GATEWAY_PORT=5454                    # HTTP server port
```

## API Quick Test

```bash
# Health
curl http://localhost:5454/health

# Relay status
curl http://localhost:5454/relay/status

# Turn on relay 1
curl -X POST http://localhost:5454/relay/control \
  -H "Content-Type: application/json" \
  -d '{"relay": 1, "state": 1}'

# QR scan
curl "http://localhost:5454/qrscanner?cardid=TEST&cjihao=X&mjihao=1&status=1&time=2024-01-01"
```