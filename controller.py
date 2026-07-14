#!/usr/bin/env python3
"""
Staging control loop for the NH3 monitoring + auto-refill Pi.

  *** NOT the production entrypoint yet — this is the testing-phase main.   ***
  *** Promote this to main.py only after the relay wiring, sensors, and     ***
  *** backend creds are verified. (main.py / main_demo.py are LEGACY and    ***
  *** speak an old contract the backend no longer exposes — do not run them.)***

What it does:
  0. ONCE at startup: claim the relays and drive them to a known-OFF state. This must
     happen before anything else — if the Pi boots while the network is down, no
     backend command ever arrives, and un-driven pins sit at their power-on default
     (LOW), which ENERGISES an active-low relay board.
  Then each cycle (every SEND_INTERVAL seconds):
  1. Read all sensors -> backend-shaped frame.
  2. Save the frame to the local offline buffer (sqlite) FIRST.
  3. POST it live to /api/ingest:
       - online   -> apply the returned pump/valve command, mark frame synced,
                     then replay any backlog to /api/ingest/batch.
       - offline  -> leave it buffered; the actuator watchdog forces OFF, so a
                     refill can't run with no oversight.
       - rejected -> the backend refused it (bad key, bad payload). Retrying
                     unchanged won't help, so say so plainly.
  4. Prune the buffer so it can't grow until the SD card fills.
  5. Between cycles, tick the watchdog every second.

Before running:
  * Fill in .env  (BACKEND_URL, INGEST_API_KEY) — see .env.example.
  * Set PUMP_PIN / VALVE_PIN / RELAY_ACTIVE_LOW in .env to your wiring.
  * Enable SPI + 1-Wire on the Pi, and run inside the venv.
"""

import socket
import time
from datetime import datetime, timezone

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


def _fmt_frame(frame):
    """One readable block showing every sensor value the Pi is sending."""
    nh3 = frame["nh3"]
    ph = frame["ph"]
    temp = frame["waterTemp"]
    level = frame["waterLevel"]["distanceCm"]

    nh3_str = (
        "ADC FAULT (SPI/MCP3008 unreadable)"
        if nh3["voltage"] == cfg.ADC_INVALID_V
        else f"{nh3['voltage']} V  (raw {nh3['raw']}/1023)"
    )
    ph_str = (
        "ADC FAULT (SPI/MCP3008 unreadable)"
        if ph["voltage"] == cfg.ADC_INVALID_V
        else f"{ph['pH']}  ({ph['voltage']} V)"
    )
    temp_str = (
        "DISCONNECTED" if temp["tempC"] == cfg.DS18B20_DISCONNECTED_C
        else f"{temp['tempC']}C / {temp['tempF']}F"
    )
    level_str = (
        "INVALID (no echo / out of range)" if level <= 0 else f"{level} cm to surface"
    )
    return (
        f"   NH3   : {nh3_str}\n"
        f"   pH    : {ph_str}\n"
        f"   Temp  : {temp_str}\n"
        f"   Level : {level_str}"
    )


def _sleep_with_watchdog(seconds):
    """Sleep in 1s steps so the actuator safety watchdog stays responsive."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        actuators.tick()
        time.sleep(min(1, max(0, end - time.monotonic())))


class _Stop(Exception):
    """Raised by the SIGTERM handler so the main loop's `finally` still runs."""


def _install_signal_handlers():
    """
    Turn SIGTERM into an exception so the `finally` that drives the relays OFF actually
    runs. Without this, `systemctl stop`/`restart` (and every deploy) killed the process
    outright with the pump possibly energised: `except KeyboardInterrupt` doesn't match
    SIGTERM, so actuators.shutdown() was never called.

    SIGKILL remains unfixable in software — see the hardware caveat on actuators.shutdown().
    """
    import signal

    def _handler(signum, _frame):
        raise _Stop(f"signal {signum}")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError, AttributeError) as e:
            print(f"[controller] WARNING: could not install handler for {sig} ({e})")


def _flush_backlog():
    """
    Replay buffered (offline) frames as history, oldest first.

    Frames the backend SKIPPED (an implausible ts from an unsynced Pi clock) are dropped
    rather than left pending: they will never be accepted, and pending() always returns
    the oldest rows first, so keeping them would block the entire backlog forever.
    """
    items = buffer.pending()
    if not items:
        return
    accepted, skipped = client.backfill(items)
    if not accepted:
        return
    ids = [it["id"] for it in items]
    buffer.mark_synced(ids)
    if skipped:
        # The backend doesn't say WHICH frames it skipped, only how many. They're all
        # from this batch and none will ever be accepted, so retiring the batch is
        # correct — marking synced above already did that.
        print(f"[controller] {skipped} frame(s) discarded by the backend (bad timestamps)")
    print(f"[controller] backfilled {len(items) - skipped} buffered frame(s)")


def main():
    print("=" * 56)
    print(" NH3 CONTROL LOOP (staging)")
    print(f" backend : {cfg.BACKEND_URL}")
    print(f" interval: {cfg.SEND_INTERVAL}s   device: {cfg.DEVICE_ID}")
    print(f" pump=GPIO{cfg.PUMP_PIN} valve=GPIO{cfg.VALVE_PIN} active_low={cfg.RELAY_ACTIVE_LOW}")
    print(f" buffer  : {cfg.BUFFER_DB}")
    if not cfg.INGEST_API_KEY:
        print(" [WARN] INGEST_API_KEY is empty - the backend will reject ingest (401).")
    if cfg.SEND_INTERVAL >= cfg.DEFAULT_MAX_RUNTIME_SEC:
        # Only catches a bad DEFAULT. The window that actually arms the watchdog is the
        # backend's per-command maxRuntimeSec, which this check never sees — so
        # apply_command() clamps that one itself.
        print(f" [WARN] SEND_INTERVAL ({cfg.SEND_INTERVAL}s) >= max runtime "
              f"({cfg.DEFAULT_MAX_RUNTIME_SEC}s): the watchdog will force the relays off "
              f"between every frame and the pump will chatter. Lower SEND_INTERVAL.")

    _install_signal_handlers()

    # Everything that touches hardware lives INSIDE the try, so the `finally` that drives
    # the relays off covers startup too. init() claims the pins, so a SIGTERM arriving
    # during start-up previously skipped cleanup entirely.
    try:
        # Drive the relays to a known-OFF state BEFORE the first frame — never wait for a
        # backend command to establish safety.
        actuators.init()
        if not actuators.hardware_ok():
            print(" [WARN] relays are NOT under control (see the messages above). Sensor data "
                  "will still be reported, but commands cannot be applied.")
        # Independent of the main loop, so a wedged sensor/HTTP read can't silence it.
        actuators.start_watchdog()
        print("=" * 56)

        while True:
            # Capture time is stored with an explicit offset; the backend normalises it
            # to UTC on backfill (it sorts timestamps lexicographically).
            ts = datetime.now(timezone.utc).astimezone().isoformat()

            try:
                frame = sensors.read_frame()
            except Exception as e:
                print(f"[controller] sensor read failed: {e}")
                _sleep_with_watchdog(cfg.SEND_INTERVAL)
                continue

            try:
                row_id = buffer.enqueue(frame, ts)
            except Exception as e:
                # A failed buffer write (e.g. disk full) must not kill the loop — the
                # live path can still run and keep the tank controlled.
                print(f"[controller] buffer write failed: {e}")
                row_id = None

            # Live frame carries the Pi's applied actuator state + gateway meta.
            live = dict(frame)
            live["actuators"] = actuators.state()
            live["gateway"] = _gateway_meta()

            result = client.send_live(live)
            actuators.tick()  # the POST can take up to HTTP_TIMEOUT; stay responsive

            # Every buffer call below is guarded. `enqueue` above was already, but the
            # rest were not — and they run in the same loop body against the same disk.
            # A full SD card (the exact case prune() exists for) fails the UPDATE in
            # mark_synced just as readily as the INSERT, and an unguarded raise here
            # escapes `except KeyboardInterrupt`, exits the process, and leaves the tank
            # unmonitored with the pins released.
            if result.ok:
                try:
                    actuators.apply_command(result.command)
                except Exception as e:
                    # Never let a bad command strand the hardware in an unknown state.
                    print(f"[controller] apply_command failed ({e}); forcing actuators OFF")
                    actuators.shutdown()
                    actuators.init()
                try:
                    if row_id is not None:
                        buffer.mark_synced([row_id])
                    _flush_backlog()
                except Exception as e:
                    print(f"[controller] buffer sync failed: {e}")
                print(f"[OK] {ts}\n{_fmt_frame(frame)}")
            elif result.status == client.REJECTED:
                print(f"[REJECTED] backend refused the frame: {result.detail}\n{_fmt_frame(frame)}")
            else:
                # Offline: watchdog will force actuators off if a refill was live.
                try:
                    pending_n = buffer.count_pending()
                except Exception as e:
                    pending_n = f"?({e})"
                print(f"[OFFLINE] buffered frame #{row_id} "
                      f"({pending_n} pending)\n{_fmt_frame(frame)}")

            try:
                buffer.prune()
            except Exception as e:
                print(f"[controller] buffer prune failed: {e}")

            _sleep_with_watchdog(cfg.SEND_INTERVAL)

    # ASCII only. These sit on the shutdown path, immediately before the `finally` that
    # drives the relays off — a print() raising UnicodeEncodeError under a LANG=C systemd
    # unit would be raised from inside the except clause and could skip that cleanup.
    except KeyboardInterrupt:
        print("\nstopping...")
    except _Stop as e:
        print(f"\nstopping ({e})...")
    finally:
        # Always leave the hardware safe, even on an unexpected exception. Stop the
        # watchdog only AFTER shutdown() has driven the relays off, never before.
        actuators.shutdown()
        actuators.stop_watchdog()
        sensors.close()
        buffer.close()


if __name__ == "__main__":
    main()
