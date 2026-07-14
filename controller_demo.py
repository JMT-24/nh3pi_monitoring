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
    """
    NH3 sweep ~1.48<->1.76 V so it crosses the DEFAULT calibration warn(1.56)/
    crit(1.71) — watch the backend start/stop a water exchange. Water level
    sweeps ~12<->18 cm around the 15 cm baseline so the drain/intake throttle
    (pause outside the ±2 cm band) is exercised too.
    """
    global _step
    _step += 1
    nh3_v = 1.62 + 0.14 * math.sin(_step / 6.0)
    nh3_raw = round(nh3_v / (cfg.VREF / cfg.ADC_RESOLUTION), 1)
    ph = 7.2 + 0.1 * math.sin(_step / 9.0)
    temp_c = 28.0 + 0.5 * math.sin(_step / 11.0)
    level_cm = 15.0 + 3.0 * math.sin(_step / 7.0)
    return {
        "nh3": {"raw": nh3_raw, "voltage": round(nh3_v, 4)},
        "ph": {"voltage": 2.5, "pH": round(ph, 2)},
        "waterTemp": {"tempC": round(temp_c, 2), "tempF": round(temp_c * 9 / 5 + 32, 1)},
        "waterLevel": {"distanceCm": round(level_cm, 1)},
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

            # Mirrors controller.py's contract with backend_client: send_live returns a
            # Result (never None), and backfill returns an (accepted, skipped) tuple.
            # Testing those the old way was actively harmful: `if command is not None`
            # is always true for a Result, and `(False, 0)` is a NON-EMPTY tuple and
            # therefore truthy — so a failed backfill marked every pending frame synced
            # and prune() then deleted frames that had never reached the backend.
            result = client.send_live(live)
            if result.ok:
                actuators.apply_command(result.command)
                buffer.mark_synced([row_id])
                items = buffer.pending()
                if items:
                    accepted, skipped = client.backfill(items)
                    if accepted:
                        buffer.mark_synced([it["id"] for it in items])
                        print(f"  backfilled {len(items) - skipped}")
                print(f"[OK] nh3={frame['nh3']['voltage']}V -> "
                      f"pump={result.command.get('pump')} valve={result.command.get('valve')}")
            elif result.status == client.REJECTED:
                print(f"[REJECTED] {result.detail}")
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
