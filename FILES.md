# Bhumijo Gateway Service - File Structure

> **Legacy gateway.** This document covers `gateway_service.py`, the old single-door, online-only gateway.
> It stays here only until the switchover. **New installs use the facility node:** see
> [`facility-node/SETUP.md`](facility-node/SETUP.md).

## Core Service Files

### gateway_service.py (12 KB)
Main Flask HTTP server. Handles all incoming requests from QR scanners, buttons, sensors.
- Exposes HTTP endpoints on port 5454
- Controls KC868-A4S relay via HTTP
- Syncs data with Bhumijo backend server
- Implements relay, QR, service button, and air quality handlers

**Key Classes:**
- `KC868A4S` - Relay controller (set/get status)
- `BhumijoClient` - Backend server communication
- `Config` - Configuration loader

**Endpoints:**
- `/health` - Health check
- `/relay/status` - Get relay status
- `/relay/control` - Control relay
- `/qrscanner` - QR scan handler
- `/service` - Service button handler
- `/airquality` - Environmental data handler
- `/manager` - Event manager handler
- `/wifibutton` - WiFi button handler
- `/rakindaqrscanner` - Rakinda QR handler

---

## Configuration Files

### gateway.conf (241 bytes)
Service configuration file. Defines relay IP, password, backend server.

**Variables:**
```
RELAY_IP=192.168.1.100              # KC868-A4S IP
RELAY_PASSWORD=12345                 # Relay password
SERVER_URL=http://localhost:8000     # Backend server
DEVICE_TOKEN=gateway-device-token    # Device ID
GATEWAY_PORT=5454                    # HTTP port
```

**Location After Install:** `/etc/bhumijo-gateway/gateway.conf`

---

## Installation & Deployment

### install.sh (1.8 KB)
Automated installer script. Creates user, directories, installs dependencies.

**Usage:**
```bash
sudo bash install.sh
```

**What it does:**
1. Creates `bhumijo-gateway` user
2. Installs Python dependencies (flask, requests, etc.)
3. Creates `/opt/bhumijo-gateway`, `/etc/bhumijo-gateway`, `/var/log/bhumijo`
4. Copies service files to system locations
5. Sets proper permissions and ownership
6. Reloads systemd

### bhumijo-gateway.service (704 bytes)
Systemd unit file. Runs gateway as service.

**Features:**
- Auto-restart on failure
- Security hardening (ProtectSystem, NoNewPrivileges)
- Standard output to journal
- Logging to syslog

**Location After Install:** `/etc/systemd/system/bhumijo-gateway.service`

### requirements.txt (67 bytes)
Python package dependencies.

**Packages:**
- flask==2.3.3
- requests==2.31.0
- gunicorn==21.2.0
- python-dotenv==1.0.0

---

## Docker Files

### Dockerfile (663 bytes)
Docker image definition. Runs service in container.

**Features:**
- Python 3.11 slim base
- Non-root user (gateway:1000)
- Health check endpoint
- Environment variables for config

**Build & Run:**
```bash
docker build -t bhumijo-gateway .
docker run -e RELAY_IP=192.168.1.100 -p 5454:5454 bhumijo-gateway
```

### docker-compose.yml (623 bytes)
Docker Compose configuration. Orchestrates container.

**Features:**
- Service definition
- Port mapping (5454:5454)
- Environment variables
- Auto-restart policy
- Health checks
- Logging configuration

**Usage:**
```bash
docker-compose up -d
```

---

## Testing & Monitoring

### test_gateway.sh (3.3 KB)
Comprehensive test suite. Tests all endpoints and functionality.

**Tests:**
- Health check
- Relay control (on/off/pulse)
- QR scanner (GET & POST)
- Service buttons
- WiFi buttons
- Air quality data
- Manager events
- Rakinda QR scanner
- 404 error handling

**Usage:**
```bash
bash test_gateway.sh
```

**Output:**
- Test count and pass/fail status
- HTTP status codes
- Response bodies
- Color-coded results

### gateway_admin.py (8.7 KB)
Admin CLI tool. Manages service and relay control.

**Commands:**
```
status              # Show gateway status
relay-on <1-4>      # Turn relay ON
relay-off <1-4>     # Turn relay OFF
relay-pulse <1-4>   # Momentary pulse
test-qr [CARD_ID]   # Test QR scan
test-air            # Test air quality
logs [LINES]        # Show logs
start/stop/restart  # Control service
config              # Show configuration
```

**Features:**
- Color-coded output
- Error handling
- Systemctl integration
- JSON data display

**Usage:**
```bash
python3 gateway_admin.py status
python3 gateway_admin.py relay-on 1
```

### Makefile (1.8 KB)
Convenient make commands for common operations.

**Targets:**
```
make install        # Install service
make start          # Start service
make stop           # Stop service
make status         # Check status
make logs           # Follow logs
make test           # Run tests
make docker-up      # Start Docker
make docker-down    # Stop Docker
```

**Usage:**
```bash
make install
make start
make test
```

---

## Documentation

### README.md (4.0 KB)
Complete project documentation. Features, installation, API endpoints, troubleshooting.

**Sections:**
- Features overview
- Installation methods
- Configuration guide
- API endpoint documentation
- Testing procedures
- Troubleshooting guide
- Architecture overview

### QUICKSTART.md (2.7 KB)
Quick reference for getting started. Fastest path to running service.

**Covers:**
- Bare metal installation (3 steps)
- Docker setup (3 steps)
- Systemctl commands
- Testing procedures
- Common issues
- File locations

### SETUP.md (8.6 KB)
Comprehensive setup and deployment guide. Production ready.

**Sections:**
- Complete architecture diagram
- Component overview
- 3 installation methods (bare metal, Docker, manual)
- Detailed configuration
- Service management
- All API endpoints
- Extensive troubleshooting
- Monitoring setup
- Firewall rules
- Production checklist

### FILES.md (this file)
Documentation of all files and their purposes.

---

## Directory Structure

```
bhumijo-gateway-service/
├── Core Service
│   ├── gateway_service.py         # Main Flask app
│   ├── requirements.txt           # Python dependencies
│   └── gateway.conf              # Configuration template
│
├── Installation & Deployment
│   ├── install.sh                # Automated installer
│   ├── bhumijo-gateway.service  # Systemd unit
│   ├── Dockerfile               # Docker image
│   └── docker-compose.yml       # Docker Compose config
│
├── Testing & Admin
│   ├── test_gateway.sh          # Test suite
│   ├── gateway_admin.py         # Admin CLI
│   └── Makefile                 # Build automation
│
└── Documentation
    ├── README.md                # Project docs
    ├── QUICKSTART.md           # Quick reference
    ├── SETUP.md                # Complete setup guide
    └── FILES.md                # This file
```

---

## Installation Paths

### After Running install.sh:

```
/opt/bhumijo-gateway/
├── gateway_service.py
├── requirements.txt
└── [other files copied]

/etc/bhumijo-gateway/
├── gateway.conf
└── [configuration]

/etc/systemd/system/
└── bhumijo-gateway.service

/var/log/bhumijo/
└── [logs]
```

---

## Systemd Service

**Start:**
```bash
sudo systemctl start bhumijo-gateway
```

**Stop:**
```bash
sudo systemctl stop bhumijo-gateway
```

**Restart:**
```bash
sudo systemctl restart bhumijo-gateway
```

**Enable (boot):**
```bash
sudo systemctl enable bhumijo-gateway
```

**Status:**
```bash
sudo systemctl status bhumijo-gateway
```

**Logs:**
```bash
sudo journalctl -u bhumijo-gateway -f
```

---

## Quick Start

1. **Extract/Clone**
   ```bash
   git clone <repo> bhumijo-gateway
   cd bhumijo-gateway
   ```

2. **Install**
   ```bash
   sudo bash install.sh
   ```

3. **Configure**
   ```bash
   sudo nano /etc/bhumijo-gateway/gateway.conf
   ```

4. **Start**
   ```bash
   sudo systemctl start bhumijo-gateway
   ```

5. **Verify**
   ```bash
   curl http://localhost:5454/health
   bash test_gateway.sh
   ```

---

## Docker Quick Start

1. **Configure**
   ```bash
   cp gateway.conf .env
   nano .env
   ```

2. **Run**
   ```bash
   docker-compose up -d
   ```

3. **Test**
   ```bash
   curl http://localhost:5454/health
   ```

---

## File Sizes

| File | Size | Purpose |
|------|------|---------|
| gateway_service.py | 12 KB | Main service |
| test_gateway.sh | 3.3 KB | Test suite |
| gateway_admin.py | 8.7 KB | Admin CLI |
| SETUP.md | 8.6 KB | Setup guide |
| README.md | 4.0 KB | Documentation |
| Dockerfile | 663 B | Docker image |
| bhumijo-gateway.service | 704 B | Systemd unit |
| docker-compose.yml | 623 B | Docker Compose |
| QUICKSTART.md | 2.7 KB | Quick reference |
| Makefile | 1.8 KB | Build automation |
| install.sh | 1.8 KB | Installer |
| requirements.txt | 67 B | Dependencies |
| gateway.conf | 241 B | Configuration |

**Total: ~47 KB**

---

## Permissions After Install

```
/opt/bhumijo-gateway/
  - Owner: bhumijo-gateway:bhumijo-gateway
  - Mode: 755 (rwxr-xr-x)

/etc/bhumijo-gateway/
  - Owner: bhumijo-gateway:bhumijo-gateway
  - Mode: 644 (rw-r--r--)

/var/log/bhumijo-gateway.log
  - Owner: bhumijo-gateway:bhumijo-gateway
  - Mode: 644 (rw-r--r--)
```

---

## Support

**Quick Check:**
```bash
curl http://localhost:5454/health
```

**Full Test:**
```bash
bash test_gateway.sh
```

**Admin Check:**
```bash
python3 gateway_admin.py status
```

**View Logs:**
```bash
sudo journalctl -u bhumijo-gateway -f
```

---

End of FILES.md