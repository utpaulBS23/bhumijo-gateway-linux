#!/usr/bin/env python3
"""
Bhumijo Gateway Admin - CLI interface for managing gateway service
"""

import argparse
import requests
import json
import subprocess
import sys
from datetime import datetime
from typing import Optional, Dict

class Colors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

class GatewayAdmin:
    def __init__(self, gateway_url: str = "http://localhost:5454"):
        self.gateway_url = gateway_url
        self.timeout = 5

    def _request(self, method: str, path: str, data: Optional[Dict] = None) -> tuple:
        """Make HTTP request"""
        try:
            url = f"{self.gateway_url}{path}"
            if method.upper() == "GET":
                resp = requests.get(url, timeout=self.timeout)
            else:
                resp = requests.post(url, json=data, timeout=self.timeout)
            return resp.status_code, resp.json() if resp.text else {}
        except Exception as e:
            return 0, {"error": str(e)}

    def status(self):
        """Show gateway status"""
        print(f"\n{Colors.BOLD}Bhumijo Gateway Status{Colors.ENDC}")
        print("=" * 50)

        # Health check
        code, data = self._request("GET", "/health")
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Gateway: RUNNING")
            print(f"  Relay connected: {data.get('relay_connected', False)}")
            print(f"  Timestamp: {data.get('timestamp', 'N/A')}")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Gateway: OFFLINE")
            return

        # Relay status
        code, data = self._request("GET", "/relay/status")
        if code == 200:
            print(f"\n{Colors.BOLD}Relay Status:{Colors.ENDC}")
            relays = data.get('relays', {})
            for i in range(1, 5):
                state = "ON" if relays.get(str(i), False) else "OFF"
                color = Colors.OKGREEN if state == "ON" else Colors.WARNING
                print(f"  Relay {i}: {color}{state}{Colors.ENDC}")

    def relay_on(self, relay: int, duration: int = 3600):
        """Turn relay ON"""
        code, data = self._request("POST", "/relay/control", {
            "relay": relay,
            "state": 1,
            "duration": duration
        })
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Relay {relay} turned ON")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {data}")

    def relay_off(self, relay: int):
        """Turn relay OFF"""
        code, data = self._request("POST", "/relay/control", {
            "relay": relay,
            "state": 0
        })
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Relay {relay} turned OFF")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {data}")

    def relay_pulse(self, relay: int, seconds: int = 1):
        """Pulse relay (momentary)"""
        code, data = self._request("POST", "/relay/control", {
            "relay": relay,
            "state": 1,
            "duration": seconds
        })
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Relay {relay} pulsed for {seconds}s")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {data}")

    def test_qr(self, card_id: str):
        """Test QR scan"""
        code, data = self._request("GET", f"/qrscanner?cardid={card_id}&cjihao=X&mjihao=1&status=1&time={datetime.now().isoformat()}")
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} QR scan test: {card_id}")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {data}")

    def test_airquality(self, aq: int = 50, temp: float = 25.5, humidity: float = 60):
        """Test air quality data"""
        code, data = self._request("POST", "/airquality", {
            "air_quality": aq,
            "temperature": temp,
            "humidity": humidity,
            "section": "test"
        })
        if code == 200:
            print(f"{Colors.OKGREEN}✓{Colors.ENDC} Air quality test: AQ={aq}, T={temp}°C, H={humidity}%")
        else:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {data}")

    def systemctl(self, action: str):
        """Control systemd service"""
        try:
            result = subprocess.run(
                ["sudo", "systemctl", action, "bhumijo-gateway"],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                print(f"{Colors.OKGREEN}✓{Colors.ENDC} Service {action}ed")
            else:
                print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {result.stderr}")
        except Exception as e:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {e}")

    def logs(self, lines: int = 50):
        """Show recent logs"""
        try:
            result = subprocess.run(
                ["sudo", "journalctl", "-u", "bhumijo-gateway", "-n", str(lines)],
                capture_output=True,
                text=True
            )
            print(f"\n{Colors.BOLD}Recent Logs ({lines} lines):{Colors.ENDC}")
            print(result.stdout)
        except Exception as e:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {e}")

    def config(self):
        """Show configuration"""
        try:
            with open("/etc/bhumijo-gateway/gateway.conf", "r") as f:
                print(f"\n{Colors.BOLD}Configuration:{Colors.ENDC}")
                for line in f:
                    if not line.startswith('#'):
                        print(f"  {line.strip()}")
        except Exception as e:
            print(f"{Colors.FAIL}✗{Colors.ENDC} Error: {e}")

def main():
    parser = argparse.ArgumentParser(
        description="Bhumijo Gateway Admin",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s status                 # Show gateway status
  %(prog)s relay-on 1             # Turn on relay 1
  %(prog)s relay-off 2            # Turn off relay 2
  %(prog)s relay-pulse 3 2        # Pulse relay 3 for 2 seconds
  %(prog)s test-qr ABC123         # Test QR scan
  %(prog)s test-air               # Test air quality
  %(prog)s logs 100               # Show last 100 logs
  %(prog)s start                  # Start service
  %(prog)s stop                   # Stop service
        """
    )

    parser.add_argument("--gateway", default="http://localhost:5454", help="Gateway URL")

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Status
    subparsers.add_parser("status", help="Show gateway status")

    # Relay commands
    relay_on = subparsers.add_parser("relay-on", help="Turn relay ON")
    relay_on.add_argument("relay", type=int, choices=[1, 2, 3, 4])
    relay_on.add_argument("--duration", type=int, default=3600)

    relay_off = subparsers.add_parser("relay-off", help="Turn relay OFF")
    relay_off.add_argument("relay", type=int, choices=[1, 2, 3, 4])

    relay_pulse = subparsers.add_parser("relay-pulse", help="Pulse relay")
    relay_pulse.add_argument("relay", type=int, choices=[1, 2, 3, 4])
    relay_pulse.add_argument("seconds", type=int, nargs="?", default=1)

    # Test commands
    test_qr = subparsers.add_parser("test-qr", help="Test QR scan")
    test_qr.add_argument("card_id", default="TEST123", nargs="?")

    subparsers.add_parser("test-air", help="Test air quality data")

    # Logs
    logs = subparsers.add_parser("logs", help="Show logs")
    logs.add_argument("lines", type=int, nargs="?", default=50)

    # Service control
    subparsers.add_parser("start", help="Start service")
    subparsers.add_parser("stop", help="Stop service")
    subparsers.add_parser("restart", help="Restart service")

    # Config
    subparsers.add_parser("config", help="Show configuration")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    admin = GatewayAdmin(args.gateway)

    if args.command == "status":
        admin.status()
    elif args.command == "relay-on":
        admin.relay_on(args.relay, args.duration)
    elif args.command == "relay-off":
        admin.relay_off(args.relay)
    elif args.command == "relay-pulse":
        admin.relay_pulse(args.relay, args.seconds)
    elif args.command == "test-qr":
        admin.test_qr(args.card_id)
    elif args.command == "test-air":
        admin.test_airquality()
    elif args.command == "logs":
        admin.logs(args.lines)
    elif args.command == "start":
        admin.systemctl("start")
    elif args.command == "stop":
        admin.systemctl("stop")
    elif args.command == "restart":
        admin.systemctl("restart")
    elif args.command == "config":
        admin.config()

if __name__ == "__main__":
    main()