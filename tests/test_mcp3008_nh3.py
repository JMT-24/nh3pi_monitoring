#!/usr/bin/env python3
"""
MCP3008 + MQ137 (NH3) connection test.

Run this FIRST, before trusting main.py — it proves the MCP3008 ADC is wired
and talking over SPI, and that the MQ137 ammonia sensor is producing a live
analog signal on its channel. No backend, no pump, no relay: read-only.

Wiring assumed (matches the project README):
    MCP3008  -> Raspberry Pi (SPI0)
      VDD/VREF -> 3.3V (or 5V if your MCP3008 board is 5V-tolerant on logic)
      AGND/DGND-> GND
      CLK      -> GPIO11 (SCLK)
      DOUT     -> GPIO9  (MISO)
      DIN      -> GPIO10 (MOSI)
      CS/SHDN  -> GPIO8  (CE0)
    MQ137 analog out -> MCP3008 CH2

Enable SPI first:  sudo raspi-config -> Interface Options -> SPI -> Enable

Usage:
    python3 tests/test_mcp3008_nh3.py            # read CH2 (NH3) once per 2s
    python3 tests/test_mcp3008_nh3.py --scan     # show all 8 channels (wiring sweep)
    python3 tests/test_mcp3008_nh3.py --warmup   # 65s MQ137 heater warm-up first
"""

import argparse
import sys
import time

try:
    import spidev
except ImportError:
    sys.exit(
        "[FATAL] spidev not installed. On the Pi run:  pip install spidev\n"
        "        (This test must run on the Raspberry Pi, not a laptop.)"
    )

# ---- Config -----------------------------------------------------------------
NH3_CHANNEL = 2          # MQ137 analog out -> MCP3008 CH2
VREF = 5.0               # MCP3008 reference voltage (set to your actual VREF)
ADC_RESOLUTION = 1023.0  # 10-bit ADC -> 0..1023
NUM_SAMPLES = 10
SAMPLE_DELAY = 0.05      # seconds between samples within one averaged read
READ_INTERVAL = 2.0      # seconds between printed readings
WARMUP_SECONDS = 65      # MQ137 heater needs to stabilise from cold

# MQ137 ppm curve (datasheet approximation). RO must be calibrated in CLEAN air
# for ppm to mean anything — until then, trust RAW and VOLTAGE, treat ppm as a
# rough indicator only. The backend works in VOLTS, so ppm here is just for eyes.
RL = 10.0                # load resistor on the module, kOhm
RO = 30.0                # sensor resistance in clean air, kOhm (CALIBRATE THIS)
CURVE_A = 102.7
CURVE_B = -2.473


def read_mcp3008(spi, channel):
    """Single-ended read of one MCP3008 channel -> 0..1023."""
    if channel < 0 or channel > 7:
        raise ValueError("Channel must be 0-7")
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    return ((adc[1] & 3) << 8) + adc[2]


def average_raw(spi, channel, samples=NUM_SAMPLES):
    total = 0
    for _ in range(samples):
        total += read_mcp3008(spi, channel)
        time.sleep(SAMPLE_DELAY)
    return total / samples


def estimate_ppm(voltage):
    """Rough MQ137 ppm from voltage. Meaningless until RO is calibrated."""
    if voltage <= 0:
        return None, None
    rs = ((VREF - voltage) / voltage) * RL   # sensor resistance, kOhm
    ratio = rs / RO                          # <-- the bug in the archive script
    ppm = max(0.0, CURVE_A * (ratio ** CURVE_B))
    return rs, ppm


def diagnose(raw):
    """Plain-language hint about whether the wiring looks alive."""
    if raw <= 1:
        return "STUCK LOW  -> check MQ137 AOUT->CH2 and MCP3008 GND/VREF wiring"
    if raw >= 1022:
        return "STUCK HIGH -> sensor saturated or AOUT floating/shorted to VREF"
    return "OK (signal is live)"


def warmup():
    print(f"Warming up MQ137 heater... {WARMUP_SECONDS}s")
    for i in range(WARMUP_SECONDS, 0, -1):
        print(f"  warm-up: {i:2d}s remaining ", end="\r", flush=True)
        time.sleep(1)
    print("\n  ready.\n")


def run_scan(spi):
    """One-shot sweep of all 8 channels — handy for finding mis-wired channels."""
    print("Channel sweep (raw / voltage):")
    for ch in range(8):
        raw = average_raw(spi, ch, samples=5)
        volts = raw * (VREF / ADC_RESOLUTION)
        marker = "  <- NH3 (CH2)" if ch == NH3_CHANNEL else ""
        print(f"  CH{ch}: {raw:6.1f}  {volts:5.3f} V{marker}")
    print()


def run_monitor(spi):
    print("=" * 52)
    print(f" MQ137 NH3 test  |  CH{NH3_CHANNEL}  |  VREF {VREF} V")
    print(" RAW and VOLTAGE prove the wiring. ppm is rough until RO calibrated.")
    print(" Ctrl+C to stop.")
    print("=" * 52)
    while True:
        raw = average_raw(spi, NH3_CHANNEL)
        voltage = raw * (VREF / ADC_RESOLUTION)
        rs, ppm = estimate_ppm(voltage)
        ppm_str = "  --" if ppm is None else f"{ppm:7.2f} ppm"
        rs_str = "  --" if rs is None else f"{rs:6.2f} k"
        print(
            f"raw {raw:6.1f} | {voltage:5.3f} V | Rs {rs_str} | "
            f"~{ppm_str} | {diagnose(raw)}"
        )
        time.sleep(READ_INTERVAL)


def main():
    global VREF
    ap = argparse.ArgumentParser(description="MCP3008 + MQ137 NH3 connection test")
    ap.add_argument("--scan", action="store_true", help="sweep all 8 ADC channels once")
    ap.add_argument("--warmup", action="store_true", help="65s MQ137 warm-up before reading")
    ap.add_argument("--vref", type=float, default=VREF,
                    help="ADC reference voltage = the chip's VDD rail (3.3 or 5.0). "
                         "Use 3.3 if you wired VDD/VREF to the 3.3V pin. Default %(default)s")
    args = ap.parse_args()
    VREF = args.vref

    spi = spidev.SpiDev()
    try:
        spi.open(0, 0)              # SPI bus 0, CE0
        spi.max_speed_hz = 1350000
    except (FileNotFoundError, PermissionError) as e:
        sys.exit(
            f"[FATAL] Could not open SPI ({e}).\n"
            "        Enable it: sudo raspi-config -> Interface Options -> SPI -> Enable, then reboot."
        )

    try:
        if args.scan:
            run_scan(spi)
            return
        if args.warmup:
            warmup()
        run_monitor(spi)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        spi.close()


if __name__ == "__main__":
    main()
