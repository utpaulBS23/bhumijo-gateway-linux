#!/usr/bin/env python3
"""MAC checks: is the device at this IP the one that was wired in?

Each LAN device (relay board, QR scanners, cameras, air sensor board) can have
its MAC set in .env (RELAY_MAC, DOOR_<NAME>_SCANNER_MACS, CAM_MAC /
CAM_<NAME>_MAC, SENSOR_MAC). The Pi reads the MAC it actually talks to from
its ARP table (/proc/net/arp, no root needed), so a laptop that takes over the
relay board's IP never receives the relay password, and a fake scanner can't
open a door. A wrong MAC blocks that device and raises a device_mismatch alert.

This stops casual impersonation and wiring mix-ups. A MAC can be faked, so a
strong relay password and a separate customer WiFi still matter.

    facility macs          the MAC seen for every configured device
    facility macs save     write those MACs into .env (asks first; needs sudo)
"""

import argparse
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

log = logging.getLogger("netcheck")

ARP = "/proc/net/arp"
NO_MAC = "00:00:00:00:00:00"


def arp_table(path=ARP):
    """{ip: mac} of complete ARP entries; {} if the table can't be read."""
    table = {}
    try:
        with open(path) as f:
            next(f, None)                         # header line
            for line in f:
                parts = line.split()
                # IP address, HW type, Flags, HW address, Mask, Device
                if len(parts) >= 4 and parts[2] != "0x0" and parts[3] != NO_MAC:
                    table[parts[0]] = parts[3].lower()
    except OSError:
        pass
    return table


def tcp_touch(ip, port, timeout=1.0):
    """Open and close a TCP connection, so the kernel learns the device's MAC.
    Nothing is sent, so no password leaves the Pi before the MAC is checked."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            pass
    except OSError:
        pass


def host_port(url, default_port=80):
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else default_port)
    return parts.hostname, port


class MacGuard:
    """Checks devices against their expected MACs; remembers what it saw.

    check() is cheap (one read of a small /proc file) and safe to call before
    every relay command. A mismatch is alerted at most once per `realert`
    seconds per device and MAC, so a relay polled 5 times a second can't flood
    the queue.
    """

    def __init__(self, settings, uplink=None, arp=arp_table, touch=tcp_touch,
                 clock=time.time, realert=600):
        self.s = settings
        self.uplink = uplink
        self.arp = arp
        self.touch = touch
        self.clock = clock
        self.realert = realert
        self.devices = {}         # name -> {"ip", "macs", "port", "section", "env_key"}
        self._seen = {}           # name -> {"mac", "ok", "checked_at"}
        self._alerted = {}        # (name, mac) -> ts
        self._lock = threading.Lock()

    def add(self, name, ip, macs=(), port=None, section=None, env_key=None):
        """port: TCP port to touch when the ARP table has no entry yet."""
        if ip:
            self.devices[name] = {"ip": ip, "macs": tuple(macs), "port": port,
                                  "section": section, "env_key": env_key}

    def check(self, name, ip=None):
        """True = go ahead (MAC matches, or no MAC set for it); False = blocked."""
        dev = self.devices.get(name)
        if dev is None:
            return True
        ip = ip or dev["ip"]
        seen = self.arp().get(ip)
        if seen is None and dev["port"]:
            self.touch(ip, dev["port"])
            seen = self.arp().get(ip)
        ok = None if not dev["macs"] else seen in dev["macs"]
        with self._lock:
            self._seen[name] = {"mac": seen, "ok": ok, "checked_at": self.clock()}
        if ok is False:
            self._mismatch(name, dev, ip, seen)
        return ok is not False

    def _mismatch(self, name, dev, ip, seen):
        now = self.clock()
        with self._lock:
            last = self._alerted.get((name, seen))
            if last is not None and now - last < self.realert:
                return
            self._alerted[(name, seen)] = now
        log.error("%s at %s has MAC %s, expected %s: blocked", name, ip,
                  seen or "unknown (no ARP entry)", ", ".join(dev["macs"]))
        if self.uplink is None:
            return
        from uplink import iso
        payload = {"facility": self.s.facility_id, "type": "anomaly",
                   "subtype": "device_mismatch", "device": name, "ip": ip,
                   "expected": list(dev["macs"]), "seen": seen, "ts": iso(now)}
        if dev["section"]:
            payload["door"] = dev["section"]
        self.uplink.enqueue("alert", payload)

    def state(self):
        """{name: {ip, mac, expected, ok}} for the sensor push and /health.
        ok: true / false, or null when no MAC is set (reported, not checked)."""
        table = self.arp()
        with self._lock:
            seen = dict(self._seen)
        out = {}
        for name, dev in self.devices.items():
            last = seen.get(name, {})
            mac = table.get(dev["ip"]) or last.get("mac")
            ok = None if not dev["macs"] else (last["ok"] if "ok" in last
                                               else (mac in dev["macs"] if mac else None))
            out[name] = {"ip": dev["ip"], "mac": mac, "expected": list(dev["macs"]) or None,
                         "ok": ok}
        return out

    def verifier(self, name):
        """A no-argument check for one device, for the relay / camera / sensor clients."""
        return lambda: self.check(name)


def devices_from_settings(guard, s):
    """Register every LAN device the node talks to, with its .env MACs."""
    single_door = os.environ.get("DOORS") is None
    single_cam = os.environ.get("CAMERAS") is None
    if s.enable_doors and s.relay_url:
        host, port = host_port(s.relay_url)
        guard.add("relay", host, s.relay_macs, port, env_key="RELAY_MAC")
    if s.enable_doors:
        for d in s.doors:
            key = "SCANNER_MACS" if single_door else f"DOOR_{d.name.upper()}_SCANNER_MACS"
            for i, ip in enumerate(d.scanner_ips):
                name = f"scanner_{d.name}" + (f"_{i + 1}" if len(d.scanner_ips) > 1 else "")
                guard.add(name, ip, d.scanner_macs, section=d.door_id or d.name, env_key=key)
    for c in s.cameras:
        key = "CAM_MAC" if single_cam else f"CAM_{c.name.upper()}_MAC"
        guard.add(f"camera_{c.name}", c.host, c.macs, c.port, env_key=key)
    if s.enable_odour and s.sensor_url:
        host, port = host_port(s.sensor_url)
        guard.add("sensor_board", host, s.sensor_macs, port, env_key="SENSOR_MAC")
    return guard


def scanner_name(guard, ip):
    for name, dev in guard.devices.items():
        if name.startswith("scanner_") and dev["ip"] == ip:
            return name
    return None


# ---- facility macs ---------------------------------------------------------

def set_env_keys(path, values):
    """Set KEY=value lines in .env in place (keeps owner and mode); add missing keys."""
    with open(path) as f:
        lines = f.read().splitlines()
    done = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and key in values and key not in done:
            lines[i] = f"{key}={values[key]}"
            done.add(key)
    lines += [f"{k}={v}" for k, v in values.items() if k not in done]
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def ping(ip):
    subprocess.run(["ping", "-c1", "-W1", ip], capture_output=True)


def main(argv=None):
    from config import ConfigError, Settings, load_env_file
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--env", default=os.environ.get("FACILITY_ENV", "/opt/facility-node/.env"))
    p.add_argument("action", nargs="?", choices=["show", "save"], default="show")
    p.add_argument("-y", "--yes", action="store_true", help="save without asking")
    args = p.parse_args(argv)
    try:
        os.environ.update(load_env_file(args.env))
        s = Settings.from_env()
    except (OSError, ConfigError, ValueError) as e:
        print(f"cannot load {args.env}: {e}")
        return 1
    guard = devices_from_settings(MacGuard(s), s)
    for dev in guard.devices.values():
        ping(dev["ip"])
    table = arp_table()

    print(f"{'device':<20} {'ip':<16} {'MAC seen':<18}  set in .env")
    found = {}
    for name, dev in guard.devices.items():
        seen = table.get(dev["ip"])
        expected = ", ".join(dev["macs"]) or "-"
        mark = "" if not dev["macs"] else ("  OK" if seen in dev["macs"] else "  MISMATCH")
        print(f"{name:<20} {dev['ip']:<16} {seen or 'not found':<18}  {expected}{mark}")
        if seen:
            found.setdefault(dev["env_key"], []).append(seen)
    print(f"\nthis Pi: {s.device_mac or 'unknown'} (DEVICE_MAC)")
    if args.action == "show":
        return 0

    values = {k: ",".join(dict.fromkeys(v)) for k, v in found.items()}
    if not values:
        print("no device answered; nothing to save")
        return 1
    print("\nwill write:\n" + "\n".join(f"  {k}={v}" for k, v in values.items()))
    print("Only save while every device is the real one (on site, wired as planned).")
    if not args.yes and input("write these to .env? [y/N] ").strip().lower() != "y":
        print("nothing written")
        return 1
    set_env_keys(args.env, values)
    print(f"saved to {args.env}; restart the service: sudo systemctl restart facility-node")
    return 0


if __name__ == "__main__":
    sys.exit(main())
