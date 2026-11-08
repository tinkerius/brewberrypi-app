# 06 — Hardware Abstraction (HAL)

## 6.1 HAL interface

- `HW-001` All hardware access goes through one interface with exactly these operations:

| Operation | Semantics |
|---|---|
| `switchOnPump()` | Pump relay on. Idempotent. |
| `switchOffPump()` | Pump relay off. Idempotent. |
| `getPumpStatus() → bool` | `true` if pump is on. |
| `setHeating(p: float 0..1)` | Request heating power (see `CTL-040..046`). Values outside [0,1] are clamped. |
| `getHeatingStatus() → float` | Last requested heating power. |
| `getTemperature() → float or INVALID` | Latest cached kettle temperature in °C (1 decimal) or **INVALID** if no valid reading is available. `[FIX-02]` |
| `shortBeep()` | Non-blocking, see `HW-040`. |
| `longBeep()` | Non-blocking, see `HW-041`. |
| `switchEverythingOff()` | Heating 0 **and** pump off. |

- `HW-002` Two implementations exist: **Raspberry Pi** (real GPIO + DS18B20) and **Simulation** (`--dummymode`, `HW-060`). The implementation is selected at startup (`NFR-020`).
- `HW-003` All operations are thread-safe (called from engine loop, command handlers, the push channel and shutdown).

## 6.2 Wiring and configuration `[NEW]` (user decision: pins configurable)

- `HW-010` Pins, relay polarity and sensor are read from the configuration file (`data/config.schema.json`). Pin numbers use the **BCM (Broadcom) GPIO numbering**.
- `HW-011` Defaults reproduce the legacy wiring:

| Function | Legacy (WiringPi / Pi4J v1) | Default BCM | Physical header pin | Active level | Initial / safe state |
|---|---|---|---|---|---|
| Heating relay | `GPIO_04` | **23** | 16 | **low** (active-low relay) | off = HIGH |
| Pump relay | `GPIO_03` | **22** | 15 | **low** (active-low relay) | off = HIGH |
| Buzzer | `GPIO_12` | **10** | 19 | **high** | off = LOW |
| Temperature sensor | 1-Wire, first device `28-*` | 1-Wire bus (kernel `w1-gpio`, default data pin BCM 4) | 7 | – | – |

- `HW-012` Config per output: `pin` (BCM), `activeLow` (bool). "On" means driving the active level; "off" the opposite.
- `HW-013` Sensor config: `sensorId` (e.g. `28-0000056291e6`) or `"auto"` (default) = first device whose directory name starts with `28-` below the 1-Wire devices directory (`/sys/bus/w1/devices/` by default, configurable `hardware.sensor.devicesPath`).

## 6.3 Output safety

- `HW-020` On HAL initialisation, **all outputs are driven to their off state before anything else** (heating off, pump off, buzzer off).
- `HW-021` On process shutdown (normal exit, SIGINT/SIGTERM), the HAL MUST drive heating off, pump off, buzzer off and release the GPIO resources.
- `HW-022` If the temperature reading is INVALID (`HW-032`), any temperature-controlling logic MUST switch the heater off (`CTL-010`). `[FIX-02]`
  Legacy: an error sentinel value (≈ −9.2·10¹⁸) was returned as a temperature; the controller interpreted it as "very cold" and switched the heater to **100 %**.

## 6.4 Temperature sensor (DS18B20 via Linux 1-Wire sysfs)

- `HW-030` A background **sensor loop** reads the sensor continuously and caches the latest value; `getTemperature()` only returns the cache (never blocks on I/O). Pause between reads: **100 ms** (`hardware.sensor.pollPauseMilliseconds`). (One read itself takes ≈ 750 ms because of the DS18B20 conversion time; the effective rate is ≈ 1 Hz.)
- `HW-031` **Parsing** of the `w1_slave` file `[FIX-01]`. Typical content:

```text
72 01 4b 46 7f ff 0e 10 57 : crc=57 YES
72 01 4b 46 7f ff 0e 10 57 t=23125
```

  - Line 1 MUST end in `YES` (CRC valid); otherwise the reading is invalid.
  - The temperature is the (possibly negative) integer after `t=` in **milli-°C** → `value / 1000`.
  - The value is rounded to **one decimal place, half-up (away from zero)**, computed on the exact decimal value of the integer milli-degrees (`23125 → 23.1`, `23150 → 23.2`, `-1250 → -1.3`). (Legacy rounded a binary float, so e.g. `23150` became `23.1`.)
  - **Power-on reset value** `[FIX-27]` (`Q-06`): 85.0 °C is the value a DS18B20 holds after power-up. After a brief loss of power, for example when a loose sensor plug moves, the sensor can report it with a valid CRC before a new conversion has finished. A reading of **85.0 °C** (after rounding) is therefore accepted only when the last successful reading was within **2.0 K** of it (83.0 to 87.0 °C). Otherwise it is **skipped**: a warning is logged, and the cached reading and its age stay as they are (no error beep, no INVALID). Skipping is limited by `hardware.sensor.staleAfterSeconds` (5 s): when the cached reading is no longer fresh (stale, INVALID after a failed read, or none since startup), an implausible 85.0 °C counts as a failed read instead (`HW-032`, `HW-033`). A skipped value is not a successful reading. A kettle that really heats through 85 °C gets there gradually, so its readings are accepted. (Legacy accepted 85.0 °C always, so one glitch could end a `HeatUp` goal with a target ≤ 85 °C, `GOAL-002`.)
  - Legacy defect: the legacy parser took the last 6 characters of the file. It failed for temperatures < 10 °C and read **100.000 °C as 0.0 °C**.
- `HW-032` A reading is **INVALID** when: the sensor/file does not exist, reading fails, the CRC line is not `YES`, parsing fails, the reading is the implausible power-on reset value and cannot be skipped (`HW-031`), or the last successful reading is older than `hardware.sensor.staleAfterSeconds` (default **5 s**) `[NEW]`.
- `HW-033` On every failed read: log a warning (with cause), cache INVALID, and play the **error beep** (`HW-042`). Reading is retried in the next loop iteration (legacy behaviour: continuous beeping while the sensor is broken).
- `HW-034` Startup: the application waits up to **2 s** after HAL initialisation for a first reading before announcing readiness (legacy fixed sleep of 2 s).

## 6.5 Buzzer patterns

All patterns are played asynchronously (callers never block), except where noted.

- `HW-040` **Short beep** — buzzer on **500 ms**. Used: goal finished, application ready.
- `HW-041` **Long beep** (attention pattern) — on 2000 ms, off 300, on 200, off 300, on 200, off 300, on 200, off 300, on 2000 ms (total ≈ 5.8 s). Used: operator-request reminder (`DOM-033`).
- `HW-042` **Error beep** — buzzer on **500 ms** (played synchronously inside the sensor loop, so it also throttles retries).
- `HW-043` Overlapping patterns MUST NOT leave the buzzer permanently on; the buzzer is off after the last pattern ends.

## 6.6 Heating actuation

See `CTL-040 … CTL-046` (duty cycle). The heating relay's duty cycle runs in the HAL, independent of the engine tick.

## 6.7 Simulation mode (`--dummymode`)

- `HW-060` Simulated kettle, updated **every 1 s**:
  - start temperature **20.0 °C**;
  - if heating power `p > 0` and `T ≤ 100`: `T += 0.085 · p` (°C per second);
  - if `p == 0` and `T > 20`: `T −= 0.005`;
  - no sensor noise, no sensor errors; temperature reported unrounded (legacy) — SHOULD be rounded to 1 decimal like the real sensor.
- `HW-061` Pump status is tracked in memory only; heating power is stored as requested (no duty cycle needed, the simulation integrates `p` directly).
- `HW-062` Beeps are played as **1000 Hz tones on the host's audio output** if available (legacy: short = 500 ms tone; long = 1000 ms, 300 pause, 500, 300 pause, 500, 300 pause, 1000 ms). If no audio device exists, beeps are only logged. The simulation SHOULD use the same timing as `HW-041` for consistency.
- `HW-063` State changes (pump on/off, heating power changes) are logged at info level.
- `HW-064` `[NEW]` The simulation SHOULD support a configurable **time acceleration factor** (`simulation.timeScale`, default 1) applied consistently to the physics, engine tick and countdowns, so that acceptance tests with 60-minute rests can run quickly. Real hardware mode ignores it.
