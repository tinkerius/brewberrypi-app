# 12 — Auxiliary Tools

Command-line utilities for installation and diagnosis on the Raspberry Pi. They
use the same configuration file and HAL as the main application (legacy: each
tool had its own copy of pin numbers / sensor code).

## `TOOL-001` Temperature logger

- Reads the kettle temperature every **1 s** and appends a line to `temperatureLog.csv` (path configurable via argument, default current directory; file is created if missing, otherwise appended) and prints the same line to stdout.
- Line format: `<timestamp>;<temperature>` with timestamp `yyyy/MM/dd HH:mm:ss` (24-hour) `[FIX-19]` and temperature with one decimal and `.` as decimal separator; INVALID readings are written as `ERROR`.
  (Legacy used `h:mm:ss` — 12-hour clock without AM/PM — making the log ambiguous.)
- Each line is flushed immediately (safe against power loss).
- Does not switch any output (heater/pump stay off). Runs until interrupted.

## `TOOL-002` GPIO smoke test

- Drives outputs in an endless cycle so the wiring can be checked by ear/eye:
  1. wait 2 s → heating **on** → print pin and state;
  2. wait 2 s → heating **off** → print;
  3. wait 2 s → pump **on** → print;
  4. wait 2 s → pump **off** → print;
  5. wait 2 s → buzzer pulse 500 ms → print `buzzer pulse for 500ms`.
- On exit (Ctrl+C): heating off, pump off, buzzer off, release GPIO.
- `[NEW, optional]` A mode that cycles the heating duty cycle through 0.0, 0.1 … 1.0 (existed as commented-out code in legacy, with 10 s per step) to verify `CTL-042`. Each step lasts two periods of its duty cycle, at least 20 s (0.0 and 1.0: 20 s; 0.5: 40 s; 0.1 and 0.9: 200 s; one round about 15 minutes with the default `minPhaseMilliseconds`), and prints the power and the step length.

## `TOOL-003` Temperature sensor test

- Auto-detects the DS18B20 (`HW-013`), then continuously prints `yyyy/MM/dd HH:mm:ss;<temperature>` to stdout; on failure prints `Temperature could not be read: <reason>` to stderr and continues.
- Legacy had no delay between reads; the new tool SHOULD read at ≈ 1 Hz.

## Legacy tests (for reference)

The legacy "unit tests" were manual, endless-running programs, not assertions:
`MashEngineTest` (runs the fixture recipe of [07 §7.5](07-recipes.md) forever in simulation)
and `RaspberryPiHardswareControllerTest` (prints temperatures forever). They are
superseded by [11 — Acceptance Criteria](11-acceptance-criteria.md).
