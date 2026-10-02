import machine
import network
import uasyncio as asyncio
import socket

# Ethernet Setup
lan = network.LAN(mdc=machine.Pin(23), mdio=machine.Pin(18), power=machine.Pin(5),
                  phy_type=network.PHY_LAN8720, phy_addr=0,
                  ref_clk=machine.Pin(17), ref_clk_mode=machine.Pin.OUT)
lan.active(True)
lan.ipconfig('addr4')

# Get the network configuration
network_config = lan.ifconfig()
ip_address = network_config[0]
print("IP Address:", ip_address)

# Password lives in board_secrets.py on the board (not in this file).
# Create it once per site:  POST_PASSWORD = "<long random string>"
try:
    from board_secrets import POST_PASSWORD as post_password
except ImportError:
    post_password = None
    print("WARNING: board_secrets.py missing - all requests will be rejected")
relay_states = [0, 0, 0, 0]

# Define I2C pins
A4S_SDA = 4   # SDA pin
A4S_SCL = 16  # SCL pin

# Initialize I2C
i2c = machine.I2C(0, scl=machine.Pin(A4S_SCL), sda=machine.Pin(A4S_SDA), freq=100000)

# PCF8574 I2C address for relays
PCF8574_ADDR = 0x24  

# PCF8574 for digital inputs. Find with: print([hex(a) for a in i2c.scan()])
PCF8574_INPUT_ADDR = 0x22   # <-- REPLACE with the scanned address (not 0x24)

def read_inputs():
    # Active-low: a contact to GND reads 0 when pressed. If pressed reads as
    # released, remove the `not`.
    raw = i2c.readfrom(PCF8574_INPUT_ADDR, 1)[0]
    return [not bool((raw >> i) & 1) for i in range(4)]

def parse_params(request):
    # "GET /x.cgi?a=1&b=2 HTTP/1.1" -> {"a": "1", "b": "2"}; {} if malformed
    try:
        query = request.split(" ")[1].split("?", 1)[1]
    except IndexError:
        return {}
    params = {}
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v
    return params

def authorized(params):
    return post_password is not None and params.get("postpwd") == post_password

async def respond(writer, status, body=b"", ctype="text/plain"):
    writer.write(("HTTP/1.1 " + status + "\r\nContent-Type: " + ctype + "\r\n\r\n").encode() + body)
    await writer.drain()
    await writer.wait_closed()

# Function to write data to PCF8574
def write_pcf8574(data):
    i2c.writeto(PCF8574_ADDR, bytes([data]))

# Function to turn relay ON (LOW) and schedule auto-off
async def relay_on(relay):
    global relay_states
    state = i2c.readfrom(PCF8574_ADDR, 1)[0]
    state &= ~(1 << relay)
    write_pcf8574(state)
    relay_states[relay] = 1
    
    # Auto-turn off after 1 seconds
    await asyncio.sleep(1)
    relay_off(relay)

# Function to turn relay OFF (HIGH)
def relay_off(relay):
    global relay_states
    state = i2c.readfrom(PCF8574_ADDR, 1)[0]
    state |= (1 << relay)
    write_pcf8574(state)
    relay_states[relay] = 0

# Initialize all relays to OFF (HIGH)
write_pcf8574(0xFF)

# Web Server Handler
async def handle_client(reader, writer):
    request = await reader.read(1024)
    request = request.decode()
    # Log the request line only: the full request contains the password
    print("Request:", request.split("\r\n", 1)[0].split("?", 1)[0])

    if "sw_ctl.cgi" in request:
        params = parse_params(request)
        if not authorized(params):
            # 401 (was 200) so the Pi can tell a failed unlock from a real one
            await respond(writer, "401 Unauthorized")
            return
        for key, value in params.items():
            if key.startswith("Relay") and value in ["ON", "OFF"]:
                try:
                    relay_id = int(key.replace("Relay", "")) - 1
                except ValueError:
                    continue
                if 0 <= relay_id < 4:
                    if value == "ON":
                        asyncio.create_task(relay_on(relay_id))
                    else:
                        relay_off(relay_id)
        await respond(writer, "200 OK", b"Relay Control Successful", "text/html")
        return

    elif "input_ctl.cgi" in request:
        params = parse_params(request)
        if not authorized(params):
            # Previously a missing postpwd raised KeyError and hung the socket
            await respond(writer, "401 Unauthorized")
            return
        relays = "&".join([f"Relay0{i+1}={'ON' if state else 'OFF'}" for i, state in enumerate(relay_states)])
        try:
            ins = "&".join([f"Input0{i+1}={'ON' if s else 'OFF'}" for i, s in enumerate(read_inputs())])
            response = relays + "&" + ins
        except OSError:
            # Input chip not found: report relays only; the Pi logs that exit tracking is off
            response = relays
        await respond(writer, "200 OK", response.encode())
        return

    await respond(writer, "404 Not Found")

# Start Web Server
async def start_server():
    server = await asyncio.start_server(handle_client, "", 80)
    print("Web server started on IP:", ip_address)

    while True:  # Keep the server running
        await asyncio.sleep(1)

# Run the event loop
asyncio.run(start_server())
