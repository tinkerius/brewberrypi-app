# Bug & Deviation Log (legacy → specification)

Every place where the specification **deliberately deviates** from the legacy
code. Decision rule (product owner, 2026-09-24): *fix the bugs, keep the
behaviour — on specification level.* Anything not listed here must behave like
the legacy system.

Legacy paths refer to the original Eclipse project `com.brewberrypi` (not part of this repository).

| ID | Legacy defect / behaviour | Legacy location | Spec decision | Spec refs |
|---|---|---|---|---|
| FIX-01 | DS18B20 parser takes the **last 6 characters** of the file: temperatures < 10 °C throw a parse error; **100.000 °C is read as 0.0 °C**; CRC (`YES`) is not checked. | `src/org/raspberrypi/ds18b20/DS18b20SensorImpl.java` `parseSensorFileContent` | Parse integer after `t=`, check CRC line, support negative values, exact decimal rounding. | HW-031, AC-020/021 |
| FIX-02 | On sensor read failure the cached value becomes `Long.MIN_VALUE` (≈ −9.2e18) and is returned as a temperature → controller sees "very cold" and switches the heater to **100 %**; UI shows a huge negative number. | `RaspberryPiHardwareControllerBase.startDS18b20SensorLoop`, `MashGoalBase.pursueTemperature` | INVALID state; heater off while INVALID; `null` in status; UI shows "Sensor error". | HW-001, HW-022, HW-032, CTL-010, IF-010, UI-010, AC-005 |
| FIX-03 | Duty-cycle race: when switching from a partial power to 0/1, the old worker thread is interrupted asynchronously and its `finally` block sets the heater **HIGH (off) after** the new state was applied → heater off while status says 100 %. | `RaspberryPiHardwareControllerDutyCycleImpl` `setHeating`, `DutyCycleWorker.run` | Atomic transitions; old cycle may not touch the relay after a new power is applied. | CTL-044, AC-012 |
| FIX-04 | `start` without a loaded process → `NullPointerException` in the worker thread (silent failure). | `MashEngine.startProcess`, `MashWorker.run` | Rejected with reply. | ENG-012, AC-032 |
| FIX-05 | `start` pressed twice → **two worker threads** drive the same process and hardware concurrently. | `MashEngine.startProcess` | At most one execution loop; second start rejected. | ENG-012, AC-033 |
| FIX-06 | `stop` without a previous start → `NullPointerException` (`mashWorkerThread` null). | `MashEngine.stopProcess` | Rejected with reply. | ENG-014, AC-034 |
| FIX-07 | `pause` throws `UnsupportedOperationException` ("not implemented yet"), although the UI offers a Pause button. | `MashEngine.pauseProcess` | Pause/resume implemented (heater & pump off, timers frozen). See Q-01. | ENG-013, ENG-030..032, AC-036 |
| FIX-08 | Recipes are singleton objects; loading a recipe again reuses the **same goal instances** — goals stay `Done`, captured start temperature and timer start dates are reused (rests end immediately on re-run). | `RecipeDatabase`, `MashEngine.loadProcess` | Every load creates a fresh process instance. | DOM-011, AC-037 |
| FIX-09 | `load`/`unload` while running: the worker keeps running the old (now invisible) process and keeps controlling the hardware. | `MashEngine.loadProcess/unloadProcess` | Rejected while Running/Paused. | ENG-010/011, AC-038 |
| FIX-10 | `load` with unknown recipe id → `NullPointerException`; missing parameter → exception thrown into the messaging framework. | `BrewberryPiCometdServices.loadProcess` | Validated, error reply. | DOM-003, ENG-010, AC-031 |
| FIX-11 | `respond` without process / active goal → `NullPointerException`; invalid option only logged. | `BrewberryPiCometdServices.requestResponse`, `MashGoalBase.*Request.answerByString` | Error replies, no crash. | DOM-032, ENG-015, AC-051/052 |
| FIX-12 | Reminder beep thread runs while `activeRequest != null`; **stop/unload never clear the request** → beeping every 10 s continues forever after stop. Replacing requests can start additional reminder threads. | `MashGoalBase.setNewRequest`, `MashEngine.stopProcess` | Reminders stop on answer, goal done, pause, stop, unload; single reminder loop. | DOM-033, ENG-013/014, AC-057 |
| FIX-13 | Iodine test asking phase does **no temperature control**: the heater stays in whatever state the previous rest's last tick left it (possibly on at 100 %) until the brewer answers. | `IodineTestGoal.pursue` | Hold the iodine-test temperature while asking. | GOAL-005, AC-054 |
| FIX-14 | Progress computation: division by zero when start temperature == target; negative / > 100 % values; timer message not zero-padded (`6:0 left`) and negative after expiry; iodine status message `null` before the first additional rest. | `MashGoalBase.pursueTemperature/pursueTimerange`, `IodineTestGoal.getStatusMessage` | Clamp 0..100, defined edge cases, `MM:SS left`, "Waiting for answer". | DOM-020, CTL-020, CTL-031, GOAL-005, AC-006/007 |
| FIX-15 | Command feedback never reaches the UI: server replies only to `load` (on a channel the UI does not listen to); UI listens for replies to unload/start/pause/stop that are never sent. | `BrewberryPiCometdServices`, `index.html` | Every command gets a `CommandReply`; UI shows it. | IF-050, UI-050, AC-076 |
| FIX-16 | Temperature published as a **locale-formatted string** (`String.format("%.1f")` → `"62,5"` on a German system). | `StatusBroadcastWorker.broadcastHardwareStatus` | JSON number (or null). | IF-010, AC-071 |
| FIX-17 | UI shows **hard-coded demo chart data** (fake series up to 300 °C) and a hard-coded recipe list until the first broadcast; chart filter `temp > 0` used as validity check. | `brewberrypi.angularjs-controllers.js`, `ChartBroadcastWorker` | Empty until data arrives; filter on validity. | IF-041, UI-022, UI-040, AC-074 |
| FIX-18 | "No process" is published as the **string** `"null"` (workaround for a cometd.js crash). | `StatusBroadcastWorker.broadcastProcessState` | `{ "state": "NoProcess" }`. | IF-020, AC-072 |
| FIX-19 | Temperature logger timestamp `yyyy/MM/dd h:mm:ss` = 12-hour clock without AM/PM. | `src/com/brewberrypi/tools/TemperatureLoggerStart.java` | `HH:mm:ss`. | TOOL-001 |
| FIX-20 | No explicit process state: UI cannot distinguish loaded / running / finished / stopped. | `MashEngine` | Engine/process state machine, published in ProcessState. | DOM (2.5), ENG-001, IF-020, UI-011 |
| FIX-21 | Web server starts accepting connections before hardware controller, engine and command services exist. | `web/Start.main` | Defined startup order. | NFR-021 |
| FIX-22 | An exception in a goal's `pursue` is logged and retried every tick, but the heater keeps its last state (possibly on). | `MashWorker.run` | Heater off on the failing tick. | ENG-020/023 |
| FIX-24 | Goal descriptions containing `\n` (iodine additional rest) are rendered on one line in HTML. | `index.html` | Render line breaks. | UI-031 |
| FIX-25 | Timers use wall-clock `new Date()`; the Pi has no RTC, NTP time jumps after boot distort rests. | `MashGoalBase.pursueTimerange` | Monotonic clock. | NFR-005 |
| FIX-26 | Duty cycle with a **500 ms** minimum phase: at 50 % power (near the target and during the whole boil) the mechanical heater relay switches about once per second and wears out after a few dozen hours of brewing. | `RaspberryPiHardwareControllerDutyCycleImpl` | Same formula, minimum phase **10 s** (10 s on + 10 s off at 50 %), configurable. See Q-26. | CTL-042, AC-010, AC-011, TOOL-002 |

(FIX-23 intentionally unused.)

## Behaviour intentionally **kept** although questionable

These are *not* fixed; they are listed in [../specification/13-open-questions.md](../specification/13-open-questions.md):

- MashIn / PourInBrewingWater do not control temperature while waiting (Q-03).
- Boil at 50 % power because of the near-target band at 100 °C target (Q-04).
- Spelling in texts (`successfull`, `Maltoserastr`) (Q-07).
- Chart history since server start, shared, not reset per process (Q-08).
- Continuous error beeping while the sensor is broken (HW-033).
- Iodine description grows by one line per "No" answer (GOAL-005).
- `HeatUpForHopsCooking` leaves the heater at 100 % when done (GOAL-007).

## New features (not in legacy) — marked `[NEW]` in the spec

Configurable wiring & constants (OV-007, HW-010..013, NFR-030), recipe directory with one file per recipe (RCP-001),
sensor staleness (HW-032), simulation time scale (HW-064), `--config` (NFR-020),
service installation / non-root (NFR-023/024), crash-safe outputs (NFR-003),
button enabling & stop confirmation (UI-042/043), engine state display (UI-011),
request/response commands with outcomes and changes pushed instead of 500 ms broadcasts (IF-001, IF-052, IF-072, Q-28),
`resume` command (ENG-016), request ids (DOM-035), recipe details (IF-031), same-origin rule (IF-061, Q-17).

## Legacy data not taken over

Of the 8 legacy recipes only `BENIWEISS` and `WaterBoil` are shipped; the other 6 are removed (`Q-32`, `RCP-002`).
