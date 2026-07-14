"""
Sensor acquisition + light calibration for the staging control loop.

Produces an ingest frame that matches the backend contract EXACTLY
(nh3_backend/src/validation.ts -> ingestSchema):

    {
      "nh3":        {"raw": <float>, "voltage": <float>},   # -1 == ADC unreadable
      "ph":         {"voltage": <float>, "pH": <float>},    # -1 == ADC unreadable
      "waterTemp":  {"tempC": <float>, "tempF": <float>},   # -127 == disconnected
      "waterLevel": {"distanceCm": <float>}                 # <= 0 == invalid
    }

Calibration split: the Pi computes voltage + pH here (cheap math); the backend
owns the danger thresholds and works in volts. NH3 stays as a voltage — the
backend does NOT use ppm, so we deliberately do not compute it here.

FAULT REPORTING — the rule this module follows:
  Never substitute a plausible-looking value for a failed read. A failure must
  produce a SENTINEL the backend recognises, never a number that reads as healthy.
  (0.0 V of ammonia means "perfectly clean water", so returning 0.0 when the ADC is
  dead would suppress every alarm and every water exchange, silently.)

  Detection scope, unchanged in spirit but now precise:
    * DS18B20 / JSN-SR04T -> disconnect IS detectable (sentinels -127 / -1).
    * MQ137 / PH-4502C    -> a probe unplugged from a WORKING ADC is NOT detectable
                             (an open input is indistinguishable from a real reading),
                             and we do not guess. But a failure of the ADC/SPI bus
                             ITSELF raises an exception, which IS detectable — so that
                             case reports ADC_INVALID_V rather than 0.0.

TESTING PHASE — needs the MCP3008 + DS18B20 wired and SPI enabled. Hardware
libs are imported lazily so this module can be imported on a laptop; only
read_frame() actually touches hardware.
"""

import time

import nh3config as cfg

_spi = None
_temp_sensor = None
_level_sensor = None

# Print each device's init failure ONCE (not every frame) so a missing library
# or unplugged sensor is visible in the log instead of silently degrading to a
# sentinel value. Cleared implicitly on process restart.
_warned = {"spi": False, "temp": False, "level": False}


def _warn_once(key, msg):
    if not _warned[key]:
        print(f"[sensors] {msg}")
        _warned[key] = True


def _ensure_hardware():
    """
    Open SPI + the 1-Wire temp sensor + ultrasonic level sensor.

    Each device is initialised INDEPENDENTLY and non-fatally: if one is unplugged
    (or simply missing at boot) it stays None and is retried on the next frame,
    while the others keep working. This is what lets the backend/dashboard see a
    single sensor drop out instead of the whole frame going dark.
    """
    global _spi, _temp_sensor, _level_sensor
    if _spi is None:
        try:
            import spidev

            _spi = spidev.SpiDev()
            _spi.open(cfg.SPI_BUS, cfg.SPI_DEVICE)
            _spi.max_speed_hz = cfg.SPI_MAX_HZ
        except Exception as e:
            _spi = None  # NH3/pH report the ADC-invalid sentinel until it recovers
            _warn_once("spi", f"MCP3008/SPI init failed ({e}); NH3/pH report ADC FAULT "
                              f"({cfg.ADC_INVALID_V}) until it recovers. "
                              "Enable SPI (raspi-config) and check wiring.")
    if _temp_sensor is None:
        try:
            from w1thermsensor import W1ThermSensor

            _temp_sensor = W1ThermSensor()
        except Exception as e:
            _temp_sensor = None  # read_temperature() reports the disconnect sentinel
            _warn_once("temp", f"DS18B20 init failed ({e}); temp reports {cfg.DS18B20_DISCONNECTED_C}. "
                               "If this says 'No module named w1thermsensor', run inside the venv.")
    if _level_sensor is None:
        try:
            from gpiozero import DistanceSensor

            # max_distance is in metres; echo must arrive via a 5V->3.3V divider.
            # queue_len sets gpiozero's internal smoothing window (it samples in a
            # background thread and `.distance` returns that queue's mean).
            _level_sensor = DistanceSensor(
                echo=cfg.LEVEL_ECHO_PIN,
                trigger=cfg.LEVEL_TRIG_PIN,
                max_distance=cfg.LEVEL_MAX_DISTANCE_CM / 100.0,
                queue_len=cfg.LEVEL_QUEUE_LEN,
            )
        except Exception as e:
            _level_sensor = None  # read_level() reports the invalid sentinel
            _warn_once("level", f"JSN-SR04T init failed ({e}); water level = invalid. "
                                "If this mentions edge detection, install lgpio (pip install lgpio).")


def read_mcp3008(channel):
    """Single-ended read of one MCP3008 channel -> 0..1023. Raises if SPI is down."""
    if channel < 0 or channel > 7:
        raise ValueError("Channel must be 0-7")
    if _spi is None:
        raise RuntimeError("SPI/MCP3008 not available")
    adc = _spi.xfer2([1, (8 + channel) << 4, 0])
    return ((adc[1] & 3) << 8) + adc[2]


def average_raw(channel, samples=None):
    samples = samples if samples else cfg.SAMPLES_PER_READ
    total = sum(read_mcp3008(channel) for _ in range(samples))
    return total / samples


def _to_voltage(raw):
    return raw * (cfg.VREF / cfg.ADC_RESOLUTION)


def _to_ph(voltage):
    ph = 7.0 + ((cfg.PH_NEUTRAL_VOLTAGE - voltage) * cfg.PH_SLOPE)
    return max(0.0, min(14.0, ph))


def read_analog(channel):
    """
    Read one MCP3008 channel -> (raw, voltage), or the ADC-fault sentinel pair if the
    bus is unreadable. Returning (0.0, 0.0) here would be a FALSE SAFE: 0 V reads as
    pristine water on NH3 (no alarm, no exchange) and as pH 14 (a false critical).
    """
    try:
        raw = average_raw(channel)
        return round(raw, 1), round(_to_voltage(raw), 4)
    except Exception:
        return cfg.ADC_INVALID_RAW, cfg.ADC_INVALID_V


def read_temperature():
    """Return °C, or the disconnect sentinel the backend understands."""
    if _temp_sensor is None:
        return cfg.DS18B20_DISCONNECTED_C  # never initialised / unplugged at boot
    try:
        return round(_temp_sensor.get_temperature(), 2)
    except Exception:
        # Includes w1thermsensor.SensorNotReadyError — report disconnect so the
        # backend raises sensor.offline instead of acting on a garbage value.
        return cfg.DS18B20_DISCONNECTED_C


def read_level():
    """
    Distance (cm) from the JSN-SR04T to the water surface. Returns the invalid sentinel
    on failure or a pegged (max-range) read so the backend treats the level as unknown
    rather than acting on it.

    Note on sampling: gpiozero's DistanceSensor samples in a BACKGROUND thread and
    `.distance` returns the mean of its internal queue — so reading it N times in a
    tight loop just returns the same cached mean N times, and a median over that
    rejects nothing. We space the reads out instead, so each one reflects a genuinely
    refreshed queue, and take the median of those to drop a transient bad echo.
    """
    if _level_sensor is None:
        return cfg.LEVEL_INVALID_CM  # never initialised / unplugged at boot
    try:
        samples = []
        for i in range(cfg.LEVEL_SAMPLES):
            if i:
                time.sleep(cfg.LEVEL_SAMPLE_GAP_S)  # let the queue refresh
            samples.append(_level_sensor.distance * 100.0)
        samples.sort()
        cm = samples[len(samples) // 2]  # median
    except Exception:
        return cfg.LEVEL_INVALID_CM
    # gpiozero pegs at max_distance when there is no echo — treat that as invalid.
    if cm >= cfg.LEVEL_MAX_DISTANCE_CM * 0.99:
        return cfg.LEVEL_INVALID_CM
    return round(cm, 1)


def read_frame():
    """
    Read all sensors once and return a backend-shaped ingest frame. Each sensor is
    read independently so a failure in one never drops the others.
    """
    _ensure_hardware()

    nh3_raw, nh3_v = read_analog(cfg.NH3_CHANNEL)
    ph_raw, ph_v = read_analog(cfg.PH_CHANNEL)

    # An unreadable ADC has no meaningful pH; pass the sentinel through rather than
    # letting _to_ph(-1) invent a plausible 12.4.
    ph_value = cfg.ADC_INVALID_V if ph_v == cfg.ADC_INVALID_V else round(_to_ph(ph_v), 2)

    temp_c = read_temperature()
    # Don't do arithmetic on the sentinel: -127C would become a plausible-looking
    # -196.6F that no downstream consumer would recognise as "disconnected".
    temp_f = (
        cfg.DS18B20_DISCONNECTED_C
        if temp_c == cfg.DS18B20_DISCONNECTED_C
        else round(temp_c * 9 / 5 + 32, 1)
    )

    level_cm = read_level()

    return {
        "nh3": {"raw": nh3_raw, "voltage": nh3_v},
        "ph": {"voltage": ph_v, "pH": ph_value},
        "waterTemp": {"tempC": temp_c, "tempF": temp_f},
        "waterLevel": {"distanceCm": level_cm},
    }


def close():
    global _spi, _level_sensor
    if _spi is not None:
        try:
            _spi.close()
        except Exception:
            pass
        _spi = None
    if _level_sensor is not None:
        try:
            _level_sensor.close()
        except Exception:
            pass
        _level_sensor = None
