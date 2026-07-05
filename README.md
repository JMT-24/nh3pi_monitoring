# 🐟 NH3 Pi Monitoring

Raspberry Pi code for the **Automated Water Refill and Ammonia Nitrogen (NH3) Monitoring-Notification System**. This handles all sensor reading and forwards raw data to the backend for processing.

---

## 📁 Project Structure

```
nh3_monitoring/
├── controller.py     # Staging entrypoint — full control loop (sensors + buffer + control)
├── sensors.py        # Sensor acquisition -> backend-shaped ingest frame
├── actuators.py      # Relay control (pump/valve) + safety watchdog
├── backend_client.py # POST /api/ingest and /api/ingest/batch (x-api-key)
├── buffer.py         # Local SQLite offline buffer (replayed when back online)
├── nh3config.py      # Central config (reads .env)
├── main.py           # OLD simple sender (legacy — posts to /readings; do not use)
├── main_demo.py      # Demo mode — fake data, no hardware
├── requirements.txt  # Python dependencies
├── .env              # Your local config/secrets (never pushed) — see .env.example
└── archive/          # Old standalone sensor test scripts (not used in production)
```

---

## ⚙️ How It Works

Each cycle (every `SEND_INTERVAL` seconds) `controller.py` reads all sensors into a
backend-shaped frame, buffers it locally first, then POSTs it to `POST /api/ingest`
with the `x-api-key` header. The backend echoes back the desired pump/valve command,
which the Pi applies (with a local safety watchdog that forces OFF if commands stop
arriving). Frames captured while offline are replayed to `/api/ingest/batch`.

Calibration/thresholds live on the **backend** (editable from the dashboard). The Pi
only does light math (voltage, pH) — it does not compute NH3 ppm.

| Sensor | Module | Interface |
|---|---|---|
| Temperature | DS18B20 (waterproof) | 1-Wire on GPIO4 |
| Water level | JSN-SR04T ultrasonic | TRIG GPIO23 / ECHO GPIO24 (via 5V→3.3V divider) |
| Ammonia (NH3) | MQ137 | MCP3008 CH2 (SPI) |
| pH | PH-4502C | MCP3008 CH0 (SPI) |

---

## 🔌 Hardware Requirements

| Component | Specification |
|---|---|
| Raspberry Pi | Pi 4 / Pi 5 |
| MQ137 NH3 Sensor | Ammonia gas sensor module → MCP3008 CH2 |
| pH Sensor Kit | PH-4502C with probe → MCP3008 CH0 |
| DS18B20 | Waterproof temp sensor (needs 4.7kΩ pull-up on DATA→3.3V) |
| JSN-SR04T | Ultrasonic water-level sensor (ECHO needs a 5V→3.3V divider) |
| MCP3008 | ADC converter (SPI); VDD/VREF wired to **3.3V** |
| Relay Module | 4-channel 5V (IN1=GPIO17 pump, IN2=GPIO27 valve) |

---

## 🚀 Setup

### 1. Clone the repo
```bash
git clone https://github.com/JMT-24/nh3pi_monitoring.git
cd nh3pi_monitoring
```

### 2. Create + activate a virtualenv, then install dependencies
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
> ⚠️ Always run inside the venv. Running with the system Python misses
> `w1thermsensor` (temp reads `-127`) and other libs.

> **lgpio note:** `gpiozero`'s `DistanceSensor` (water level) needs the **lgpio**
> pin factory on Pi OS Bookworm/Pi 5 — RPi.GPIO's edge detection fails there. If the
> pip build can't find the library, install its build deps first:
> `sudo apt install -y swig python3-dev liblgpio-dev`, then `pip install lgpio`.

### 3. Enable SPI + 1-Wire
```bash
sudo raspi-config
# Interface Options → SPI    → Enable
# Interface Options → 1-Wire → Enable
sudo reboot
```

### 4. Create your `.env` file
```bash
cp .env.example .env
nano .env
```
Set at minimum:
```
BACKEND_URL=http://<backend-ip>:4000     # base URL only — NO /readings path
INGEST_API_KEY=<must match the backend's INGEST_API_KEY>
```
Wiring-dependent values (`VREF=3.3`, `PUMP_PIN=17`, `VALVE_PIN=27`) already default
correctly in `.env.example`.

### 5. Run
```bash
source venv/bin/activate      # if not already active
python controller.py
```
Look for `[OK]` lines. `[OFFLINE]` = can't reach the backend (check IP/port/firewall);
`HTTP 401` = the API key doesn't match the backend.

---

## 🔄 Auto-Update via Cron

This Pi is configured to auto-pull from GitHub every 5 minutes so changes pushed from a laptop are applied automatically — no SSH needed.

```bash
crontab -e
# */5 * * * * /home/nh3/nh3_monitoring/git_pull.sh
```

---

## 📦 Key Dependencies

```
requests          # HTTP to the backend
python-dotenv     # .env loading
spidev            # MCP3008 (NH3 + pH)
w1thermsensor     # DS18B20 temperature (1-Wire)
gpiozero          # relays + JSN-SR04T level sensor
lgpio             # pin factory gpiozero needs for edge detection (Bookworm/Pi 5)
```
Full pinned list in `requirements.txt`.

---

## 🔗 Related Repos

| Part | Repo |
|---|---|
| Backend | `nh3-monitor-backend` |
| Frontend | `nh3-monitor-frontend` |

---

## 👨‍💻 Developer

**Charles Brian C. Mitra**  
Project: Automated Water Refill and NH3 Monitoring-Notification System