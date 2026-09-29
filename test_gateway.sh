#!/bin/bash

# Bhumijo Gateway Service Test Script

GATEWAY="http://localhost:5454"
RELAY_IP="${RELAY_IP:-192.168.1.100}"
RELAY_PASSWORD="${RELAY_PASSWORD:-12345}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

test_count=0
pass_count=0

test_endpoint() {
    local name=$1
    local method=$2
    local endpoint=$3
    local data=$4

    test_count=$((test_count + 1))
    echo -n "Test $test_count: $name ... "

    if [ "$method" = "GET" ]; then
        response=$(curl -s -w "\n%{http_code}" "$GATEWAY$endpoint")
    else
        response=$(curl -s -w "\n%{http_code}" -X $method -H "Content-Type: application/json" -d "$data" "$GATEWAY$endpoint")
    fi

    http_code=$(echo "$response" | tail -n1)
    body=$(echo "$response" | head -n-1)

    if [[ $http_code == 200 || $http_code == 201 ]]; then
        echo -e "${GREEN}PASS${NC} (HTTP $http_code)"
        echo "  Response: $body"
        pass_count=$((pass_count + 1))
    else
        echo -e "${RED}FAIL${NC} (HTTP $http_code)"
        echo "  Response: $body"
    fi
    echo ""
}

# Banner
echo "======================================="
echo "Bhumijo Gateway Service - Test Suite"
echo "======================================="
echo "Gateway: $GATEWAY"
echo "Relay: $RELAY_IP"
echo ""

# Check if gateway is running
echo -n "Checking if gateway is running... "
if curl -s "$GATEWAY/health" > /dev/null 2>&1; then
    echo -e "${GREEN}OK${NC}"
else
    echo -e "${RED}FAILED${NC}"
    echo "Gateway not responding at $GATEWAY"
    echo "Start with: sudo systemctl start bhumijo-gateway"
    exit 1
fi
echo ""

# Tests
echo "=== Health Check ==="
test_endpoint "Health endpoint" "GET" "/health" ""

echo "=== Relay Control ==="
test_endpoint "Get relay status" "GET" "/relay/status" ""
test_endpoint "Turn on relay 1" "POST" "/relay/control" '{"relay": 1, "state": 1, "duration": 3600}'
test_endpoint "Turn off relay 1" "POST" "/relay/control" '{"relay": 1, "state": 0}'
test_endpoint "Pulse relay 2" "POST" "/relay/control" '{"relay": 2, "state": 1, "duration": 2}'

echo "=== QR Scanner ==="
test_endpoint "QR scan (GET)" "GET" "/qrscanner?cardid=TEST123&cjihao=X&mjihao=1&status=1&time=2024-01-01" ""
test_endpoint "QR scan (POST)" "POST" "/qrscanner" '{"cardid": "TEST123", "cjihao": "X", "mjihao": 1, "status": 1, "time": "2024-01-01"}'

echo "=== Service Button ==="
test_endpoint "Service button (GET)" "GET" "/service?button_id=1&gender=male" ""
test_endpoint "Service button (POST)" "POST" "/service" '{"button_id": 1, "gender": "male"}'

echo "=== WiFi Button ==="
test_endpoint "WiFi button" "POST" "/wifibutton" '{"button_id": 1}'

echo "=== Environmental Quality ==="
test_endpoint "Air quality data" "POST" "/airquality" '{"air_quality": 50, "temperature": 25.5, "humidity": 60, "section": "building_a"}'

echo "=== Manager Event ==="
test_endpoint "Manager event" "POST" "/manager" '{"card_id": "ABC123", "gender": "male", "button_id": 1}'

echo "=== Rakinda QR Scanner ==="
test_endpoint "Rakinda scan" "POST" "/rakindaqrscanner" '{"SCode": "ABC123"}'

echo "=== 404 Error ==="
test_endpoint "Invalid endpoint" "GET" "/invalid" ""

# Summary
echo "======================================="
echo "Test Results: $pass_count/$test_count PASSED"
if [ $pass_count -eq $test_count ]; then
    echo -e "${GREEN}All tests passed!${NC}"
else
    echo -e "${RED}Some tests failed!${NC}"
fi
echo "======================================="