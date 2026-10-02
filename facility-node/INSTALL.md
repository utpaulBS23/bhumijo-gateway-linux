# Raspberry Pi Installation Guide

Install the facility node on a Raspberry Pi 5: from an empty SD card to a running, self-checking service. Every command here can be copied and pasted.

> **Scope.** This guide covers the **Pi software**. Wiring, KC868 firmware, QR scanners and site commissioning are in **[SETUP.md](SETUP.md)**. For how the system works, see **[README.md](README.md)**.
> Once installed, all three docs are also on the Pi: `facility docs install | setup | readme`.

| | |
|---|---|
| Time | About 30 min (most of it is the OS update) |
| You need | Pi 5 (4 GB), endurance microSD (32 GB), 5V/5A USB-C supply, Ethernet cable, a laptop on the same network |
| From the backend team | `FACILITY_ID`, `ADMIN_URL`, `ADMIN_KEY` (and the API paths / auth header if not the defaults) |
| From the site | KC868 board password (`POST_PASSWORD`), camera RTSP URL |

---

## 1. Flash the SD card

On your laptop, install **Raspberry Pi Imager** (raspberrypi.com/software), then:

1. **Device:** Raspberry Pi 5
2. **OS:** Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)**
3. **Storage:** the microSD card
4. **Edit settings** (when asked "apply OS customisation?"):

| Tab | Setting | Value |
|---|---|---|
| General | Hostname | `facility-<id>`, e.g. `facility-001` |
| General | Username / password | `pi` / a strong password (write it down) |
| General | Wireless LAN | leave **off** (use Ethernet) |
| General | Locale | your timezone, e.g. `Asia/Dhaka` |
| Services | Enable SSH | ✓ (public-key only, if you have a key) |

5. **Write**, wait for verification, put the card in the Pi.

---

## 2. First boot and log in

1. Connect the Pi's Ethernet to the facility router and power it on. Wait about 2 minutes.
2. From your laptop (on the same network):

```bash
ssh pi@facility-001.local
```

If `.local` doesn't resolve, find the Pi's address in the router's DHCP client list and run `ssh pi@<that-ip>`.

✅ **You see a `pi@facility-001:~ $` prompt.**

---

## 3. Static IP: 192.168.10.104

Use **one** of these methods.

**Option A (recommended): router reservation.** In the router, reserve `192.168.10.104` for the Pi's MAC address, which you can see with `ip link show eth0`. Then reboot the Pi.

**Option B: set it on the Pi.**

```bash
sudo nmcli con mod "Wired connection 1" ipv4.method manual \
  ipv4.addresses 192.168.10.104/24 ipv4.gateway 192.168.10.100 ipv4.dns 192.168.10.100
sudo nmcli con up "Wired connection 1"
```

Your SSH session drops when the address changes. Reconnect with:

```bash
ssh pi@192.168.10.104
```

✅ **Check:**

```bash
hostname -I                 # 192.168.10.104
ping -c2 192.168.10.100     # router replies
ping -c2 8.8.8.8            # internet works (needed for the install)
```

---

## 4. Update the OS

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Reconnect after about a minute: `ssh pi@192.168.10.104`.

---

## 5. Copy the code to the Pi

Pick **one** method.

**From your laptop (scp):**

```bash
# on the laptop, in the repo folder
scp -r facility-node pi@192.168.10.104:~
```

**With git (recommended, if the Pi can reach your git server):**

```bash
# on the Pi
sudo apt install -y git
git clone https://github.com/utpaulBS23/bhumijo-gateway-linux.git ~/bhumijo && ln -s ~/bhumijo/facility-node ~/facility-node
```

The repo is public, so the Pi needs **no GitHub key** to clone or `git pull` over HTTPS. Developers who push use SSH: `git@github.com:utpaulBS23/bhumijo-gateway-linux.git`.

> Never put site secrets in the repo. They belong only in `/opt/facility-node/.env` on each Pi (and `board_secrets.py` on the relay board). Both are git-ignored.

With a git checkout:
- **Updating is automatic:** after the bootstrap in step 6, every `git pull` reinstalls and restarts the node.
- **Version is exact:** `facility version` shows the commit.

**From a USB stick:** copy the `facility-node` folder onto the stick, plug it into the Pi, then:

```bash
sudo mount /dev/sda1 /mnt && cp -r /mnt/facility-node ~ && sudo umount /mnt
```

✅ **Check:** `ls ~/facility-node` shows `app.py`, `deploy/`, `INSTALL.md`.

---

## 6. Run the installer

**Git checkout:** bootstrap once. It turns on auto-install-on-pull, then runs the installer:

```bash
cd ~/bhumijo
sudo bash facility-node/deploy/bootstrap.sh
```

**scp or USB copy:**

```bash
cd ~/facility-node
sudo bash deploy/install.sh
```

> **Let Claude Code do it.** If Claude Code is installed on the Pi, run `claude` in `~/bhumijo` and say **"install"**. It follows `CLAUDE.md`: preflight, bootstrap, asks you for the `.env` secrets, doctor, tests, and a final report. Saying "update" or "check" works the same way.

If your LAN isn't `192.168.10.0/24`, set it first:

```bash
sudo LAN_CIDR=192.168.20.0/24 bash deploy/install.sh
```

**What the installer does:**

| Step | Result |
|---|---|
| Checks | Warns if this isn't a Pi 5 running 64-bit Bookworm |
| Packages | python3, venv, gpiozero, lgpio, ffmpeg, i2c-tools, ufw, curl |
| User | `facility` system user, in the `gpio` and `i2c` groups (the service runs as this user, not root) |
| Code | `/opt/facility-node` (code, `docs/`, `dev/`, `firmware/`, `deploy/`, `VERSION`) |
| Python | venv at `/opt/facility-node/venv`, with flask, requests, smbus2, qrcode |
| Config | `/opt/facility-node/.env` from the template (only on first install), mode `640 root:facility` |
| Data | `/var/lib/facility/` (database, snapshots, QR images), owned by `facility` |
| Command | `/usr/local/bin/facility` |
| I2C | enabled |
| Firewall | deny incoming, except SSH and port 5454 from the LAN only |
| Watchdog | hardware watchdog, so a hung Pi reboots itself within 15 s |
| Service | `facility-node.service`, enabled at boot |

✅ **The installer ends with `Installed …` and a "Next:" list.**

---

## 7. Configure

```bash
facility config
```

This opens `/opt/facility-node/.env` in nano. **Save with Ctrl-O, Enter, then exit with Ctrl-X.** The service restarts automatically after you exit.

### 7.1 Required: every `CHANGE_ME`

| Setting | Value |
|---|---|
| `FACILITY_ID` | from the admin backend, e.g. `facility-001` |
| `RELAY_PWD` | the KC868 board's `POST_PASSWORD` (SETUP.md section 4.3) |
| `ADMIN_URL` | backend base URL, e.g. `https://admin.example.com/api` |
| `ADMIN_KEY` | backend key |

### 7.2 Check for this site

| Setting | Default | Change if… |
|---|---|---|
| `RELAY_URL` | `http://192.168.10.174` | the relay has another IP |
| `CAM_HOST` / `CAM_RTSP` | `.180` / example URL | always: put the real camera user, password and stream |
| `DOORS` + `DOOR_MALE_*` / `DOOR_FEMALE_*` | male: Relay01/Input01/GPIO27/.101, female: Relay02/Input02/GPIO22/.102 | wiring or scanner IPs differ |
| `REED` | `0` | set `1` once the reed switches are fitted |
| `ADMIN_AUTH_HEADER` / `ADMIN_AUTH_SCHEME` | `Authorization` / `Bearer` | the backend uses another header (e.g. `X-API-Key` with an empty scheme) |
| `SENSOR_PATH`, `DOOR_EVENT_PATH`, `ALERT_PATH`, `TOKENS_PATH` | `/facility/...` | the backend uses other paths |

### 7.3 Optional features

| Feature | Settings | Guide |
|---|---|---|
| Online QR validation | `QR_API_*` | SETUP.md 8a |
| Odour alert limits | `ODOUR_*` | SETUP.md 8b |
| MQ-135 / MQ-136 gas sensors | `MQ=1`, `MQ135_R0`, `MQ136_R0` | SETUP.md 8b |
| Snapshot upload | `SNAPSHOT_UPLOAD_PATH` | SETUP.md 8c |

Every setting is explained in `.env.example`:

```bash
less /opt/facility-node/.env.example
```

> **Rule for `.env`:** one `KEY=value` per line, with no spaces around `=`. Put comments on their own line starting with `#`. A comment after a value (`KEY=1  # note`) becomes part of the value and breaks it.

---

## 8. Check everything

```bash
facility doctor
```

The doctor checks:
- that `.env` is complete and valid;
- the database folder and free space;
- the relay board (reachable, password right, exit inputs reported);
- the camera port and `ffmpeg`;
- the backend token pull, and the QR API if configured;
- the I2C sensors (AHT20 0x38, ENS160 0x53, ADS1115 0x48);
- that the service is running and `/health` answers.

It never fires a relay. Example of a healthy site:

```
Configuration
  ✓ facility facility-001
  ✓ door male - relay 1, exit input 0, reed GPIO 27, scanners 192.168.10.101
  ✓ door female - relay 2, exit input 1, reed GPIO 22, scanners 192.168.10.102
Relay board (http://192.168.10.174)
  ✓ reachable, password accepted
  ✓ inputs reported - pressed now: none
...
0 problem(s), 0 warning(s)
```

Fix every `✗` before continuing. A `!` is a warning: read it and decide. Some are expected, for example "MQ sensors not calibrated" before burn-in.

Then run the test suite (no hardware needed):

```bash
facility test       # last line: OK
```

---

## 9. Start and watch

The service started when you saved the config. To watch it:

```bash
facility status     # active (running)
facility logs       # live log, Ctrl-C to stop
facility health     # JSON: queue, tokens, doors, camera, odour, qr_api
```

In `facility logs`, look for:

```
door male: relay 1, exit input 0, reed GPIO 27, scanners 192.168.10.101
door female: relay 2, exit input 1, reed GPIO 22, scanners 192.168.10.102
odour limits {...}; MQ sensors off; snapshot upload off
QR validation: local cache only
token cache refreshed: N tokens
```

---

## 10. Reboot test

```bash
sudo reboot
```

Wait a minute, reconnect, then:

```bash
facility status && facility doctor
```

✅ **The service came back by itself, and the watchdog is now active.** Check with `systemctl show -p RuntimeWatchdogUSec`, which should print `15s`.

**The Pi software is done.** Continue with **SETUP.md**: scanners (section 7), test QR codes (section 8), commissioning tests (section 10).

---

## 11. The `facility` command

| Command | Does |
|---|---|
| `facility status` | Service status |
| `facility logs` | Live log |
| `facility health` | Node health JSON |
| `facility doctor` | Full installation check |
| `facility start` / `stop` / `restart` | Control the service |
| `facility config` | Edit `.env`, then restart |
| `facility test` | Run the test suite |
| `facility qr create --label test` | Make an unlock QR (24 h); add `--count 5 --hours 720` for staff codes |
| `facility qr list` / `facility qr revoke --label test` | List or revoke Pi-made codes |
| `facility mq read` / `facility mq calibrate` | MQ gas sensor values / clean-air calibration |
| `facility docs install` / `setup` / `readme` | Read the docs (q to quit) |
| `facility update` | `git pull` the checkout, reinstall, restart |
| `facility version` | Installed version |

---

## 12. Where things live on the Pi

| Path | What | Owner / mode |
|---|---|---|
| `/opt/facility-node/` | Code, venv, `VERSION` | root:facility |
| `/opt/facility-node/.env` | **Site config + secrets** | root:facility 640 |
| `/opt/facility-node/docs/` | README, INSTALL, SETUP | |
| `/opt/facility-node/dev/` | Dummy QR API + dummy QR images (testing only) | |
| `/opt/facility-node/firmware/kc868/` | Relay firmware (to flash from a laptop) | |
| `/var/lib/facility/facility.db` | Token cache, Auth Code hash, event queue, Pi-made codes | facility |
| `/var/lib/facility/snapshots/` | Camera JPEGs (kept `SNAPSHOT_KEEP_DAYS`) | facility |
| `/var/lib/facility/qr/` | QR images from `facility qr create` | facility |
| `/etc/systemd/system/facility-node.service` | Service unit | root |
| `/etc/systemd/system.conf.d/watchdog.conf` | Hardware watchdog | root |
| `/usr/local/bin/facility` | Helper command | root |
| `journalctl -u facility-node` | Logs (systemd journal) | |

---

## 13. Update to a new version

**Git checkout (bootstrapped):** just pull. The git hook reinstalls and restarts if anything under `facility-node/` changed:

```bash
cd ~/bhumijo && git pull
```

Or use the command, which works even without the hooks:

```bash
facility update
```

To pull **without** reinstalling, run `FACILITY_SKIP_AUTO_INSTALL=1 git pull`.

**scp copy:**

```bash
# laptop
scp -r facility-node pi@192.168.10.104:~
# Pi
cd ~/facility-node && sudo bash deploy/install.sh
```

The installer keeps `.env`, the database, snapshots and QR codes. It lists any **new settings** that this version added to `.env.example`, which you can copy into `.env` if you need them. It then restarts the service.

```bash
facility version && facility doctor
```

---

## 14. Backup, restore, and swapping in a spare Pi

**Back up (after commissioning, and after any config change):**

```bash
sudo tar czf ~/facility-backup-$(date +%F).tgz /opt/facility-node/.env /var/lib/facility
# then from the laptop:
scp pi@192.168.10.104:~/facility-backup-*.tgz .
```

Also keep the KC868's `board_secrets.py` and its `PCF8574_INPUT_ADDR` value with the site records.

**Swap in a spare Pi:**
1. Flash and install the spare (sections 1–6) with the **same hostname and IP**.
2. Copy the backup over and restore it:

```bash
scp facility-backup-*.tgz pi@192.168.10.104:~       # from the laptop
sudo systemctl stop facility-node
sudo tar xzf ~/facility-backup-*.tgz -C /
sudo chown -R facility:facility /var/lib/facility
sudo chown root:facility /opt/facility-node/.env && sudo chmod 640 /opt/facility-node/.env
sudo systemctl start facility-node && facility doctor
```

**Faster: SD image.** After commissioning, image the card on your laptop (Raspberry Pi Imager or `dd`). Writing that image to a new card gives an identical Pi.

---

## 15. Uninstall

```bash
cd ~/facility-node
sudo bash deploy/uninstall.sh            # keeps /var/lib/facility, saves .env to /root/
sudo bash deploy/uninstall.sh --purge    # also deletes the database, snapshots, QR images
```

---

## 16. Install troubleshooting

| Problem | Fix |
|---|---|
| `ssh: Could not resolve hostname facility-001.local` | Use the IP from the router's DHCP list |
| Installer: `Unable to locate package python3-lgpio` | Not Raspberry Pi OS Bookworm. Reflash with Raspberry Pi OS Lite (64-bit) |
| Installer: `pip … externally-managed-environment` | You ran pip outside the venv. Always use `/opt/facility-node/venv/bin/pip`; the installer already does |
| Installer stops at the firewall step, SSH drops | It doesn't if you're on SSH (port 22 is allowed first). If you're locked out, use a keyboard and screen and run `sudo ufw allow ssh` |
| `facility: command not found` | Re-run the installer, or call it directly: `/opt/facility-node/deploy/facility` |
| Service `failed`, log says `X is required (set it in .env)` | `facility config`, fill in X |
| Log: `relay channel used by more than one door` | Two doors share a relay, input, GPIO or scanner IP in `.env` |
| Doctor: `I2C bus 1 not available` | `sudo raspi-config nonint do_i2c 0 && sudo reboot` |
| Doctor: `0x38` / `0x53` not found | Sensor wiring (SDA = pin 3, SCL = pin 5, 3.3V = pin 1, GND = pin 9). Check with `sudo i2cdetect -y 1` |
| Doctor: `wrong relay password` | `RELAY_PWD` ≠ the board's `POST_PASSWORD` |
| Doctor: `firmware doesn't report inputs` | Flash `firmware/kc868/main.py` (SETUP.md section 4) |
| Doctor: `token pull HTTP 401` | `ADMIN_KEY` or `ADMIN_AUTH_HEADER` / `ADMIN_AUTH_SCHEME` wrong |
| Doctor: `token pull HTTP 404` | `ADMIN_URL` or `TOKENS_PATH` wrong |
| `facility health`: `Connection refused` | Service not running: `facility logs` shows why |
| Time is wrong in the logs | `timedatectl` should show `NTP service: active`; the Pi needs internet once to sync |
