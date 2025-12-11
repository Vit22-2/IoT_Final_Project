# ------------------------------------------------------------
# ESP32 Water Monitoring System
# LCD + Telegram + MQTT + Automatic Watering + HTTP (MIT App)
# User can set watering interval & duration via Telegram or MIT App
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
ALLOWED_CHAT_IDS = {1128192910, -4716959086}
API = "http://api.telegram.org/bot" + BOT_TOKEN

# ------------------------------------------------------------
# MQTT Configuration
# ------------------------------------------------------------
BROKER = "test.mosquitto.org"
PORT = 1883
CLIENT_ID = b"esp32_water_monitor"

TOPIC_WATER_PERCENT = b"/aupp/group1/water_percent"
TOPIC_WATER_CM      = b"/aupp/group1/water_cm"
TOPIC_TEMP          = b"/aupp/group1/temperature"
TOPIC_HUM           = b"/aupp/group1/humidity"
TOPIC_PRESS         = b"/aupp/group1/pressure"

# ------------------------------------------------------------
# Hardware Pins
# ------------------------------------------------------------
DHT_PIN = 4
TRIG_PIN = 27
ECHO_PIN = 26
PUMP_PIN = Pin(14, Pin.OUT)

I2C_SDA = 21
I2C_SCL = 22
LCD_ADDR = 0x27

# ------------------------------------------------------------
# Water Tank Calibration
# ------------------------------------------------------------
TANK_FULL_CM  = 3.0   # 100%
TANK_EMPTY_CM = 8.6   # 0%

def water_percent(dist):
    if dist is None:
        return None
    if dist >= TANK_EMPTY_CM:
        return 0
    if dist <= TANK_FULL_CM:
        return 100
    percent = (TANK_EMPTY_CM - dist) / (TANK_EMPTY_CM - TANK_FULL_CM) * 100
    return int(percent)

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

# ------------------------------------------------------------
# Track last tank values (HTTP /tank)
# ------------------------------------------------------------
last_tank_percent = None
last_tank_cm = None

# ------------------------------------------------------------
# WiFi
# ------------------------------------------------------------
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    while not wlan.isconnected():
        utime.sleep(0.2)
    print("WiFi OK:", wlan.ifconfig())

# ------------------------------------------------------------
# MQTT
# ------------------------------------------------------------
def mqtt_connect():
    global client
    client = MQTTClient(CLIENT_ID, BROKER, PORT)
    client.connect()
    print("MQTT Connected")

def mqtt_publish(topic, value):
    client.publish(topic, str(value))

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------
def send_msg(chat_id, text):
    encoded = url_encode(text)
    url = API + "/sendMessage?chat_id={}&text={}".format(chat_id, encoded)
    try:
        r = urequests.get(url)
        r.close()
    except:
        pass

def broadcast(text):
    for cid in ALLOWED_CHAT_IDS:
        send_msg(cid, text)

def get_updates(offset=None):
    url = API + "/getUpdates?timeout=1"
    if offset:
        url += "&offset=" + str(offset)
    try:
        r = urequests.get(url)
        data = r.json()
        r.close()
        return data.get("result", [])
    except:
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
    except:
        return None
    if t <= 0:
        return None
    return (t * 0.0343) / 2.0

# ------------------------------------------------------------
# Pump Control
# ------------------------------------------------------------
WATERING = False

def pump_on():
    global WATERING
    if not WATERING:
        PUMP_PIN.on()
        WATERING = True
        broadcast("💧 Pump ON — watering started.")
        print("Pump ON")

def pump_off():
    global WATERING
    if WATERING:
        PUMP_PIN.off()
        WATERING = False
        broadcast("✔ Pump OFF — watering stopped.")
        print("Pump OFF")

# ------------------------------------------------------------
# User Adjustable Values
# ------------------------------------------------------------
watering_interval = 3600   # seconds
watering_duration = 5      # seconds

# ------------------------------------------------------------
# HTTP SERVER (MIT App)
# ------------------------------------------------------------
def start_http_server():
    addr = socket.getaddrinfo("0.0.0.0", 80)[0][-1]
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(addr)
    s.listen(1)
    s.settimeout(0.1)
    print("HTTP server started on port 80")
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
    global watering_interval, watering_duration

    try:
        cl, addr = sock.accept()
    except OSError:
        return

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

        # -------------------------
        # MIT APP ACTION ROUTES
        # -------------------------

        if path == "/pump_on":
            pump_on()
            broadcast("📱 MIT App: Pump ON")
            print("MIT App → Pump ON")
            body = "Pump ON"

        elif path == "/pump_off":
            pump_off()
            broadcast("📱 MIT App: Pump OFF")
            print("MIT App → Pump OFF")
            body = "Pump OFF"

        elif path == "/set_interval":
            sec = params.get("sec", "")
            if sec.isdigit():
                watering_interval = int(sec)
                print("MIT App → Interval:", watering_interval)
                broadcast(f"📱 MIT App: Interval set to {watering_interval} sec")
                body = "Interval updated"
            else:
                body = "Invalid interval"

        elif path == "/set_duration":
            sec = params.get("sec", "")
            if sec.isdigit():
                watering_duration = int(sec)
                print("MIT App → Duration:", watering_duration)
                broadcast(f"📱 MIT App: Duration set to {watering_duration} sec")
                body = "Duration updated"
            else:
                body = "Invalid duration"

        elif path == "/tank":
            body = "Tank: {}% ({} cm)".format(last_tank_percent, last_tank_cm)

        else:
            body = "Unknown endpoint"

        cl.send("HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n")
        cl.send(body)

    except Exception as e:
        print("HTTP Error:", e)
    finally:
        cl.close()

# ------------------------------------------------------------
# Telegram Commands
# ------------------------------------------------------------
def handle_command(chat_id, text):
    global watering_interval, watering_duration

    t = text.lower()

    if t == "/start":
        send_msg(chat_id,
            "Commands:\n"
            "/status\n/tank\n"
            "/setwater <sec>\n/setduration <sec>"
        )

    elif t == "/tank":
        dist = water_level_cm()
        percent = water_percent(dist)
        send_msg(chat_id, f"Tank Level: {percent}%")

    elif t == "/status":
        dht_sensor.measure()
        dist = water_level_cm()
        percent = water_percent(dist)
        send_msg(chat_id,
            f"Temp: {dht_sensor.temperature()}°C\n"
            f"Humidity: {dht_sensor.humidity()}%\n"
            f"Pressure: {bmp.pressure/100:.1f} hPa\n"
            f"Tank Level: {percent}%\n"
            f"Interval: {watering_interval}s\n"
            f"Duration: {watering_duration}s"
        )

    elif t.startswith("/setwater"):
        parts = t.split()
        if len(parts) == 2 and parts[1].isdigit():
            watering_interval = int(parts[1])
            send_msg(chat_id, f"⏱ Interval set to {watering_interval} sec")
        else:
            send_msg(chat_id, "Usage: /setwater <seconds>")

    elif t.startswith("/setduration"):
        parts = t.split()
        if len(parts) == 2 and parts[1].isdigit():
            watering_duration = int(parts[1])
            send_msg(chat_id, f"💧 Duration set to {watering_duration} sec")
        else:
            send_msg(chat_id, "Usage: /setduration <seconds>")

# ------------------------------------------------------------
# MAIN LOOP
# ------------------------------------------------------------
def main():
    global last_tank_percent, last_tank_cm

    connect_wifi()
    mqtt_connect()
    http_sock = start_http_server()

    last_id = None
    last_water_time = 0

    while True:
        # Read sensors
        dht_sensor.measure()
        temperature = dht_sensor.temperature()
        humidity = dht_sensor.humidity()
        pressure = bmp.pressure / 100
        dist = water_level_cm()
        percent = water_percent(dist)

        last_tank_cm = dist
        last_tank_percent = percent

        # LCD Update
        lcd.clear()
        lcd.putstr("Tank Level {}%".format(percent))

        # MQTT Publish
        mqtt_publish(TOPIC_WATER_PERCENT, percent)
        mqtt_publish(TOPIC_WATER_CM, dist)
        mqtt_publish(TOPIC_TEMP, temperature)
        mqtt_publish(TOPIC_HUM, humidity)
        mqtt_publish(TOPIC_PRESS, pressure)

        # Automatic Watering
        now = utime.time()

        if percent is not None and percent < 15:
            pump_off()
            broadcast("⚠ Tank LOW — watering CANCELLED")
        else:
            if now - last_water_time >= watering_interval:
                pump_on()
                utime.sleep(watering_duration)
                pump_off()
                last_water_time = now

        # Telegram Commands
        updates = get_updates(last_id + 1 if last_id else None)
        for u in updates:
            last_id = u["update_id"]
            msg = u.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            if chat_id in ALLOWED_CHAT_IDS:
                handle_command(chat_id, text)

        # MIT App HTTP Actions
        handle_http_request(http_sock)

        utime.sleep(0.2)

# ------------------------------------------------------------
# RUN SYSTEM
# ------------------------------------------------------------
try:
    main()
except Exception as e:
    print("Fatal Error:", e)
    utime.sleep(3)
    reset()
