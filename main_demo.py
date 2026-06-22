import requests
import time
from datetime import datetime
from dotenv import load_dotenv
import os
import random


load_dotenv()
BACKEND_URL = os.getenv("BACKEND_URL")
SEND_INTERVAL = 5 

def fake_readings():
    return {
        "temperature": round(random.uniform(30.0, 36.0), 2),
        "ph_raw":      round(random.uniform(400.0, 600.0), 1),
        "nh3_raw":     round(random.uniform(200.0, 500.0), 1),
        "timestamp":   datetime.now().isoformat()
    }

def check_connection():
    try:
        r = requests.get(BACKEND_URL.replace("/readings", "/"), timeout=5)
        if r.status_code == 200:
            print(" Backend is reachable!")
            return True
        else:
            print(f" Backend responded with status: {r.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print(" Cannot reach backend check IP or is it running?")
        return False
    except requests.exceptions.Timeout:
        print(" Connection timed out")
        return False

# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    print("=================================")
    print("   NH3 Monitor  DEMO MODE       ")
    print("=================================")
    print(f"Backend : {BACKEND_URL}")
    print(f"Interval: every {SEND_INTERVAL}s")
    print("=================================\n")

    # Check connection first
    print(" Checking backend connection...")
    if not check_connection():
        print("\n[DEMO] Backend unreachable. Fix connection then retry.")
        return

    print("\n Sending fake sensor data...\n")

    try:
        while True:
            payload = fake_readings()
            print(f"[DEMO SEND] temp={payload['temperature']}°C | ph_raw={payload['ph_raw']} | nh3_raw={payload['nh3_raw']}")

            try:
                r = requests.post(BACKEND_URL, json=payload, timeout=5)
                data = r.json()
                print(f" pH: {data['record']['ph']} | NH3: {data['record']['nh3_ppm']} ppm", end="")
                if data['alerts']:
                    print(f" |  {', '.join(data['alerts'])}")
                else:
                    print(" |  All clear")
            except requests.exceptions.ConnectionError:
                print(" Lost connection to backend")
            except requests.exceptions.Timeout:
                print(" Request timed out")

            time.sleep(SEND_INTERVAL)

    except KeyboardInterrupt:
        print("\n[Stopped by user]")

if __name__ == "__main__":
    main()
