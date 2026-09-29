#!/usr/bin/env python3
"""
Bhumijo Gateway Service - Linux Implementation
Receives requests from QR scanners, buttons, sensors
Controls relay devices (KC868-A4S)
Syncs with backend Bhumijo server
"""

import json
import logging
import requests
import os
from datetime import datetime
from flask import Flask, request, jsonify
from threading import Thread
import time
from typing import Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/bhumijo-gateway.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('BhumijoGateway')

app = Flask(__name__)

# Configuration
class Config:
    RELAY_IP = os.getenv('RELAY_IP', '192.168.1.100')
    RELAY_PASSWORD = os.getenv('RELAY_PASSWORD', '12345')
    SERVER_URL = os.getenv('SERVER_URL', 'http://localhost:8000')
    GATEWAY_PORT = int(os.getenv('GATEWAY_PORT', 5454))
    DEVICE_TOKEN = os.getenv('DEVICE_TOKEN', 'gateway-token')

class KC868A4S:
    """KC868-A4S 4-Relay Controller"""
    def __init__(self, ip: str, password: str = "12345"):
        self.ip = ip
        self.password = password
        self.base_url = f"http://{ip}"
        self.timeout = 5

    def get_status(self) -> Dict:
        """Load relay status from input.cgi"""
        try:
            resp = requests.get(f"{self.base_url}/input.cgi", timeout=self.timeout)
            resp.raise_for_status()
            return self._parse_status(resp.text)
        except Exception as e:
            logger.error(f"Relay status error: {e}")
            return {"error": str(e), "success": False}

    def control_relay(self, relay: int, state: int, duration: int = 3600) -> bool:
        """Control relay 1-4: state 1=ON, 0=OFF"""
        if not 1 <= relay <= 4:
            logger.warning(f"Invalid relay: {relay}")
            return False
        try:
            url = (f"{self.base_url}/cgi-bin/control?"
                   f"type=1&relay={relay}&on={state}"
                   f"&time={duration}&pwd={self.password}")
            resp = requests.get(url, timeout=self.timeout)
            resp.raise_for_status()
            logger.info(f"Relay {relay} -> {'ON' if state else 'OFF'}")
            return True
        except Exception as e:
            logger.error(f"Relay control error: {e}")
            return False

    def turn_on(self, relay: int, duration: int = 3600) -> bool:
        return self.control_relay(relay, 1, duration)

    def turn_off(self, relay: int) -> bool:
        return self.control_relay(relay, 0, 0)

    def pulse(self, relay: int, seconds: int = 1) -> bool:
        """Momentary pulse"""
        return self.control_relay(relay, 1, seconds)

    @staticmethod
    def _parse_status(response: str) -> Dict:
        """Parse: (result&relayCount&r1&r2&r3&r4)"""
        try:
            cleaned = response.strip()[1:-1]
            parts = cleaned.split('&')
            return {
                "success": parts[0] == "1",
                "relay_count": int(parts[1]),
                "relays": {
                    1: bool(int(parts[2])),
                    2: bool(int(parts[3])),
                    3: bool(int(parts[4])),
                    4: bool(int(parts[5]))
                }
            }
        except Exception as e:
            logger.error(f"Parse status error: {e}")
            return {"error": str(e), "success": False}

class BhumijoClient:
    """Backend server client"""
    def __init__(self, base_url: str, device_token: str):
        self.base_url = base_url
        self.device_token = device_token
        self.timeout = 10

    def get_user_access(self, card_id: str) -> Optional[Dict]:
        """Check user access"""
        try:
            payload = {
                "card_id": card_id,
                "device_token": self.device_token,
                "timestamp": datetime.now().isoformat()
            }
            resp = requests.post(
                f"{self.base_url}/api/user/accesses",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Get access error: {e}")
            return None

    def post_environment_quality(self, air_quality: int, temp: float, humidity: float, section: str) -> bool:
        """Post environmental data"""
        try:
            payload = {
                "device_token": self.device_token,
                "air_quality": air_quality,
                "temperature": temp,
                "humidity": humidity,
                "section": section,
                "timestamp": datetime.now().isoformat()
            }
            resp = requests.post(
                f"{self.base_url}/api/gateway/facilities/environment-qualities",
                json=payload,
                timeout=self.timeout
            )
            resp.raise_for_status()
            logger.info(f"Environment data posted: temp={temp}, humidity={humidity}")
            return True
        except Exception as e:
            logger.error(f"Post environment error: {e}")
            return False

    def get_services(self) -> Optional[Dict]:
        """Get facility services"""
        try:
            resp = requests.get(
                f"{self.base_url}/api/gateway/facilities/services",
                timeout=self.timeout
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Get services error: {e}")
            return None

# Initialize controllers
relay = KC868A4S(Config.RELAY_IP, Config.RELAY_PASSWORD)
bhumijo = BhumijoClient(Config.SERVER_URL, Config.DEVICE_TOKEN)

# ============ HTTP Endpoints ============

@app.route('/service', methods=['GET', 'POST'])
def handle_service():
    """Handle service button press
    GET: /service?button_id=1&gender=male
    POST: {"button_id": 1, "gender": "male"}
    """
    try:
        if request.method == 'GET':
            button_id = request.args.get('button_id')
            gender = request.args.get('gender')
        else:
            data = request.get_json() or {}
            button_id = data.get('button_id')
            gender = data.get('gender')

        logger.info(f"Service button: id={button_id}, gender={gender}")

        # Control relay based on button
        if button_id:
            relay_num = int(button_id) % 4 + 1
            relay.pulse(relay_num, seconds=2)

        return jsonify({"status": "success", "message": "item received"}), 200
    except Exception as e:
        logger.error(f"Service error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/qrscanner', methods=['GET', 'POST'])
def handle_qrscanner():
    """Handle QR code scan
    GET: /qrscanner?cardid=ABC123&cjihao=X&mjihao=1&status=1&time=2024-01-01
    """
    try:
        if request.method == 'GET':
            params = request.args.to_dict()
        else:
            params = request.get_json() or {}

        card_id = params.get('cardid')
        logger.info(f"QR scanned: {card_id}")

        # Check access on backend
        access = bhumijo.get_user_access(card_id)
        if access:
            logger.info(f"Access granted: {card_id}")
            # Trigger relay
            relay.pulse(1, seconds=3)
            response = {
                "status": "success",
                "data": [params],
                "access_granted": True
            }
        else:
            response = {
                "status": "denied",
                "data": [params],
                "access_granted": False
            }

        return jsonify(response), 200
    except Exception as e:
        logger.error(f"QR scanner error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/manager', methods=['POST'])
def handle_manager():
    """Handle event manager request"""
    try:
        data = request.get_json() or {}
        card_id = data.get('card_id')
        gender = data.get('gender')
        button_id = data.get('button_id')

        logger.info(f"Manager event: card={card_id}, button={button_id}")

        # Trigger appropriate relay
        if button_id:
            relay_num = int(button_id) % 4 + 1
            relay.pulse(relay_num, seconds=2)

        return jsonify({"status": "success", "message": "request received"}), 200
    except Exception as e:
        logger.error(f"Manager error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/airquality', methods=['POST'])
def handle_airquality():
    """Handle environmental quality data
    POST: {"air_quality": 50, "temperature": 25.5, "humidity": 60, "section": "building_a"}
    """
    try:
        data = request.get_json() or {}
        air_quality = data.get('air_quality', 0)
        temperature = data.get('temperature', 0)
        humidity = data.get('humidity', 0)
        section = data.get('section', 'unknown')

        logger.info(f"Air quality: AQ={air_quality}, T={temperature}, H={humidity}")

        # Post to backend
        bhumijo.post_environment_quality(air_quality, temperature, humidity, section)

        return jsonify({"status": "Item received"}), 200
    except Exception as e:
        logger.error(f"Air quality error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/wifibutton', methods=['POST'])
def handle_wifibutton():
    """Handle WiFi button press"""
    try:
        data = request.get_json() or {}
        logger.info(f"WiFi button: {data}")

        return jsonify({"status": "success", "message": "item posted"}), 200
    except Exception as e:
        logger.error(f"WiFi button error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route('/rakindaqrscanner', methods=['POST'])
def handle_rakinda():
    """Handle Rakinda QR scanner"""
    try:
        data = request.get_json() or {}
        scode = data.get('SCode')
        logger.info(f"Rakinda QR: {scode}")

        # Trigger relay for valid scan
        relay.pulse(1, seconds=2)

        return jsonify({
            "ResultCode": "1",
            "Msg": "Please Enter the facility",
            "Audio": "40"
        }), 200
    except Exception as e:
        logger.error(f"Rakinda error: {e}")
        return jsonify({"ResultCode": "0", "Msg": str(e)}), 400

@app.route('/relay/status', methods=['GET'])
def relay_status():
    """Get relay status"""
    try:
        status = relay.get_status()
        return jsonify(status), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/relay/control', methods=['POST'])
def relay_control():
    """Control relay
    POST: {"relay": 1, "state": 1, "duration": 3600}
    """
    try:
        data = request.get_json() or {}
        relay_num = data.get('relay', 1)
        state = data.get('state', 1)
        duration = data.get('duration', 3600)

        success = relay.control_relay(relay_num, state, duration)
        return jsonify({
            "status": "success" if success else "error",
            "relay": relay_num,
            "state": state
        }), 200 if success else 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    status = relay.get_status()
    return jsonify({
        "status": "healthy",
        "relay_connected": status.get('success', False),
        "timestamp": datetime.now().isoformat()
    }), 200

@app.errorhandler(404)
def not_found(error):
    return jsonify({"status": "error", "message": "Endpoint not found"}), 404

def main():
    logger.info("Starting Bhumijo Gateway Service")
    logger.info(f"Relay: {Config.RELAY_IP}")
    logger.info(f"Server: {Config.SERVER_URL}")
    logger.info(f"Port: {Config.GATEWAY_PORT}")

    # Test relay connection
    status = relay.get_status()
    if status.get('success'):
        logger.info(f"Relay connected: {status.get('relays')}")
    else:
        logger.warning("Relay connection failed")

    app.run(host='0.0.0.0', port=Config.GATEWAY_PORT, debug=False)

if __name__ == '__main__':
    main()