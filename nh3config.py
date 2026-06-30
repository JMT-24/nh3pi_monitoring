"""
Central config for the staging control loop (controller.py).

TESTING PHASE — read before running:
  * Requires a populated .env (BACKEND_URL + INGEST_API_KEY).  See .env.example.
  * Relay pins below MUST match your physical 4-channel relay wiring before you
    let controller.py drive the pump/valve. Defaults are placeholders.
  * Nothing here imports hardware, so it is safe to import on a laptop.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


# ---- Backend ---------------------------------------------------------------
# BACKEND_URL is the base (e.g. http://192.168.4.10:4000). The control loop
# talks to {BACKEND_URL}/api/ingest (live) and {BACKEND_URL}/api/ingest/batch
# (offline backfill). NOTE: this changed from the old main.py, which posted to
# a bare /readings endpoint that the current backend does not expose.
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:4000").rstrip("/")
INGEST_API_KEY = os.getenv("INGEST_API_KEY", "")  # sent as the x-api-key header
HTTP_TIMEOUT = _f("HTTP_TIMEOUT", "5")             # seconds per request

DEVICE_ID = os.getenv("DEVICE_ID", "pi-01")
FW_VERSION = os.getenv("FW_VERSION", "0.5.0-staging")

# ---- Loop timing -----------------------------------------------------------
SEND_INTERVAL = _i("SEND_INTERVAL", 30)            # seconds between sensor frames
BACKFILL_BATCH = _i("BACKFILL_BATCH", 50)          # max buffered rows per replay

# ---- ADC / sensors ---------------------------------------------------------
VREF = _f("VREF", "5.0")
ADC_RESOLUTION = 1023.0          # MCP3008 is 10-bit
SPI_BUS = 0
SPI_DEVICE = 0
SPI_MAX_HZ = 1350000
SAMPLES_PER_READ = _i("SAMPLES_PER_READ", 10)

NH3_CHANNEL = _i("NH3_CHANNEL", 2)   # MQ137 -> MCP3008 CH2
PH_CHANNEL = _i("PH_CHANNEL", 0)     # PH-4502C -> MCP3008 CH0

# pH calibration (from archive/sensors/ph_sensor.py). The Pi does this light
# conversion; the backend works in pH/volts and owns the danger thresholds.
PH_NEUTRAL_VOLTAGE = _f("PH_NEUTRAL_VOLTAGE", "2.549")
PH_SLOPE = _f("PH_SLOPE", "3.5")

DS18B20_DISCONNECTED_C = -127.0      # sentinel the backend recognises as offline

# ---- Actuators / relay -----------------------------------------------------
# BCM pin numbers. CHANGE THESE to match your wiring before driving anything.
PUMP_PIN = _i("PUMP_PIN", 5)
VALVE_PIN = _i("VALVE_PIN", 6)
# Most cheap relay boards are ACTIVE-LOW (GPIO low = relay energised / load ON).
RELAY_ACTIVE_LOW = _b("RELAY_ACTIVE_LOW", True)

# Hybrid-safety fallback: if the loop applies an active (pump-on) command and
# then gets no fresh command for this many seconds, force everything OFF. The
# backend sends its own maxRuntimeSec per command; this is the floor used when
# offline or if the command omits one.
DEFAULT_MAX_RUNTIME_SEC = _i("DEFAULT_MAX_RUNTIME_SEC", 120)

# ---- Offline buffer --------------------------------------------------------
BUFFER_DB = os.getenv("BUFFER_DB", "buffer.db")    # local SQLite store on the Pi


def ingest_url():
    return f"{BACKEND_URL}/api/ingest"


def batch_url():
    return f"{BACKEND_URL}/api/ingest/batch"
