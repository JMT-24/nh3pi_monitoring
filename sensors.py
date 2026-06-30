"""
Sensor acquisition + light calibration for the staging control loop.

Produces an ingest frame that matches the backend contract EXACTLY
(nh3_backend/src/validation.ts -> ingestSchema):

    {
      "nh3":       {"raw": <int>,   "voltage": <float>},
      "ph":        {"voltage": <float>, "pH": <float>},
      "waterTemp": {"tempC": <float>, "tempF": <float>}
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


def _ensure_hardware():
    """Open SPI + the 1-Wire temp sensor on first use (raises off-Pi)."""
    global _spi, _temp_sensor
    if _spi is None:
        import spidev

        _spi = spidev.SpiDev()
        _spi.open(cfg.SPI_BUS, cfg.SPI_DEVICE)
        _spi.max_speed_hz = cfg.SPI_MAX_HZ
    if _temp_sensor is None:
        from w1thermsensor import W1ThermSensor

        _temp_sensor = W1ThermSensor()


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
    try:
        return round(_temp_sensor.get_temperature(), 2)
    except Exception:
        # Includes w1thermsensor.SensorNotReadyError — report disconnect so the
        # backend raises sensor.offline instead of acting on a garbage value.
        return cfg.DS18B20_DISCONNECTED_C


def read_frame():
    """Read all sensors once and return a backend-shaped ingest frame."""
    _ensure_hardware()

    nh3_raw = average_raw(cfg.NH3_CHANNEL)
    nh3_v = _to_voltage(nh3_raw)

    ph_raw = average_raw(cfg.PH_CHANNEL)
    ph_v = _to_voltage(ph_raw)

    temp_c = read_temperature()
    temp_f = round(temp_c * 9 / 5 + 32, 1)

    return {
        "nh3": {"raw": round(nh3_raw, 1), "voltage": round(nh3_v, 4)},
        "ph": {"voltage": round(ph_v, 4), "pH": round(_to_ph(ph_v), 2)},
        "waterTemp": {"tempC": temp_c, "tempF": temp_f},
    }


def close():
    if _spi is not None:
        _spi.close()
