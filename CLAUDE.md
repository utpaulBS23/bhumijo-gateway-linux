# CLAUDE.md

Instructions for Claude Code working in this repo, **on the facility Raspberry Pi** or on a dev machine.

The system is the **facility node** in `facility-node/`: an offline-first controller for one Raspberry Pi 5 per facility, with three functions, each switched on or off in `.env`: **door lock/unlock** (`door/`, `ENABLE_DOORS`), **camera monitoring** (`camera/`, `ENABLE_CAMERA`) and **odour monitoring** (`odour/`, `ENABLE_ODOUR`). Read `facility-node/README.md` for how it works, `facility-node/INSTALL.md` for the Pi install, and `facility-node/SETUP.md` for field setup. The `FLUTTER_*.md` files at the root are the attendant-app design.

---

## 0. Where am I?

Run this first and pick the matching workflow:

```bash
uname -m; tr -d '\0' < /proc/device-tree/model 2>/dev/null; echo; test -d /opt/facility-node && echo INSTALLED || echo NOT-INSTALLED
```

| Output | Workflow |
|---|---|
| `aarch64` + `Raspberry Pi 5` + `NOT-INSTALLED` | **§1 Install** |
| `aarch64` + `Raspberry Pi 5` + `INSTALLED` | **§2 Update** or **§3 Check**, depending on what the user asked |
| Anything else | **§5 Development** (laptop). Never run the installer here |

When the user just says "install", "set up", "do everything", or refers you to this file, run §1 or §2 end to end without stopping, except where a step says to **ask**.

---

## 1. Install on a fresh Pi

Work from the repo root (the folder containing this file). Report briefly after each step.

1. **Preflight.** Stop and tell the user if any of these fail:
   ```bash
   grep -E 'VERSION_CODENAME' /etc/os-release      # bookworm (or trixie)
   hostname -I                                     # expect 192.168.10.104
   ping -c1 -W2 192.168.10.1 && ping -c1 -W3 8.8.8.8
   sudo -n true && echo SUDO-OK
   ```
   - **Wrong OS:** Raspberry Pi OS Lite 64-bit is needed; see INSTALL.md §1.
   - **IP isn't `.104`:** warn the user and point to INSTALL.md §3, but continue.
   - **No internet:** the install can't download packages.
   - **No passwordless sudo:** ask the user to run the bootstrap command themselves.

2. **Install and turn on auto-update on pull:**
   ```bash
   sudo bash facility-node/deploy/bootstrap.sh
   ```
   This sets `core.hooksPath` so every later `git pull` reinstalls the node, then runs `deploy/install.sh`. If it fails, read the error, fix the cause (see the INSTALL.md §16 table), and re-run it. It's safe to re-run.

3. **Configure `.env`.** List what's missing without printing any values:
   ```bash
   sudo grep -n 'CHANGE_ME' /opt/facility-node/.env | cut -d= -f1
   ```
   - **Ask the user** which of the three functions this site has (doors, camera, odour) and switch the others off (`ENABLE_*=0`, or `facility feature <fn> off` once installed).
   - **Ask the user** for each missing value in one message: `FACILITY_ID`, `ADMIN_URL`, `ADMIN_KEY`, `RELAY_PWD` (doors), and the camera via `facility camera` (camera).
   - Also ask whether these differ from the defaults:
     - the door map (male: Relay01 / Input01 / GPIO27 / scanner .101; female: Relay02 / Input02 / GPIO22 / scanner .102) and each door's unlock time (`DOOR_*_UNLOCK_SECONDS`, default 5 s);
     - `REED` (0 until reed switches are fitted);
     - the backend `*_PATH` and `ADMIN_AUTH_*` settings.
   - **Never invent, guess or reuse example values** for secrets or IDs.
   - Write each value with an exact, anchored replacement, for example:
     ```bash
     sudo sed -i 's|^FACILITY_ID=.*|FACILITY_ID=<value>|' /opt/facility-node/.env
     ```
     For values containing `|` or `&`, use a small Python edit instead.
   - Keep `KEY=value` with no spaces and **no comment after a value** (systemd keeps it as part of the value).
   - Keep permissions: `sudo chown root:facility /opt/facility-node/.env && sudo chmod 640 /opt/facility-node/.env`.
   - **Never echo secrets back** in chat or logs. Confirm by key name only.

4. **Start and verify:**
   ```bash
   sudo systemctl restart facility-node
   facility doctor
   facility test
   facility health
   ```
   - Fix every `✗` in `facility doctor`, re-running it after each fix.
   - Explain each `!` to the user. Expected ones on a new site: "MQ sensors not calibrated", "no Auth Code cached yet" (until the backend sends `authCode`), "database not created yet" (first minute).
   - Hardware problems (wiring, relay firmware, sensor not found): tell the user what to check, quoting the SETUP.md section. Don't keep retrying.

5. **Reboot test.** Ask the user first, because it drops the session:
   ```bash
   sudo reboot
   ```
   After reconnecting: `facility status && facility doctor`, and `systemctl show -p RuntimeWatchdogUSec` should show `15s`.

6. **Final report:**
   - `facility version`
   - the doctor summary line
   - which functions are on (`facility feature`) and which optional features are on or off (QR API, MQ, snapshot upload, reed, sensor board)
   - what's left for the user: SETUP.md §4.4 scanners, §4.5 test QR, the per-function tests (§4.8 door, §5.3 camera, §6.5 odour), §8 commissioning tests, §11 security checklist.

---

## 2. Update an installed Pi

When the user asks to "update" or "pull":

```bash
facility update          # git pull --ff-only + reinstall + restart
facility doctor
```

- **Hooks are on (bootstrap was run):** a plain `git pull` also reinstalls automatically.
- **Installer reports "new optional settings":** tell the user which ones and what they do (from `.env.example`). Only add them to `.env` if the user wants them.
- **Pull fails (local changes, diverged):** show `git status` and ask. Never `reset --hard` or force anything.

---

## 3. Check / diagnose

When the user asks "is it working?", "check", or reports a problem:

```bash
facility doctor            # or one function: facility doctor door | camera | odour
facility health
journalctl -u facility-node -n 100 --no-pager
```

Match the symptoms against the troubleshooting tables in INSTALL.md §16 and SETUP.md (§4.9 door, §5.4 camera, §6.6 odour, §10 general), and give the cause and the fix.

---

## 4. Rules on the Pi (always)

- **Never fire a relay or unlock a door** (`sw_ctl.cgi`, `POST /qr`, `POST /facility/open`) without the user's explicit OK for that specific test. People may be at the door.
- **Never weaken security:**
  - don't disable or open the firewall (`ufw`), and don't expose port 5454 beyond the LAN;
  - don't loosen `.env` permissions, and don't set `DOOR_TEST_ENDPOINTS=1` unless asked for bench testing (then remind them to turn it off);
  - don't run the service as root.
- **QR codes:**
  - never create codes with `--no-expiry` unless the user insists after a warning;
  - revoke test codes after commissioning (`facility qr revoke --label test`);
  - never paste a real token into chat.
- **Dummy QR API / dummy backend (`facility-node/dev/`):** only when the user asks for testing. Remind them afterwards to restore the real `ADMIN_URL` / `ADMIN_KEY`, clear `QR_API_URL`, stop the dummies, and remove any `ufw` rule for 8091.
- **Don't change the system** beyond what `install.sh` does (no other `/etc` edits, apt removals or kernel and boot config changes) without asking.
- **Never commit or print** `.env`, `board_secrets.py` or keys. Don't `git push` from the Pi unless asked.
- **No destructive data actions without asking:** don't delete `/var/lib/facility` (database, queue, snapshots), and don't run `deploy/uninstall.sh --purge`.
- **Ask before rebooting** or restarting networking.

---

## 5. Development (laptop / CI)

```bash
cd facility-node
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_door.py          # must end with OK
```

- **Tests must pass** before committing. Add tests for any behaviour change. They fake the relay, backend and clock (see the existing `Fake*` classes in `test_door.py`).
- **New setting?** Update all of these:
  1. the `config.py` `Settings` field and `from_env`;
  2. `.env.example` (comment on its own line);
  3. the README §6 table;
  4. a SETUP/INSTALL section if a field tech needs it.
- **New backend field or endpoint:** update the README §8 contract **and** `dev/dummy_backend.py`, so the dummy keeps matching the real contract. Every pushed item must keep its `id` (idempotency).
- **Keep the offline-first rule:** nothing new may put the network or a sensor in the unlock path, or block the door from opening.
- **Keep the three functions apart:** door code in `door/`, camera in `camera/`, odour in `odour/`; shared code (`app.py`, `config.py`, `store.py`, `uplink.py`, `doctor.py`) at the top. A function that is switched off must start no jobs and need none of its settings (`config.FEATURES`).
- **Hardware imports** (`smbus2`, `gpiozero`) stay lazy, so tests run off-Pi.
- **Style:** match the surrounding code (small modules, docstrings that explain why, no new dependencies without need).
- **Shell scripts:** `bash -n` them; `install.sh` must stay safe to re-run.

---

## 6. Map

| Path | What |
|---|---|
| `facility-node/app.py` | Flask endpoints + starts each enabled function's jobs (entry point) |
| `facility-node/config.py` | All settings (`.env`), function switches (`FEATURES`) and validation |
| `facility-node/store.py` / `uplink.py` | SQLite cache and queue / backend client |
| `facility-node/doctor.py` | Installation self-check, per function (`facility doctor [door\|camera\|odour]`) |
| `facility-node/door/` | **1. Door:** `controller.py` unlock decisions + door alerts, `relay.py`, `inputs.py`, `qr_api.py`, `qr_tool.py` |
| `facility-node/camera/` | **2. Camera:** `camera.py` probe, frozen-feed check, snapshots |
| `facility-node/odour/` | **3. Odour:** `sensors.py` ENS160/AHT20 + sensor push, `monitor.py` TVOC score + odour alert, `mq.py` gas sensors |
| `facility-node/deploy/` | `install.sh`, `bootstrap.sh`, `uninstall.sh`, `facility` command, `feature.sh`, `camera-setup.sh`, `sensor-setup.sh`, systemd unit, `githooks/` |
| `facility-node/firmware/kc868/` | Relay board firmware (door; flashed from a laptop, not the Pi) |
| `facility-node/firmware/air-sensor/` | ESP32-S3 air sensor board firmware (odour; `facility sensor`) |
| `/opt/facility-node/` (Pi) | Installed code, venv, `.env`, docs |
| `/var/lib/facility/` (Pi) | Database, snapshots, QR images |
