#!/usr/bin/env python3
"""
LEGACY — DO NOT RUN. Superseded by controller_demo.py.

The hardware-free demo for the ORIGINAL contract, which the backend no longer exposes:

  * It POSTs {temperature, ph_raw, nh3_raw} to BACKEND_URL (now a base url) with no
    `x-api-key` -> 404/401 on every request.
  * It then reads data['record']['ph'] / data['record']['nh3_ppm'] from a response
    schema that no longer exists -> the 404 body makes r.json() raise, and only
    ConnectionError/Timeout are caught, so it dies with a traceback.
  * The backend works in VOLTS and does not compute ppm at all.

Kept for reference only. The working hardware-free demo is:

    python controller_demo.py

which exercises the real pipeline end-to-end (buffer -> live ingest -> apply the
returned pump/valve command -> backfill) against a running backend.
"""

import os
import random
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
SEND_INTERVAL = 5


def fake_readings():
    return {
        "temperature": round(random.uniform(30.0, 36.0), 2),
        "ph_raw": round(random.uniform(400.0, 600.0), 1),
        "nh3_raw": round(random.uniform(200.0, 500.0), 1),
        "timestamp": datetime.now().isoformat(),
    }


def check_connection():
    try:
        r = requests.get(BACKEND_URL.replace("/readings", "/"), timeout=5)
        if r.status_code == 200:
            print(" Backend is reachable!")
            return True
        print(f" Backend responded with status: {r.status_code}")
        return False
    except requests.exceptions.ConnectionError:
        print(" Cannot reach backend  check IP or is it running?")
        return False
    except requests.exceptions.Timeout:
        print(" Connection timed out")
        return False


def main():
    print("=================================")
    print("   NH3 Monitor  DEMO MODE (LEGACY)")
    print("=================================")
    print(f"Backend : {BACKEND_URL}")
    print(f"Interval: every {SEND_INTERVAL}s")
    print("=================================\n")

    print(" Checking backend connection...")
    if not check_connection():
        print("\n[DEMO] Backend unreachable. Fix connection then retry.")
        return

    print("\n Sending fake sensor data...\n")

    try:
        while True:
            payload = fake_readings()
            print(f"[DEMO SEND] temp={payload['temperature']}C | "
                  f"ph_raw={payload['ph_raw']} | nh3_raw={payload['nh3_raw']}")

            try:
                r = requests.post(BACKEND_URL, json=payload, timeout=5)
                data = r.json()
                print(f" pH: {data['record']['ph']} | NH3: {data['record']['nh3_ppm']} ppm", end="")
                print(f" |  {', '.join(data['alerts'])}" if data["alerts"] else " |  All clear")
            except requests.exceptions.ConnectionError:
                print(" Lost connection to backend")
            except requests.exceptions.Timeout:
                print(" Request timed out")

            time.sleep(SEND_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Stopped by user]")


if __name__ == "__main__":
    if os.getenv("NH3_ALLOW_LEGACY") != "1":
        sys.exit(
            "main_demo.py is LEGACY and speaks a contract the backend no longer exposes "
            "(it would 404 on every frame, then crash parsing a response schema that no "
            "longer exists).\n\n"
            "Run the real hardware-free demo instead:\n"
            "    python controller_demo.py\n\n"
            "(If you genuinely need this for reference: NH3_ALLOW_LEGACY=1 python main_demo.py)"
        )
    main()
