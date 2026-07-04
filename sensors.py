"""
Sensor acquisition + light calibration for the staging control loop.

Produces an ingest frame that matches the backend contract EXACTLY
(nh3_backend/src/validation.ts -> ingestSchema):

    {
      "nh3":        {"raw": <int>,   "voltage": <float>},
      "ph":         {"voltage": <float>, "pH": <float>},
      "waterTemp":  {"tempC": <float>, "tempF": <float>},
      "waterLevel": {"distanceCm": <float>}   # JSN-SR04T; <= 0 == invalid
    }

Calibration split: the Pi computes voltage + pH here (cheap math); the backend
owns the danger thresholds and works in volts. NH3 stays as a voltage — the
backend does NOT use ppm, so we deliberately do not compute it here.

TESTING PHASE — needs the MCP3008 + DS18B20 wired and SPI enabled. Hardware
libs are imported lazily so this module can be imported on a laptop; only
read_frame() actually touches hardware.
"""

import nh3config as cfg

_spi = None
_temp_sensor = None
_level_sensor = None


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
        except Exception:
            _spi = None  # analog reads fall back to 0 V until the ADC is back
    if _temp_sensor is None:
        try:
            from w1thermsensor import W1ThermSensor

            _temp_sensor = W1ThermSensor()
        except Exception:
            _temp_sensor = None  # read_temperature() reports the disconnect sentinel
    if _level_sensor is None:
        try:
            from gpiozero import DistanceSensor

            # max_distance is in metres; echo must arrive via a 5V->3.3V divider.
            _level_sensor = DistanceSensor(
                echo=cfg.LEVEL_ECHO_PIN,
                trigger=cfg.LEVEL_TRIG_PIN,
                max_distance=cfg.LEVEL_MAX_DISTANCE_CM / 100.0,
            )
        except Exception:
            _level_sensor = None  # read_level() reports the invalid sentinel


def read_mcp3008(channel):
    """Single-ended read of one MCP3008 channel -> 0..1023."""
    if channel < 0 or channel > 7:
        raise ValueError("Channel must be 0-7")
    adc = _spi.xfer2([1, (8 + channel) << 4, 0])
    return ((adc[1] & 3) << 8) + adc[2]


def average_raw(channel, samples=None):
    samples = samples or cfg.SAMPLES_PER_READ
    total = sum(read_mcp3008(channel) for _ in range(samples))
    return total / samples


def _to_voltage(raw):
    return raw * (cfg.VREF / cfg.ADC_RESOLUTION)


def _to_ph(voltage):
    ph = 7.0 + ((cfg.PH_NEUTRAL_VOLTAGE - voltage) * cfg.PH_SLOPE)
    return max(0.0, min(14.0, ph))


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
    Distance (cm) from the JSN-SR04T to the water surface — median of a few
    pings. Returns the invalid sentinel on failure or a pegged (max-range) read
    so the backend treats the level as unknown rather than acting on it.
    """
    if _level_sensor is None:
        return cfg.LEVEL_INVALID_CM  # never initialised / unplugged at boot
    try:
        samples = sorted(
            _level_sensor.distance * 100.0 for _ in range(cfg.LEVEL_SAMPLES)
        )
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

    # NH3 + pH are analog (MCP3008). We do NOT do disconnect detection for these
    # (an open analog input can't be told apart from a real reading), but we still
    # isolate the read so an SPI hiccup can't blank the temp/level sensors.
    try:
        nh3_raw = average_raw(cfg.NH3_CHANNEL)
        nh3_v = _to_voltage(nh3_raw)
    except Exception:
        nh3_raw, nh3_v = 0.0, 0.0
    try:
        ph_raw = average_raw(cfg.PH_CHANNEL)
        ph_v = _to_voltage(ph_raw)
    except Exception:
        ph_raw, ph_v = 0.0, 0.0

    temp_c = read_temperature()
    temp_f = round(temp_c * 9 / 5 + 32, 1)

    level_cm = read_level()

    return {
        "nh3": {"raw": round(nh3_raw, 1), "voltage": round(nh3_v, 4)},
        "ph": {"voltage": round(ph_v, 4), "pH": round(_to_ph(ph_v), 2)},
        "waterTemp": {"tempC": temp_c, "tempF": temp_f},
        "waterLevel": {"distanceCm": level_cm},
    }


def close():
    if _spi is not None:
        _spi.close()
    if _level_sensor is not None:
        try:
            _level_sensor.close()
        except Exception:
            pass
