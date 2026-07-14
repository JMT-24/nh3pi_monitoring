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

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


def _b(name, default):
    """
    Parse a boolean env var STRICTLY.

    A permissive `value in (...)` reading silently returned False for anything it
    didn't recognise — including `Y`, `TRUE!`, or an empty value left by an edit. On
    RELAY_ACTIVE_LOW that flips the relay polarity, and it flips it toward DANGER:
    the board then energises when the code believes it is switching the pump off, so
    the pump runs continuously while the dashboard reports `pump=off`. A typo must
    not be able to do that silently, so an unrecognised value is a hard error.
    """
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    v = raw.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(
        f"{name}={raw!r} is not a valid boolean. Use one of {_TRUE + _FALSE}. "
        f"(Refusing to guess: on RELAY_ACTIVE_LOW a wrong guess inverts relay polarity.)"
    )


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
# VREF must match how the MCP3008 VDD/VREF pins are wired. Ours go to the Pi's
# 3.3V rail (see the pinout), so the raw->voltage math uses 3.3, NOT 5.0. Set to
# 5.0 only if you rewire VREF to a 5V source.
VREF = _f("VREF", "3.3")
ADC_RESOLUTION = 1023.0          # MCP3008 is 10-bit
SPI_BUS = 0
SPI_DEVICE = 0
SPI_MAX_HZ = 1350000
SAMPLES_PER_READ = _i("SAMPLES_PER_READ", 10)

NH3_CHANNEL = _i("NH3_CHANNEL", 2)   # MQ137 -> MCP3008 CH2
PH_CHANNEL = _i("PH_CHANNEL", 0)     # PH-4502C -> MCP3008 CH0

# Sentinel reported when the MCP3008/SPI bus itself is unreadable. Negative, because
# a real reading is always >= 0 — the backend keys off `< 0` to raise an ADC-fault
# alarm. NEVER substitute 0.0 here: 0 V reads as pristine water on NH3 (suppressing
# every alarm and the water exchange) and as pH 14 (a false critical).
ADC_INVALID_V = -1.0
ADC_INVALID_RAW = -1.0

# pH calibration (from archive/sensors/ph_sensor.py). The Pi does this light
# conversion; the backend works in pH/volts and owns the danger thresholds.
PH_NEUTRAL_VOLTAGE = _f("PH_NEUTRAL_VOLTAGE", "2.549")
PH_SLOPE = _f("PH_SLOPE", "3.5")

DS18B20_DISCONNECTED_C = -127.0      # sentinel the backend recognises as offline

# ---- Water level (JSN-SR04T ultrasonic, Trig/Echo mode) --------------------
# Distance from the sensor down to the water surface, in cm (grows as the level
# drops). ECHO is 5 V and MUST reach GPIO through a 5V->3.3V divider (see docs).
# The backend owns the level baseline/marks (calibration); the Pi just measures.
LEVEL_TRIG_PIN = _i("LEVEL_TRIG_PIN", 23)          # BCM (physical pin 16)
LEVEL_ECHO_PIN = _i("LEVEL_ECHO_PIN", 24)          # BCM (physical pin 18) via divider
LEVEL_MAX_DISTANCE_CM = _f("LEVEL_MAX_DISTANCE_CM", "100")  # sensor usable range
LEVEL_SAMPLES = _i("LEVEL_SAMPLES", 5)             # median over N spaced-out reads
# gpiozero smooths internally: it pings in a background thread and `.distance` returns
# the mean of this queue. Our own sampling must be spaced out (below) or it just reads
# the same cached mean repeatedly.
LEVEL_QUEUE_LEN = _i("LEVEL_QUEUE_LEN", 9)         # gpiozero's smoothing window
LEVEL_SAMPLE_GAP_S = _f("LEVEL_SAMPLE_GAP_S", "0.06")  # gap so the queue actually refreshes
LEVEL_INVALID_CM = -1.0                            # sentinel: no valid echo

# ---- Actuators / relay -----------------------------------------------------
# BCM pin numbers, matching the wired relay board: IN1 (pump/drain) -> GPIO17
# (physical pin 11), IN2 (valve/intake) -> GPIO27 (physical pin 13). IN3/IN4
# (GPIO22/GPIO5) are spares. Change these only if you rewire the relay inputs.
PUMP_PIN = _i("PUMP_PIN", 17)
VALVE_PIN = _i("VALVE_PIN", 27)
# Most cheap relay boards are ACTIVE-LOW (GPIO low = relay energised / load ON).
RELAY_ACTIVE_LOW = _b("RELAY_ACTIVE_LOW", True)

# Hybrid-safety fallback: if the loop applies an active (pump-on) command and
# then gets no fresh command for this many seconds, force everything OFF. The
# backend sends its own maxRuntimeSec per command; this is the floor used when
# offline or if the command omits one.
DEFAULT_MAX_RUNTIME_SEC = _i("DEFAULT_MAX_RUNTIME_SEC", 120)

# ---- Offline buffer --------------------------------------------------------
# Resolved against THIS FILE's directory, not the process CWD. A relative path meant
# that running from cron (or a systemd unit with no WorkingDirectory=) silently opened
# a DIFFERENT, empty database: count_pending() reported 0 while every frame buffered by
# the previous invocation was stranded on disk forever. An absolute BUFFER_DB is used
# as-is.
_BUFFER_DB_RAW = os.getenv("BUFFER_DB", "buffer.db")
BUFFER_DB = (
    _BUFFER_DB_RAW
    if os.path.isabs(_BUFFER_DB_RAW)
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), _BUFFER_DB_RAW)
)

# Keep at most this many synced rows in the offline buffer; older ones are pruned each
# cycle. Without pruning the buffer grew ~2,880 rows/day forever until the SD card
# filled and the loop died.
BUFFER_KEEP_SYNCED = _i("BUFFER_KEEP_SYNCED", 5000)
# Hard ceiling on PENDING rows (a backend that never accepts anything). Oldest are
# dropped past this — the alternative is filling the disk and losing monitoring entirely.
BUFFER_MAX_PENDING = _i("BUFFER_MAX_PENDING", 50000)


def ingest_url():
    return f"{BACKEND_URL}/api/ingest"


def batch_url():
    return f"{BACKEND_URL}/api/ingest/batch"
