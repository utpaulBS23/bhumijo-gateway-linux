# Bhumijo Facility System

Repository: [github.com/utpaulBS23/bhumijo-gateway-linux](https://github.com/utpaulBS23/bhumijo-gateway-linux)
(clone on a Pi over HTTPS; developers push over SSH: `git@github.com:utpaulBS23/bhumijo-gateway-linux.git`)

| Folder / file | What it is |
|---|---|
| **[`facility-node/`](facility-node/)** | **Current system.** Offline-first node for one Raspberry Pi 5 per facility: two doors (male/female), QR + Facility-app unlock, exit tracking, reed alerts, sensors, camera health. **Docs: [README](facility-node/README.md) · [Install on the Pi](facility-node/INSTALL.md) · [Field setup](facility-node/SETUP.md)** |
| `facility-node/firmware/kc868/` | Patched KC868-A4S relay firmware (required by the node) |
| `CLAUDE.md` | Instructions for Claude Code: on the Pi, say "install", "update" or "check" and it runs the full workflow |
| `FLUTTER_*.md` | Facility (attendant) app design, updated for the node's `POST /facility/open` |

### Install on a Pi (one time)

```bash
git clone https://github.com/utpaulBS23/bhumijo-gateway-linux.git ~/bhumijo && cd ~/bhumijo
sudo bash facility-node/deploy/bootstrap.sh   # installs, and every later `git pull` reinstalls + restarts
facility config && facility doctor
```

### Facility network (per site)

| Device | IP |
|---|---|
| Router | 192.168.10.100 |
| QR scanner, male / female | 192.168.10.101 / .102 |
| Raspberry Pi 5 (facility node) | 192.168.10.104:5454 |
| KC868-A4S relay | 192.168.10.174 |
| IP camera | 192.168.10.180 |

The legacy single-door gateway (`gateway_service.py`) was removed; it's in git history at commit `e364645`.
