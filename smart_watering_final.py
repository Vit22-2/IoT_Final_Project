# ------------------------------------------------------------
# ESP32 Water Monitoring System
# LCD + Telegram + MQTT + Automatic Watering + HTTP (MIT App)
# Features:
#   - Distance-based tank status (LOW/MID/HIGH/UNKNOWN)
#   - Auto-watering OFF at startup, toggled via MIT App + Telegram
#   - Startup Telegram help message
#   - Terminal debug messages for ALL actions
#   - Safe-mode error handling (no fatal crash on network errors)
# ------------------------------------------------------------

import network
import urequests
import socket
from machine import Pin, SoftI2C, reset, time_pulse_us
from umqtt.simple import MQTTClient
from bmp280 import BMP280
from machine_i2c_lcd import I2cLcd
import dht
import utime
import json

# ------------------------------------------------------------
# WiFi Configuration
# ------------------------------------------------------------
WIFI_SSID     = "Robotic WIFI"
WIFI_PASSWORD = "rbtWIFI@2025"

# ------------------------------------------------------------
# Telegram Bot
# ------------------------------------------------------------
BOT_TOKEN = "8360114715:AAE1_sKMwOBkY01ynu2fvKFpAvxyvDWeK5o"
ALLOWED_CHAT_IDS = {1128192910}
API = "http://api.telegram.org/bot" + BOT_TOKEN

STARTUP_HELP = (
    "🤖 *Water System Online!*\n"
    "Available Commands:\n"
    "/status - Show all sensor values\n"
    "/tank - Show tank level\n"
    "/setwater <sec> - Set watering interval\n"
    "/setduration <sec> - Set pump on duration\n"
    "/autoon - Enable automatic watering\n"
    "/autooff - Disable automatic watering\n"
)

# ------------------------------------------------------------
# MQTT Configuration
# ------------------------------------------------------------
BROKER = "broker.hivemq.com"
PORT = 1883
CLIENT_ID = b"esp32_water_monitor"

TOPIC_WATER_STATUS = b"/aupp/group1/water_status"   # LOW/MID/HIGH/UNKNOWN
TOPIC_WATER_CM     = b"/aupp/group1/water_cm"
TOPIC_TEMP         = b"/aupp/group1/temperature"
TOPIC_HUM          = b"/aupp/group1/humidity"
TOPIC_PRESS        = b"/aupp/group1/pressure"

# ------------------------------------------------------------
# Hardware Pins
# ------------------------------------------------------------
DHT_PIN = 4
TRIG_PIN = 27
ECHO_PIN = 26
PUMP_PIN = Pin(13, Pin.OUT, value=0)

I2C_SDA = 21
I2C_SCL = 22
LCD_ADDR = 0x27

# ------------------------------------------------------------
# Water Tank Distance Ranges (cm)
# ------------------------------------------------------------
# 8.6–7.6  -> LOW
# 7.5–4.0  -> MID
# 3.9–2.0  -> HIGH
# else     -> UNKNOWN

# ------------------------------------------------------------
# URL Encoding
# ------------------------------------------------------------
def url_encode(text):
    out = ""
    for c in str(text):
        o = ord(c)
        if (48 <= o <= 57) or (65 <= o <= 90) or (97 <= o <= 122) or c in "-_.~":
            out += c
        else:
            for b in c.encode("utf-8"):
                out += "%%%02X" % b
    return out

# ------------------------------------------------------------
# Initialize Devices
# ------------------------------------------------------------
dht_sensor = dht.DHT11(Pin(DHT_PIN))
TRIG = Pin(TRIG_PIN, Pin.OUT)
ECHO = Pin(ECHO_PIN, Pin.IN)

i2c = SoftI2C(sda=Pin(I2C_SDA), scl=Pin(I2C_SCL), freq=400000)
lcd = I2cLcd(i2c, LCD_ADDR, 2, 16)
bmp = BMP280(i2c)

lcd.clear()
lcd.putstr("Starting...")
utime.sleep(1)

# Last known tank values
last_tank_status = None  # "LOW", "MID", "HIGH", "UNKNOWN"
last_tank_cm = None

# Sensor globals (for /status)
temperature = None
humidity = None
pressure = None

# Auto-watering + pump state
watering_interval = 3600   # seconds
watering_duration = 5      # seconds
auto_watering = False      # OFF at startup
WATERING = False           # pump state

# MQTT client holder
client = None

# ------------------------------------------------------------
# WiFi
# ------------------------------------------------------------
def connect_wifi():
    print("[WIFI] Connecting...")
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        utime.sleep(0.2)
    print("[WIFI] Connected:", wlan.ifconfig())

# ------------------------------------------------------------
# MQTT (safe)
# ------------------------------------------------------------
def mqtt_connect():
    global client
    try:
        client = MQTTClient(CLIENT_ID, BROKER, PORT)
        client.connect()
        print("[MQTT] Connected")
    except Exception as e:
        print("[MQTT] Connect failed:", e)
        client = None

def mqtt_publish(topic, value):
    global client
    if client is None:
        # Try to reconnect once
        mqtt_connect()
        if client is None:
            print("[MQTT] Skipping publish, still not connected")
            return
    try:
        print("[MQTT] Publish:", topic, value)
        client.publish(topic, str(value))
    except Exception as e:
        print("[MQTT] Error in publish:", e)

# ------------------------------------------------------------
# Telegram (safe)
# ------------------------------------------------------------
def send_msg(chat_id, text):
    encoded = url_encode(text)
    url = API + "/sendMessage?chat_id={}&text={}&parse_mode=Markdown".format(chat_id, encoded)
    try:
        r = urequests.get(url)
        r.close()
        print("[TG] Sent message to", chat_id)
    except Exception as e:
        print("[TG] Error sending:", e)

def broadcast(text):
    print("[TG] Broadcasting message...")
    for cid in ALLOWED_CHAT_IDS:
        try:
            send_msg(cid, text)
        except Exception as e:
            print("[TG] Broadcast failed:", e)

def send_startup_help():
    print("[SYS] Sending startup help to Telegram...")
    for cid in ALLOWED_CHAT_IDS:
        send_msg(cid, STARTUP_HELP)

def get_updates(offset=None):
    url = API + "/getUpdates?timeout=1"
    if offset:
        url += "&offset=" + str(offset)
    try:
        r = urequests.get(url)
        data = r.json()
        r.close()
        return data.get("result", [])
    except Exception as e:
        print("[TG] get_updates error:", e)
        return []

# ------------------------------------------------------------
# Ultrasonic
# ------------------------------------------------------------
def water_level_cm():
    TRIG.off()
    utime.sleep_us(2)
    TRIG.on()
    utime.sleep_us(10)
    TRIG.off()

    try:
        t = time_pulse_us(ECHO, 1, 30000)
    except Exception as e:
        print("[SENSOR] Ultrasonic timeout:", e)
        return None

    if t <= 0:
        print("[SENSOR] Invalid ultrasonic reading")
        return None

    cm = (t * 0.0343) / 2.0
    print("[TANK] Distance:", cm, "cm")
    return cm

# ------------------------------------------------------------
# Tank Status Based on Distance
# ------------------------------------------------------------
def tank_status(dist):
    if dist is None:
        return "UNKNOWN"

    if 7.6 <= dist <= 8.6:
        return "LOW"
    elif 4.0 <= dist <= 7.5:
        return "MID"
    elif 2.0 <= dist <= 3.9:
        return "HIGH"

# ------------------------------------------------------------
# Pump Control
# ------------------------------------------------------------
def pump_on():
    global WATERING
    if not WATERING:
        PUMP_PIN.value(1)
        WATERING = True
        print("[PUMP] ON")
        broadcast("💧 Pump ON — watering started.")

def pump_off():
    global WATERING
    if WATERING:
        PUMP_PIN.value(0)
        WATERING = False
        print("[PUMP] OFF")
        broadcast("✔ Pump OFF — watering stopped.")

# ------------------------------------------------------------
# HTTP SERVER (safe)
# ------------------------------------------------------------
def start_http_server():
    print("[HTTP] Starting server...")
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.1)
    print("[HTTP] Server started!")
    return s

def parse_query(qs):
    params = {}
    if not qs:
        return params
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    return params

def handle_http_request(sock):
    global watering_interval, watering_duration, auto_watering

    try:
        cl, addr = sock.accept()
    except OSError:
        return

    print("[HTTP] Request from", addr)

    try:
        req = cl.recv(512)
        if not req:
            cl.close()
            return

        line = req.split(b"\r\n", 1)[0]
        parts = line.split()
        path_q = parts[1].decode()

        if "?" in path_q:
            path, qs = path_q.split("?", 1)
        else:
            path, qs = path_q, ""

        params = parse_query(qs)

        if path == "/pump_on":
            print("[HTTP] Pump ON triggered")
            pump_on()
            body = "Pump ON"

        elif path == "/pump_off":
            print("[HTTP] Pump OFF triggered")
            pump_off()
            body = "Pump OFF"

        elif path == "/set_interval":
            sec = params.get("sec", "")
            if sec.isdigit():
                watering_interval = int(sec)
                print("[CONFIG] Interval =", watering_interval)
                broadcast(f"⏱ Interval set to {watering_interval} sec")
                body = "Interval updated"
            else:
                body = "Invalid interval"

        elif path == "/set_duration":
            sec = params.get("sec", "")
            if sec.isdigit():
                watering_duration = int(sec)
                print("[CONFIG] Duration =", watering_duration)
                broadcast(f"💧 Duration set to {watering_duration} sec")
                body = "Duration updated"
            else:
                body = "Invalid duration"

        elif path == "/auto_on":
            auto_watering = True
            print("[AUTO] Automatic watering ENABLED (HTTP)")
            broadcast("🔄 Automatic watering ENABLED")
            body = "Auto watering ON"

        elif path == "/auto_off":
            auto_watering = False
            print("[AUTO] Automatic watering DISABLED (HTTP)")
            broadcast("⛔ Automatic watering DISABLED")
            body = "Auto watering OFF"

        elif path == "/tank":
            body = f"Tank: {last_tank_status} ({last_tank_cm} cm)"

        else:
            body = "Unknown endpoint"

        try:
            cl.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
            cl.send(body)
        except Exception as e:
            print("[HTTP] Send failed:", e)

    except Exception as e:
        print("[HTTP] Error:", e)

    finally:
        try:
            cl.close()
        except:
            pass

# ------------------------------------------------------------
# Telegram Commands
# ------------------------------------------------------------
def handle_command(chat_id, text):
    global watering_interval, watering_duration, auto_watering

    print("[TG] Command from", chat_id, ":", text)

    t = text.lower().strip()

    if t == "/start":
        send_msg(chat_id, STARTUP_HELP)

    elif t == "/tank":
        send_msg(chat_id, f"Tank: {last_tank_status} ({last_tank_cm} cm)")

    elif t == "/status":
        send_msg(chat_id,
            f"Temp: {temperature}°C\n"
            f"Humidity: {humidity}%\n"
            f"Pressure: {pressure} hPa\n"
            f"Tank: {last_tank_status} ({last_tank_cm} cm)\n"
            f"Interval: {watering_interval}s\n"
            f"Duration: {watering_duration}s\n"
            f"Auto-Watering: {'ON' if auto_watering else 'OFF'}"
        )

    elif t.startswith("/setwater"):
        parts = t.split()
        if len(parts) == 2 and parts[1].isdigit():
            watering_interval = int(parts[1])
            send_msg(chat_id, f"Interval set to {watering_interval} sec")
        else:
            send_msg(chat_id, "Usage: /setwater <seconds>")

    elif t.startswith("/setduration"):
        parts = t.split()
        if len(parts) == 2 and parts[1].isdigit():
            watering_duration = int(parts[1])
            send_msg(chat_id, f"Duration set to {watering_duration} sec")
        else:
            send_msg(chat_id, "Usage: /setduration <seconds>")

    elif t == "/auto_on":
        auto_watering = True
        send_msg(chat_id, "🔄 Automatic watering ENABLED")

    elif t == "/auto_off":
        auto_watering = False
        send_msg(chat_id, "⛔ Automatic watering DISABLED")

# ------------------------------------------------------------
# MAIN LOOP (Safe Mode)
# ------------------------------------------------------------
def main():
    global last_tank_status, last_tank_cm
    global temperature, humidity, pressure
    global auto_watering

    connect_wifi()
    mqtt_connect()
    http_sock = start_http_server()

    # Force pump OFF once at startup (no repeated OFF in loop)
    print("[SYS] Forcing pump OFF at startup...")
    pump_off()

    # send startup message
    send_startup_help()

    last_id = None
    last_water_time = 0

    print("[SYS] System is running... Auto-watering is OFF by default.")

    while True:
        try:
            # --------- Sensor Reading ---------
            try:
                dht_sensor.measure()
                temperature = dht_sensor.temperature()
                humidity = dht_sensor.humidity()
            except Exception as e:
                print("[SENSOR] DHT error:", e)
                temperature = None
                humidity = None

            try:
                pressure = bmp.pressure / 100
            except Exception as e:
                print("[SENSOR] BMP280 error:", e)
                pressure = None

            dist = water_level_cm()
            status = tank_status(dist)

            last_tank_cm = dist
            last_tank_status = status

            print(
                f"[SENSOR] Temp:{temperature}C Hum:{humidity}% "
                f"Press:{pressure}hPa Tank:{status} ({dist} cm) "
                f"Auto:{'ON' if auto_watering else 'OFF'}"
            )

            # --------- LCD Update ---------
            try:
                lcd.clear()
                lcd.putstr("Tank:{}\nAuto:{}".format(
                    status, "ON" if auto_watering else "OFF"
                ))
            except Exception as e:
                print("[LCD] Error:", e)

            # --------- MQTT Publish ---------
            mqtt_publish(TOPIC_WATER_STATUS, status)
            mqtt_publish(TOPIC_WATER_CM, dist)
            mqtt_publish(TOPIC_TEMP, temperature)
            mqtt_publish(TOPIC_HUM, humidity)
            mqtt_publish(TOPIC_PRESS, pressure)

            # --------- Automatic Watering ---------
            now = utime.time()

            if auto_watering:
                if status == "LOW":
                    print("[AUTO] Tank LOW — watering stopped/prevented")
                    pump_off()

                    # NEW: Send Telegram alert on LOW tank
                    broadcast("⚠️ *Tank LOW!* Please refill the water tank.")
                elif status in ("MID", "HIGH"):
                    if now - last_water_time >= watering_interval:
                        print("[AUTO] Watering cycle started!")
                        pump_on()
                        utime.sleep(watering_duration)
                        pump_off()
                        last_water_time = now
                else:
                    # UNKNOWN level: be safe, do not water automatically
                    print("[AUTO] Tank UNKNOWN — skipping watering")
                    pump_off()
            # NOTE: no "else: pump_off()" here anymore,
            # so manual /pump_on will NOT be forced off.

            # --------- Telegram Commands ---------
            try:
                updates = get_updates(last_id + 1 if last_id else None)
                for u in updates:
                    last_id = u["update_id"]
                    msg = u.get("message", {})
                    chat_id = msg.get("chat", {}).get("id")
                    text = msg.get("text", "")
                    if chat_id in ALLOWED_CHAT_IDS:
                        handle_command(chat_id, text)
            except Exception as e:
                print("[TG] Update handling error:", e)

            # --------- MIT App HTTP Actions ---------
            handle_http_request(http_sock)

            utime.sleep(0.2)

        except Exception as e:
            # Catch ANY unexpected error in this loop and continue
            print("[LOOP ERROR]", e)
            utime.sleep(0.5)

# ------------------------------------------------------------
# RUN SYSTEM
# ------------------------------------------------------------
try:
    main()
except Exception as e:
    print("[FATAL]", e)
    utime.sleep(3)
    reset()

