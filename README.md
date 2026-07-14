# 🐟 NH3 Pi Monitoring

Raspberry Pi code for the **Automated Water Refill and Ammonia Nitrogen (NH3) Monitoring-Notification System**. This handles all sensor reading and forwards raw data to the backend for processing.

---

## 📁 Project Structure

```
nh3pi_monitoring/
├── controller.py       # ▶ Staging entrypoint — the real control loop (run this)
├── controller_demo.py  # ▶ Hardware-free demo of the full pipeline (run this to test)
├── sensors.py          # Sensor acquisition -> backend-shaped ingest frame
├── actuators.py        # Relay control (pump/valve) + safety watchdog
├── backend_client.py   # POST /api/ingest and /api/ingest/batch (x-api-key)
├── buffer.py           # Local SQLite offline buffer (replayed when back online)
├── nh3config.py        # Central config (reads .env)
├── tests/
│   └── test_mcp3008_nh3.py  # MCP3008 + MQ137 wiring check (--scan, --warmup, --vref)
├── gitpull.sh          # Cron auto-pull script
├── main.py             # ⛔ LEGACY — old /readings contract; refuses to run
├── main_demo.py        # ⛔ LEGACY — old contract + response schema; refuses to run
├── requirements.txt    # Python dependencies
├── .env                # Your local config/secrets (never pushed) — see .env.example
└── archive/            # Old standalone scripts — reference only (see archive/README.md)
```

> **`main.py` and `main_demo.py` are dead.** They speak the original `/readings` contract,
> which the backend no longer exposes — every frame would 404/401, with no actuator
> control, no offline buffer and no safety watchdog. Both now exit with a pointer rather
> than pretending to work. Use `controller.py` / `controller_demo.py`.

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

The Pi auto-pulls from GitHub every 5 minutes, so code pushed from a laptop lands without SSH.

```bash
crontab -e
# */5 * * * * /home/nh3/nh3pi_monitoring/gitpull.sh >> /home/nh3/gitpull.log 2>&1
```

Use the **real path to your clone** — `gitpull.sh` resolves the repo from its own location,
so it works wherever the repo lives, but cron still needs the correct path to the script.

> ⚠️ **This only updates files on disk — it does NOT restart `controller.py`.** A running
> loop keeps executing the old code until you restart it. If you want true auto-apply, run
> the loop under systemd and have the pull restart the unit:
> ```bash
> # in gitpull.sh, after `git pull`:
> #   sudo systemctl restart nh3-controller
> ```
> A systemd unit is also what restarts the loop if it ever exits — cron does not.

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