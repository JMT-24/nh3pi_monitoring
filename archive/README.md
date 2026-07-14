# archive/ — reference only, NOT used in production

Early standalone sensor scripts, kept for reference. **Nothing here runs as part of the
system**, and none of it should be trusted for calibration.

Read this before running any of them:

| Script | Status |
|---|---|
| `sensors/amonia_sensor.py` | Standalone MQ137 read. **`VREF = 5.0`** — the real rig wires the MCP3008 to **3.3 V**, so every voltage it prints is ~1.5× too high. Opens SPI at import. |
| `sensors/ph_sensor.py` | Standalone pH read. Same **`VREF = 5.0`** problem. Opens SPI at import. |
| `sensors/temperature.py` | Standalone DS18B20 read. Harmless, but superseded. |

**Use these instead:**

- Wiring/connection check → `tests/test_mcp3008_nh3.py` (defaults to the correct VREF=3.3,
  supports `--scan` and `--warmup`).
- Real sensor acquisition → `sensors.py`.
- Full loop → `controller.py`, or `controller_demo.py` for a hardware-free run.

**The ppm maths here is not meaningful.** `RO = 30.0` is a datasheet placeholder, not a
clean-air calibration for this sensor. The backend works in **volts** and never uses ppm —
see `nh3_projectDocs/FACTS.md`.

Two long-standing bugs in `amonia_sensor.py` were fixed in place so the file isn't a trap
for anyone who does run it: `ratio` was computed into a discarded expression (leaving it
`0`, which made the ppm curve raise `ZeroDivisionError` on every read), and the loop slept
`2000` seconds instead of `2`.
