"""
Relay control for the water pump + solenoid valve, with the hybrid-safety
watchdog the backend's control contract relies on.

The backend sends a command each ingest round-trip:
    {"mode","pump":"on|off","valve":"open|closed","reason","maxRuntimeSec","ts"}
We apply it AND remember its maxRuntimeSec. If no fresh command arrives within
that window (e.g. the network dropped), tick() forces the pump/valve OFF so a
refill can never run away. This is the Pi-side half of the hybrid model.

SAFETY RULES THIS MODULE FOLLOWS — change them only with care:
  1. init() is called ONCE at startup, before the loop, and drives both relays to
     a known-OFF state. It must NOT be deferred until the first backend command:
     if the Pi boots while the network is down, that command never arrives, and
     the pins would sit at their power-on default (input/pull-down = LOW), which
     ENERGISES an active-low board. That is the drain running with no oversight.
  2. Every hardware call is individually guarded. A failure on one device must
     never prevent the other from being shut off.
  3. The watchdog deadline is armed BEFORE energising anything, never after.
  4. If we cannot prove a relay is controllable, we say so loudly rather than
     reporting a state we are only assuming.

TESTING PHASE — needs gpiozero + the relay wired to the configured BCM pins.
gpiozero is imported lazily so this module imports fine on a laptop; if it is
missing, the actuators run in a logged "simulation" mode (no GPIO).
"""

import threading
import time

import nh3config as cfg

_pump = None
_valve = None
_simulated = False
_init_done = False

# Serialises relay access between the main loop (apply_command) and the watchdog thread
# (tick). Re-entrant because _force_off() is reached from inside already-locked paths.
#
# Without it there is a real, narrow race: tick() reads an EXPIRED deadline and enters
# _force_off() just as apply_command() arms a fresh one; _force_off() then clears
# _deadline last, erasing the new arming and leaving the pump energised with no
# watchdog at all — the exact runaway the module exists to prevent.
_lock = threading.RLock()

# Wall-clock deadline (time.monotonic) after which we self-disable. None = idle.
_deadline = None
_applied = {"pump": "off", "valve": "closed"}

# How soon tick() retries a force-off that the hardware refused. Short, because until
# it succeeds we must assume the relay is still energised.
FORCE_OFF_RETRY_SEC = 2.0


def _make_device(name, pin):
    """Construct one relay output, or return None if it can't be claimed."""
    from gpiozero import OutputDevice

    # active_high mirrors the board: active-low boards energise on GPIO low.
    active_high = not cfg.RELAY_ACTIVE_LOW
    return OutputDevice(pin, active_high=active_high, initial_value=False)


def init():
    """
    Claim both relay pins and drive them OFF. Safe to call repeatedly; retries any
    device that previously failed so a transient error at boot self-heals.

    Each device is constructed INDEPENDENTLY: a partial failure used to flip the whole
    module into simulation mode while one relay was still live, turning every later
    apply_command()/_force_off() into a silent no-op that still reported success.
    """
    global _pump, _valve, _simulated, _init_done

    try:
        from gpiozero import OutputDevice  # noqa: F401  (probe availability only)
    except Exception as e:
        if not _init_done:
            print(f"[actuators] gpiozero unavailable ({e}); running in SIMULATION mode (no GPIO).")
        _simulated = True
        _init_done = True
        return

    with _lock:
        if _pump is None:
            try:
                _pump = _make_device("pump", cfg.PUMP_PIN)
            except Exception as e:
                _pump = None
                print(f"[actuators] WARNING: pump relay on GPIO{cfg.PUMP_PIN} unavailable ({e}). "
                      f"The drain CANNOT be controlled.")
        if _valve is None:
            try:
                _valve = _make_device("valve", cfg.VALVE_PIN)
            except Exception as e:
                _valve = None
                print(f"[actuators] WARNING: valve relay on GPIO{cfg.VALVE_PIN} unavailable ({e}). "
                      f"The intake CANNOT be controlled.")

        _init_done = True
        _force_off()


def hardware_ok():
    """True only when every relay we are supposed to drive is actually claimed."""
    if _simulated:
        return False
    return _pump is not None and _valve is not None


def _set(device, on):
    """Drive one device. Never raises — a relay we can't set must not stop the other."""
    if _simulated or device is None:
        return False
    try:
        device.on() if on else device.off()
        return True
    except Exception as e:
        print(f"[actuators] ERROR: failed to set relay ({e})")
        return False


def _force_off():
    """
    Fail-safe: everything off. Each device is guarded separately, so a failure on one
    relay can never leave the other energised or escape to kill the control loop.

    Obeys SAFETY RULE 4 (see module docstring). This used to discard both _set()
    results and unconditionally record pump="off", which made the ONE path that must
    never lie — it runs at boot, on watchdog expiry, and on shutdown — report a state
    it had not achieved. A relay that failed to claim its pin sits at the power-on
    default (LOW = ENERGISED on an active-low board): the drain would be running while
    every ingest frame told the backend it was idle, so reconcileActuators saw no
    mismatch and raised no alarm.

    It also cleared the deadline unconditionally, so a single transient GPIO error
    during a network outage permanently DISARMED the watchdog while the pump was still
    energised — tick() would never retry, and the only thing that could re-arm it is a
    fresh command, which by definition is not coming during that outage.
    """
    global _deadline
    with _lock:
        ok_pump = _set(_pump, False)
        ok_valve = _set(_valve, False)
        _applied.update(
            pump="off" if (ok_pump or _simulated) else "unknown",
            valve="closed" if (ok_valve or _simulated) else "unknown",
        )
        # A device that EXISTS but refused the write is a transient fault worth retrying:
        # keep the watchdog armed so tick() comes back for it, forever if need be. A device
        # that is None can never be driven, so retrying is pointless noise — we report
        # 'unknown' instead and let the backend's reconciler alarm on it.
        retry = (_pump is not None and not ok_pump) or (_valve is not None and not ok_valve)
        if retry and not _simulated:
            # ASCII only: this line is on the safety path, and a print() that raises
            # UnicodeEncodeError under a LANG=C systemd unit would take the loop with it.
            print(f"[actuators] SAFETY: force-off FAILED - retrying in {FORCE_OFF_RETRY_SEC}s "
                  f"(pump={_applied['pump']} valve={_applied['valve']})")
            _deadline = time.monotonic() + FORCE_OFF_RETRY_SEC
        else:
            _deadline = None


def apply_command(command):
    """
    Apply a backend command dict. Anything unexpected (missing keys, a non-dict, a
    non-numeric maxRuntimeSec) is treated as ALL-OFF rather than raising: a malformed
    command from one bad deploy must not kill the loop and strand the hardware.
    """
    global _deadline
    if not _init_done:
        init()
    elif not _simulated and not hardware_ok():
        # A relay failed to claim its pin earlier. Retry, so the transient boot error
        # init()'s docstring promises to self-heal from actually does. Previously
        # _init_done was set True even when BOTH devices failed and nothing ever called
        # init() again, so the advertised retry was unreachable and a momentary lgpio
        # hiccup at boot disabled actuation for the life of the process.
        init()

    if not isinstance(command, dict):
        print(f"[actuators] malformed command ({type(command).__name__}); forcing OFF")
        _force_off()
        return

    pump_on = command.get("pump") == "on"
    valve_open = command.get("valve") == "open"

    try:
        runtime = int(command.get("maxRuntimeSec") or cfg.DEFAULT_MAX_RUNTIME_SEC)
    except (TypeError, ValueError):
        runtime = cfg.DEFAULT_MAX_RUNTIME_SEC
    if runtime <= 0:
        runtime = cfg.DEFAULT_MAX_RUNTIME_SEC

    # The watchdog window must exceed the command refresh period or the relay chatters:
    # the deadline expires before the next command can re-arm it, so the pump runs
    # `runtime`s, rests until the next frame, and repeats — while the backend believes
    # it has been draining continuously and models the exchange volume accordingly.
    # controller.py's startup check cannot catch this: it compares SEND_INTERVAL against
    # DEFAULT_MAX_RUNTIME_SEC, but THIS is the value that actually arms _deadline.
    if runtime <= cfg.SEND_INTERVAL:
        clamped = cfg.SEND_INTERVAL * 2
        print(f"[actuators] WARNING: maxRuntimeSec={runtime}s <= SEND_INTERVAL="
              f"{cfg.SEND_INTERVAL}s would chatter the relay; clamping to {clamped}s.")
        runtime = clamped

    with _lock:
        # Arm the watchdog BEFORE energising. Arming afterwards left a window where an
        # exception between the two calls could leave the pump running with no deadline.
        _deadline = (time.monotonic() + runtime) if (pump_on or valve_open) else None

        ok_pump = _set(_pump, pump_on)
        ok_valve = _set(_valve, valve_open)

        # Only claim a state we actually achieved. Reporting an assumed state is how a
        # dead relay used to look like a working one on the dashboard, while the backend
        # waited forever for ammonia to fall.
        _applied.update(
            pump=("on" if pump_on else "off") if (ok_pump or _simulated) else "unknown",
            valve=("open" if valve_open else "closed") if (ok_valve or _simulated) else "unknown",
        )

        # A refused OFF-command must keep the watchdog armed, exactly as a refused
        # force-off does. The line above sets _deadline = None for an all-off command
        # BEFORE the writes, so if _set() then throws (a transient lgpio hiccup) the relay
        # may still be energised while tick() has nothing to fire on — the drain runs with
        # no watchdog at all, and the only thing that could retry is the next backend
        # command 30s later. If the network drops in that same window (the correlated
        # failure this module exists for), nothing ever retries.
        refused_off = (
            (_pump is not None and not pump_on and not ok_pump)
            or (_valve is not None and not valve_open and not ok_valve)
        )
        if refused_off and not _simulated:
            print(f"[actuators] SAFETY: relay refused an OFF command - re-arming watchdog "
                  f"({FORCE_OFF_RETRY_SEC}s) to retry")
            _deadline = time.monotonic() + FORCE_OFF_RETRY_SEC

        applied = dict(_applied)

    reason = command.get("reason", "")
    print(f"[actuators] pump={applied['pump']} valve={applied['valve']} ({reason})")


def tick():
    """Call frequently. Forces OFF if the safety window has elapsed."""
    with _lock:
        if _deadline is not None and time.monotonic() >= _deadline:
            print("[actuators] SAFETY: no fresh command within maxRuntimeSec - forcing OFF")
            _force_off()


_watchdog_stop = None
_watchdog_thread = None


def start_watchdog(interval=0.5):
    """
    Drive tick() from a daemon thread. Call once, right after init().

    The watchdog used to run ONLY on the main loop, which meant every blocking call
    between two ticks silenced it. read_frame() blocks ~750ms-1s on the DS18B20
    conversion; send_live() blocks on HTTP (and `requests`' timeout is per-socket-op —
    it does not cover DNS resolution at all). That pushes the real worst-case pump
    runtime to maxRuntimeSec + ~12s routinely. Worse, a wedged SPI xfer2() or a 1-Wire
    read blocked on a shorted bus makes it UNBOUNDED: read_frame() never returns, tick()
    is never reached, and the pump stays on forever with no fresh command — precisely
    the runaway the watchdog exists to prevent. A watchdog that a stuck read() on the
    protected thread can silence is not a watchdog.

    Driving GPIO from this thread is safe: every relay path takes `_lock`, and gpiozero's
    OutputDevice guards its own pin access.
    """
    global _watchdog_thread, _watchdog_stop
    if _watchdog_thread is not None:
        return

    _watchdog_stop = threading.Event()

    # Bind the event to a LOCAL. Reading the module global on each iteration raced with
    # stop_watchdog() setting it to None: shutdown() holds _lock across four slow GPIO
    # calls, so the watchdog parks at tick()'s `with _lock`, and by the time it loops back
    # the global is already None -> AttributeError out of the loop condition, which sits
    # OUTSIDE the try. Harmless (relays are already off, the thread is a daemon) but it
    # made every clean `systemctl stop` print a traceback from the one module whose logs
    # actually get read.
    def _run(stop=_watchdog_stop):
        while not stop.wait(interval):
            try:
                tick()
            except Exception as e:  # a watchdog that can die is not a watchdog
                print(f"[actuators] WARNING: watchdog tick failed ({e})")

    _watchdog_thread = threading.Thread(target=_run, name="nh3-watchdog", daemon=True)
    _watchdog_thread.start()
    print(f"[actuators] watchdog thread started (ticking every {interval}s)")


def stop_watchdog(timeout=2.0):
    """Stop the watchdog thread. Only for shutdown/tests — never while a relay is live."""
    global _watchdog_thread, _watchdog_stop
    thread, stop = _watchdog_thread, _watchdog_stop
    if stop is not None:
        stop.set()
    # Join before dropping the handles, so the thread is genuinely finished rather than
    # merely unreferenced. Never join from the watchdog's own thread.
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
        if thread.is_alive():
            print("[actuators] WARNING: watchdog thread did not stop within "
                  f"{timeout}s (daemon; will die with the process)")
    _watchdog_thread = None
    _watchdog_stop = None


def state():
    """
    Current applied actuator state, for the ingest payload's `actuators`. Values are
    'on'/'off' and 'open'/'closed', or 'unknown' when a relay could not be driven.
    """
    return dict(_applied)


def shutdown():
    """
    Fail safe: turn everything off and release the pins.

    Resets the handles to None. Without that, init() saw `_pump is not None`, skipped
    reconstruction, and every later _set() ran against a CLOSED device — which raises,
    returns False, and (before the _force_off fix) was reported as a confident "off".
    The controller's own error recovery is `shutdown(); init()`, so the recovery path
    was what produced the unrecoverable state: pins released (relays energised on an
    active-low board) with the dashboard showing pump=off for the life of the process.

    ⚠ HARDWARE CAVEAT — this cannot be fully fixed in software. Releasing a pin (here,
    or when the kernel reaps a SIGKILLed process) returns it to input/pull-down = LOW,
    which ENERGISES an active-low board. Software can only narrow the window. The real
    fix is a pull-up on each relay IN line so "undriven" is genuinely "off", plus the
    Pi's hardware watchdog. See nh3_projectDocs/PROBLEMS.md (R2-7).
    """
    global _pump, _valve, _init_done
    with _lock:
        _force_off()
        for name, d in (("pump", _pump), ("valve", _valve)):
            try:
                if d is not None:
                    d.close()
            except Exception as e:
                print(f"[actuators] WARNING: failed to release {name} pin ({e})")
        _pump = None
        _valve = None
        _init_done = False
