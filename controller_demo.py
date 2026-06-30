#!/usr/bin/env python3
"""
Hardware-free demo of the staging control loop.

Same pipeline as controller.py (buffer -> live ingest -> apply command ->
backfill), but sensor frames are SIMULATED so you can exercise the full
contract against a running backend from a laptop — no Pi, MCP3008, or relay.

The NH3 voltage sweeps across the backend's warn/crit thresholds so you can
watch the backend command the pump on, then off (actuators run in gpiozero's
simulation mode and just print). Kill your backend mid-run to see frames
buffer, then bring it back to see them backfill.

Run:  python controller_demo.py        (needs .env: BACKEND_URL, INGEST_API_KEY)
"""

import math
import time
from datetime import datetime

import actuators
import backend_client as client
import buffer
import nh3config as cfg

_step = 0


def fake_frame():
    """NH3 triangle-ish sweep 0.34<->0.58 V so it crosses warn(0.40)/crit(0.50)."""
    global _step
    _step += 1
    nh3_v = 0.46 + 0.12 * math.sin(_step / 6.0)
    nh3_raw = round(nh3_v / (cfg.VREF / cfg.ADC_RESOLUTION), 1)
    ph = 7.2 + 0.1 * math.sin(_step / 9.0)
    temp_c = 28.0 + 0.5 * math.sin(_step / 11.0)
    return {
        "nh3": {"raw": nh3_raw, "voltage": round(nh3_v, 4)},
        "ph": {"voltage": 2.5, "pH": round(ph, 2)},
        "waterTemp": {"tempC": round(temp_c, 2), "tempF": round(temp_c * 9 / 5 + 32, 1)},
    }


def main():
    interval = max(2, cfg.SEND_INTERVAL // 6)  # faster than live for demoing
    print(f"DEMO control loop -> {cfg.BACKEND_URL}  (every {interval}s, Ctrl+C to stop)")
    try:
        while True:
            ts = datetime.now().astimezone().isoformat()
            frame = fake_frame()
            row_id = buffer.enqueue(frame, ts)

            live = dict(frame)
            live["actuators"] = actuators.state()
            live["gateway"] = {"fw": cfg.FW_VERSION, "ip": "127.0.0.1"}

            command = client.send_live(live)
            if command is not None:
                actuators.apply_command(command)
                buffer.mark_synced([row_id])
                items = buffer.pending()
                if items and client.backfill(items):
                    buffer.mark_synced([it["id"] for it in items])
                    print(f"  backfilled {len(items)}")
                print(f"[OK] nh3={frame['nh3']['voltage']}V -> pump={command.get('pump')} "
                      f"valve={command.get('valve')}")
            else:
                print(f"[OFFLINE] buffered #{row_id} ({buffer.count_pending()} pending)")

            for _ in range(interval):
                actuators.tick()
                time.sleep(1)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        actuators.shutdown()
        buffer.close()


if __name__ == "__main__":
    main()
