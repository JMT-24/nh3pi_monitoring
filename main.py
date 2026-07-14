#!/usr/bin/env python3
"""
LEGACY — DO NOT RUN. Superseded by controller.py.

This is the original simple sender. It speaks a contract the backend NO LONGER
EXPOSES, so running it does nothing useful and can actively mislead:

  * It POSTs {temperature, ph_raw, nh3_raw, timestamp} straight to BACKEND_URL.
    BACKEND_URL is now a BASE url (http://host:4000), and the live endpoint is
    POST /api/ingest — so every request 404s.
  * It sends no `x-api-key`, which the current backend requires (401).
  * It has no actuator control, no offline buffer, and no safety watchdog.

Kept for reference only. The working entrypoint is:

    python controller.py

It is guarded below rather than deleted so the original is still readable. Hardware
is no longer opened at import time either — this module used to grab the SPI bus and
construct W1ThermSensor() on import, which crashed on a laptop and could contend with
the real control loop on the Pi.
"""

import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
SEND_INTERVAL = 30


def read_mcp3008(spi, channel):
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    return ((adc[1] & 3) << 8) + adc[2]


def average_raw(spi, channel, samples=10):
    total = sum(read_mcp3008(spi, channel) for _ in range(samples))
    return round(total / samples, 1)


def main():
    # Imported here, not at module scope, so this file is safe to import/inspect.
    import spidev
    from w1thermsensor import W1ThermSensor

    spi = spidev.SpiDev()
    spi.open(0, 0)
    spi.max_speed_hz = 1350000
    temp_sensor = W1ThermSensor()

    print("=======================================")
    print("      NH3 MONITORING SYSTEM (LEGACY)   ")
    print("=======================================")
    print(f"Backend: {BACKEND_URL}")
    print(f"Interval: every {SEND_INTERVAL} seconds")
    print("=======================================")

    try:
        while True:
            payload = {
                "temperature": round(temp_sensor.get_temperature(), 2),
                "ph_raw": average_raw(spi, channel=0),
                "nh3_raw": average_raw(spi, channel=2),
                "timestamp": datetime.now().isoformat(),
            }
            print(f"[SEND] {payload}")

            try:
                r = requests.post(BACKEND_URL, json=payload, timeout=5)
                print(f"[OK] STATUS: {r.status_code}")
            except requests.exceptions.ConnectionError:
                print("[ERROR] Cannot reach backend")
            except requests.exceptions.Timeout:
                print("[ERROR] Request timed out")

            time.sleep(SEND_INTERVAL)

    except KeyboardInterrupt:
        print("stopped")
    finally:
        spi.close()


if __name__ == "__main__":
    if os.getenv("NH3_ALLOW_LEGACY") != "1":
        sys.exit(
            "main.py is LEGACY and speaks a contract the backend no longer exposes "
            "(it would 404/401 on every frame, with no actuator control, no offline "
            "buffer and no safety watchdog).\n\n"
            "Run the real control loop instead:\n"
            "    python controller.py\n\n"
            "(If you genuinely need this for reference: NH3_ALLOW_LEGACY=1 python main.py)"
        )
    main()
