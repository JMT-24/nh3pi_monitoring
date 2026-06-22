# 🐟 NH3 Pi Monitoring

Raspberry Pi code for the **Automated Water Refill and Ammonia Nitrogen (NH3) Monitoring-Notification System**. This handles all sensor reading and forwards raw data to the backend for processing.

---

## 📁 Project Structure

```
nh3_monitoring/
├── main.py           # Live mode — reads real sensors and sends to backend
├── main_demo.py      # Demo mode — sends fake data for testing without hardware
├── requirements.txt  # Python dependencies
├── .env              # Your local secrets (never pushed)
├── .gitignore
└── archive/          # Old standalone sensor test scripts (not used in production)
    ├── sensors/
    │   ├── amonia_sensor.py
    │   ├── ph_sensor.py
    │   └── temperature.py
    ├── actuators/
    └── notifications/
```

---

## ⚙️ How It Works

The Pi reads from three sensors every 30 seconds and sends **raw ADC values** to the backend via HTTP POST. Calibration math (pH conversion, NH3 ppm calculation) happens on the backend — not here.

| Sensor | Module | Channel |
|---|---|---|
| Temperature | DS18B20 (1-Wire) | — |
| pH | PH-4502C via MCP3008 | CH0 |
| Ammonia (NH3) | MQ137 via MCP3008 | CH2 |

---

## 🔌 Hardware Requirements

| Component | Specification |
|---|---|
| Raspberry Pi | Pi 4 (4GB recommended) |
| MQ137 NH3 Sensor | Ammonia gas sensor module |
| pH Sensor Kit | PH-4502C with probe |
| DS18B20 | Waterproof temperature sensor |
| MCP3008 | ADC converter (SPI) |
| Relay Module | 4-channel 5V |
| Water Pump | 12V submersible |

---

## 🚀 Setup

### 1. Clone the repo
```bash
git clone https://github.com/JMT-24/nh3pi_monitoring.git
cd nh3pi_monitoring
```

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Create your `.env` file
```bash
nano .env
```
Add:
```
BACKEND_URL=http://your-backend-url/readings
```

### 4. Enable SPI on the Pi
```bash
sudo raspi-config
# Interface Options → SPI → Enable
```

### 5. Run
```bash
# Live mode (real sensors)
python main.py

# Demo mode (fake data, no hardware needed)
python main_demo.py
```

---

## 🔄 Auto-Update via Cron

This Pi is configured to auto-pull from GitHub every 5 minutes so changes pushed from a laptop are applied automatically — no SSH needed.

```bash
crontab -e
# */5 * * * * /home/nh3/nh3_monitoring/git_pull.sh
```

---

## 📦 Dependencies

```
requests
python-dotenv
w1thermsensor
spidev
```

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