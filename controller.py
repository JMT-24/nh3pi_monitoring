#!/usr/bin/env python3
"""
Staging control loop for the NH3 monitoring + auto-refill Pi.

  *** NOT the production entrypoint yet — this is the testing-phase main.   ***
  *** Run main.py / main_demo.py for the simple sender. Promote this to     ***
  *** main.py only after the relay wiring, sensors, and backend creds are   ***
  *** verified.                                                             ***

What it does each cycle (every SEND_INTERVAL seconds):
  1. Read all sensors -> backend-shaped frame.
  2. Save the frame to the local offline buffer (sqlite) FIRST.
  3. POST it live to /api/ingest:
       - online  -> apply the returned pump/valve command, mark frame synced,
                    then replay any backlog to /api/ingest/batch.
       - offline -> leave it buffered; the actuator watchdog forces OFF if a
                    refill was running, so nothing runs away with no oversight.
  4. Between cycles, tick the watchdog every second.

Before running:
  * Fill in .env  (BACKEND_URL, INGEST_API_KEY) — see .env.example.
  * Set PUMP_PIN / VALVE_PIN / RELAY_ACTIVE_LOW in .env to your wiring.
  * Enable SPI + 1-Wire on the Pi.
"""

import socket
import time
from datetime import datetime

import actuators
import backend_client as client
import buffer
import nh3config as cfg
import sensors


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "0.0.0.0"


def _gateway_meta():
    return {"fw": cfg.FW_VERSION, "ip": _local_ip()}


def _sleep_with_watchdog(seconds):
    """Sleep in 1s steps so the actuator safety watchdog stays responsive."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        actuators.tick()
        time.sleep(1)


def _flush_backlog(exclude_id):
    """Replay buffered (offline) frames as history, oldest first."""
    items = buffer.pending(exclude_id=exclude_id)
    if not items:
        return
    if client.backfill(items):
        buffer.mark_synced([it["id"] for it in items])
        print(f"[controller] backfilled {len(items)} buffered frame(s)")


def main():
    print("=" * 56)
    print(" NH3 CONTROL LOOP (staging)")
    print(f" backend : {cfg.BACKEND_URL}")
    print(f" interval: {cfg.SEND_INTERVAL}s   device: {cfg.DEVICE_ID}")
    print(f" pump=GPIO{cfg.PUMP_PIN} valve=GPIO{cfg.VALVE_PIN} active_low={cfg.RELAY_ACTIVE_LOW}")
    if not cfg.INGEST_API_KEY:
        print(" [WARN] INGEST_API_KEY is empty — the backend will reject ingest (401).")
    print("=" * 56)

    try:
        while True:
            ts = datetime.now().astimezone().isoformat()

            try:
                frame = sensors.read_frame()
            except Exception as e:
                print(f"[controller] sensor read failed: {e}")
                _sleep_with_watchdog(cfg.SEND_INTERVAL)
                continue

            row_id = buffer.enqueue(frame, ts)

            # Live frame carries the Pi's applied actuator state + gateway meta.
            live = dict(frame)
            live["actuators"] = actuators.state()
            live["gateway"] = _gateway_meta()

            command = client.send_live(live)
            if command is not None:
                actuators.apply_command(command)
                buffer.mark_synced([row_id])
                _flush_backlog(exclude_id=None)
                print(f"[OK] {ts}  nh3={frame['nh3']['voltage']}V "
                      f"pH={frame['ph']['pH']} temp={frame['waterTemp']['tempC']}C")
            else:
                # Offline: watchdog will force actuators off if a refill was live.
                print(f"[OFFLINE] buffered frame #{row_id} "
                      f"({buffer.count_pending()} pending)")

            _sleep_with_watchdog(cfg.SEND_INTERVAL)

    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        actuators.shutdown()
        sensors.close()
        buffer.close()


if __name__ == "__main__":
    main()
