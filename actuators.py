"""
Relay control for the water pump + solenoid valve, with the hybrid-safety
watchdog the backend's control contract relies on.

The backend sends a command each ingest round-trip:
    {"mode","pump":"on|off","valve":"open|closed","reason","maxRuntimeSec","ts"}
We apply it AND remember its maxRuntimeSec. If no fresh command arrives within
that window (e.g. the network dropped), tick() forces the pump/valve OFF so a
refill can never run away. This is the Pi-side half of the hybrid model.

TESTING PHASE — needs gpiozero + the relay wired to the configured BCM pins.
gpiozero is imported lazily so this module imports fine on a laptop; if it is
missing, the actuators run in a logged "simulation" mode (no GPIO).
"""

import time

import nh3config as cfg

_pump = None
_valve = None
_simulated = False

# Wall-clock deadline (time.monotonic) after which we self-disable. None = idle.
_deadline = None
_applied = {"pump": "off", "valve": "closed"}


def _init():
    global _pump, _valve, _simulated
    if _pump is not None or _simulated:
        return
    try:
        from gpiozero import OutputDevice

        # active_high mirrors the board: active-low boards energise on GPIO low.
        active_high = not cfg.RELAY_ACTIVE_LOW
        _pump = OutputDevice(cfg.PUMP_PIN, active_high=active_high, initial_value=False)
        _valve = OutputDevice(cfg.VALVE_PIN, active_high=active_high, initial_value=False)
    except Exception as e:  # ImportError off-Pi, or pin/permission errors
        _simulated = True
        print(f"[actuators] GPIO unavailable ({e}); running in SIMULATION mode.")


def _set(device, on):
    if _simulated or device is None:
        return
    device.on() if on else device.off()


def _force_off():
    global _deadline
    _set(_pump, False)
    _set(_valve, False)
    _applied.update(pump="off", valve="closed")
    _deadline = None


def apply_command(command):
    """Apply a backend command dict. Unknown/empty -> treated as all-off."""
    global _deadline
    _init()

    pump_on = command.get("pump") == "on"
    valve_open = command.get("valve") == "open"
    runtime = int(command.get("maxRuntimeSec") or cfg.DEFAULT_MAX_RUNTIME_SEC)

    _set(_pump, pump_on)
    _set(_valve, valve_open)
    _applied.update(
        pump="on" if pump_on else "off",
        valve="open" if valve_open else "closed",
    )

    # Arm the watchdog only while something is actually active.
    _deadline = (time.monotonic() + runtime) if (pump_on or valve_open) else None
    reason = command.get("reason", "")
    print(f"[actuators] pump={_applied['pump']} valve={_applied['valve']} ({reason})")


def tick():
    """Call frequently. Forces OFF if the safety window has elapsed."""
    if _deadline is not None and time.monotonic() >= _deadline:
        print("[actuators] SAFETY: no fresh command within maxRuntimeSec — forcing OFF")
        _force_off()


def state():
    """Current applied actuator state, for the ingest payload's `actuators`."""
    return dict(_applied)


def shutdown():
    """Fail safe: turn everything off and release the pins."""
    _force_off()
    for d in (_pump, _valve):
        try:
            if d is not None:
                d.close()
        except Exception:
            pass
