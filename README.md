# Bhumijo Facility System

| Folder / file | What it is |
|---|---|
| **[`facility-node/`](facility-node/)** | **Current system.** Offline-first node for one Raspberry Pi 5 per facility: two doors (male/female), QR + Facility-app unlock, exit tracking, reed alerts, sensors, camera health. **Docs: [README](facility-node/README.md) · [Install on the Pi](facility-node/INSTALL.md) · [Field setup](facility-node/SETUP.md)** |
| `facility-node/firmware/kc868/` | Patched KC868-A4S relay firmware (required by the node) |
| `FLUTTER_*.md` | Facility (attendant) app design, updated for the node's `POST /facility/open` |

### Facility network (per site)

| Device | IP |
|---|---|
| Router | 192.168.10.100 |
| QR scanner, male / female | 192.168.10.101 / .102 |
| Raspberry Pi 5 (facility node) | 192.168.10.104:5454 |
| KC868-A4S relay | 192.168.10.174 |
| IP camera | 192.168.10.180 |

The legacy single-door gateway (`gateway_service.py`) was removed; it's in git history at commit `e364645`.
