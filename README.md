# Smart_Watering_Monitoring_System

## Features
- IoT Smart Plant Care System using ESP32 + MicroPython.
- Measures temperature, humidity, pressure, and tank water level.
- Automatic watering with configurable interval & duration.
- Telegram Bot control (/status, /tank, /setwater, /setduration, pump ON/OFF).
- Sends alerts: Pump ON, Pump OFF, Low Tank.
- MIT App Inventor HTTP control endpoints.
- MQTT publishing to Mosquitto broker.
- LCD display shows real-time data.
- Wi-Fi auto-connect, safe reboot, and low-tank failsafe.

## Requirements

### Hardware
- ESP32 Dev Board (MicroPython)
- DHT11 Sensor
- BMP280 (I2C)
- HC-SR04 Ultrasonic Sensor
- Water Pump + driver (MOSFET/Relay)
- I2C LCD (16x2 or 20x4)
- Water tank + tubing
- Breadboard & jumper wires

### Software
- Thonny IDE
- MIT App Inventor
- Telegram Bot (BotFather)
- test.mosquitto.org or local MQTT broker
- Node-RED or Grafana (optional)
- Wi-Fi access point

## Wiring

| ESP32 Pin | Device | Function |
|----------|--------|----------|
| GPIO4    | DHT11 | Temp/Humidity |
| GPIO21   | SDA | I2C LCD + BMP280 |
| GPIO22   | SCL | I2C LCD + BMP280 |
| GPIO27   | TRIG | Ultrasonic TRIG |
| GPIO26   | ECHO | Ultrasonic ECHO |
| GPIO14   | Pump Driver | Pump ON/OFF |
| GND      | All | Common Ground |

(Add images if needed)

## Usage Instructions

### 1. Telegram Bot Commands

| Command | Description |
|--------|-------------|
| /start | Show menu |
| /status | Temperature, humidity, pressure, interval, duration |
| /tank | Show tank water % |
| /setwater NN | Set watering interval |
| /setduration NN | Set pump duration |

Automatic Alerts:
- 💧 Pump ON
- ✔ Pump OFF
- ⚠ Tank LOW — watering cancelled

### 2. MIT App HTTP Endpoints

| Endpoint | Description |
|---------|-------------|
| /pump_on | Turn pump ON |
| /pump_off | Turn pump OFF |
| /set_interval?sec=NN | Update interval |
| /set_duration?sec=NN | Update duration |
| /tank | Return tank % and cm |

Example:


### 3. MQTT Topics

| Topic | Payload |
|--------|---------|
| /aupp/group1/water_percent | Tank % |
| /aupp/group1/water_cm | Distance CM |
| /aupp/group1/temperature | Temperature |
| /aupp/group1/humidity | Humidity |
| /aupp/group1/pressure | Pressure |


## Automatic Watering Logic

1. Read sensors  
2. If tank < 15% → stop pump + alert  
3. If interval passed → water plant  
4. Run pump for watering_duration seconds  
5. Send pump on/off notifications  

Failsafe:
- Tank low = watering disabled

## Screenshots
(Add Node-RED, Grafana, MIT App screenshots)

## Demo Video
(Add YouTube link)


## Notes
- Keep Telegram Bot Token private.
- Pump may require separate power supply.
- MQTT broker can be changed to a private one.





