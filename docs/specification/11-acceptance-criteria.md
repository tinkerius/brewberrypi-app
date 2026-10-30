# 11 — Acceptance Criteria

Unless stated otherwise: simulation HAL or mock HAL, default configuration,
controllable/accelerated clock. `T` = temperature reported by the HAL.

## 11.1 Temperature control

- `AC-001` (CTL-010/011) **Given** heater off and target 62.0 **When** T = 61.95 **Then** heater stays off (switch point 61.9). **When** T = 61.85 **Then** heating = 0.5.
- `AC-002` (CTL-011) **Given** heater at 0.5 and target 62.0 **When** T = 61.95 **Then** heater stays 0.5. **When** T = 62.0 **Then** heating = 0 and `holdTemperature` returns true.
- `AC-003` (CTL-012) **Given** target 62.0, heater off **When** T = 59.0 (distance exactly 3.0) **Then** heating = 1.0. **When** T = 59.1 **Then** heating = 0.5.
- `AC-004` (CTL-013) **Given** HeatUpForHopsCooking **When** T = 96.9 **Then** heating = 1.0; **When** T = 97.0 **Then** goal Done and heating still 1.0 at that tick.
- `AC-005` (CTL-010, FIX-02) **Given** any temperature-controlling goal active with heater at 1.0 **When** the sensor becomes INVALID **Then** heating = 0 within one tick, the goal's `statusmessage` is `Sensor error`, `/hardware/status.temperature` is `null` and the UI shows `Sensor error`. **When** valid readings return **Then** control resumes.
- `AC-006` (CTL-020, FIX-14) HeatUp 62 with start 20.0: T = 41.0 → `statuspercent` 50, `statusmessage` `50%`. Start 62.5 (already above target) → goal Done on first tick with `statuspercent` 100 (no division by zero).
- `AC-007` (CTL-031) Rest 10 min: after 4 min running → `statuspercent` 40, `statusmessage` `06:00 left`; after 10 min → Done; message never negative.

## 11.2 Duty cycle

- `AC-010` (CTL-042, FIX-26) `setHeating(0.25)` → relay on 10 s / off 30 s repeating (±20 ms). `setHeating(0.75)` → on 30 s / off 10 s.
- `AC-011` (CTL-043) Calling `setHeating(0.5)` every 500 ms keeps a regular 10 s / 10 s cycle (not restarted).
- `AC-012` (CTL-044, FIX-03) While a 0.5 cycle runs (in any phase): `setHeating(1.0)` → relay on within 50 ms and **stays on** for ≥ 25 s (longer than one 0.5 period); `getHeatingStatus()` = 1.0. `setHeating(0)` → relay off and stays off.

## 11.3 Sensor

- `AC-020` (HW-031) File `...YES\n... t=23125` → 23.1; `t=9875` → 9.9; `t=-1250` → −1.3 *(half-up on magnitude: −1.25 → −1.3)*; `t=100000` → 100.0; `t=23150` → 23.2.
- `AC-021` (HW-031) CRC line ending `NO` → INVALID, warning logged, error beep played.
- `AC-022` (HW-032) No valid reading for > 5 s → `getTemperature()` INVALID.
- `AC-023` (HW-013) `sensorId = "auto"` with devices `w1_bus_master1`, `28-0000056291e6` → uses `28-0000056291e6/w1_slave`.

## 11.4 Engine lifecycle and commands

- `AC-030` (ENG-010) **Given** `NoProcess` **When** load `ZORA` **Then** state `Loaded`, 11 goals all `Waiting`, reply `success=true`, message `Process München 12.6 - SUD1:ZORA (ZORA) loaded.`
- `AC-031` (FIX-10) load `NOPE` → `success=false`, state unchanged. load without `recipe` → `success=false`, message `parameter 'recipe' not set.`
- `AC-032` (FIX-04) start in `NoProcess` → `success=false`, no crash.
- `AC-033` (FIX-05) start twice → second is rejected; exactly one execution loop (verified by goal tick count).
- `AC-034` (FIX-06) stop in `Loaded` → `success=false`, no crash.
- `AC-035` (ENG-014) **Given** `Running` with heater 1.0, pump on **When** stop **Then** within 500 ms: heating 0, pump off, state `Stopped`, active goal still `Active`, no further beeps.
- `AC-036` (FIX-07) **Given** Rest 62/10 running, 3 min elapsed **When** pause **Then** heating 0, pump off, state `Paused`, countdown frozen at `07:00 left`. **When** 5 min pass and resume (`ENG-016`) **Then** state `Running`, countdown continues from `07:00 left`. `start` while paused is rejected (conflict).
- `AC-037` (FIX-08) Run TEST to `Finished`, load TEST again → all goals `Waiting`, iodine timer not started; starting it asks the question again.
- `AC-038` (FIX-09) load or unload while `Running` → rejected; running process unaffected.
- `AC-039` (ENG-025) Process reaching the end → heating 0, pump off, state `Finished`, short beep after the last goal.

## 11.5 Goals and operator requests

- `AC-050` (GOAL-001, DOM-033) PourInBrewingWater active → request `Pour in brewing water` / `["Confirm"]` visible; long beep immediately and every 10 s. **When** respond `Confirm` **Then** goal Done within one tick, reminders stop, short beep.
- `AC-051` (DOM-032, FIX-11) respond `confirm` (lowercase) → `success=false`, request still active.
- `AC-052` (FIX-11) respond with no process loaded → `success=false`, `There is no active request to respond to.`
- `AC-053` (GOAL-005) TEST recipe: IodineTest 25/1. Answer `No` → request removed, description gains line `Rest at 25.0 degrees (C) for 1min.`, countdown 1 min runs with temperature held at 25; after 1 min the question is asked again (new reminders). Answer `Yes` → Done → process `Finished`.
- `AC-054` (FIX-13) During the iodine question, with T dropping below 24.9 → heater switched on (0.5); T ≥ 25 → off.
- `AC-055` (GOAL-006) Lautering active → heating 0 and pump off on every tick, request `Lauter now`.
- `AC-056` (GOAL-003) MashIn active after HeatUp → heater off, pump stays on, no temperature control until confirmed.
- `AC-057` (FIX-12) Stop while a request is pending → reminder beeps stop; no reminder loop continues after stop/unload/pause.
- `AC-058` (DOM-035, IF-054) TEST recipe: answer `No` to the iodine question with its id; when the question is asked again (a new id), an answer naming the first id → `success=false`, `This request is no longer open.`, outcome conflict, the new question stays unanswered; an answer naming the new id is accepted.

## 11.6 Interface and UI

- `AC-070` (IF-005, IF-071, IF-053) A new push connection receives a `snapshot` with hardware status, process state, recipes and chart within 1 s. After `load`, the new process state is pushed to every connection before the command's reply is sent.
- `AC-071` (IF-010, FIX-16) `hardware.temperature` is a JSON number with 1 decimal regardless of server locale.
- `AC-072` (IF-020, FIX-18) No process → `{ "state": "NoProcess" }`.
- `AC-073` (IF-043) Feeding 201 points with gaps 1 s except one gap of 0.2 s between points 50/51 → point 51 is removed, point count 200.
- `AC-074` (IF-041) INVALID readings are not added to the chart.
- `AC-075` (UI-031, UI-032) Active goal shows progress bar with `statusmessage`; Done goals green; request buttons send the exact option text for that request's id.
- `AC-076` (UI-050) Every command shows the server reply message in the Information modal (or toast per `Q-11`).
- `AC-077` (UI-060) Stopping the server → UI shows "disconnected"; restarting → UI reconnects and shows live data without page reload.
- `AC-092` (UI-001) The top bar shows the BrewberryPi logo with the name `BrewberryPi` and `<host> · <version>` with the `version` of `/healthz`; without a readable version only the host is shown.
- `AC-093` (UI-002, IF-080) With the default configuration the top bar links `Device settings` to `http://<host>:8081/` of the host the page was opened with, in a new tab; `GET /api/ui` answers `{"deviceLink": "http://{host}:8081/"}`. With `ui.deviceLink` empty, `deviceLink` is `null` and there is no link.
- `AC-078` (IF-052) Outcomes: `load ZORA` → accepted; `start` twice → the second is a conflict, `Process is already running.`; `load NOPE` → not found; `respond confirm` (lowercase) → invalid; `load` with `{"recipe": 7}` → invalid. Every reply has `command`, `success`, `message`.
- `AC-079` (IF-072) With nothing changing, no state event is pushed (only `ping`); a changed temperature is pushed as one `hardware` event within one check period.
- `AC-060` (IF-061) A command from another web page (`Origin` not the server) → forbidden and nothing changes; a command not sent as JSON → refused; the same command from the server's own page or without `Origin` (command-line tool) is carried out.

## 11.7 Recipes and configuration

- `AC-080` (RCP-002) The shipped recipe files equal [data/recipes/](data/recipes/) (8 files `<id>.json`); the recipe list shows 8 recipes in the order BENIWEISS, Dummy, HANCOCK, HIPA, TEST, WIT, WaterBoil, ZORA.
- `AC-081` (RCP-003) A recipe file with a `Rest` lacking `minutes` → the application starts, logs an error naming the file, recipe and goal index, and that recipe is missing from the recipe list; the other recipes are listed.
- `AC-082` (HW-011) With the example config, the heating relay is BCM 23 active-low, pump BCM 22 active-low, buzzer BCM 10 active-high.
- `AC-083` (NFR-001/HW-020) On startup with real hardware, heating and pump relays are off before the web server accepts connections.
- `AC-084` (RCP-001) First start without a recipe directory → the directory is created with the 8 shipped recipe files, and the recipe list shows them. After two of the files are deleted, the next start lists 6 recipes and does not write the deleted files again.
- `AC-085` (RCP-006) While a recipe is loaded, a recipe file is added and the loaded recipe's file is changed → within `recipes.checkMilliseconds` every client receives the new recipe list; the loaded process keeps its name and goals. Removing the file again removes the recipe from the list.
- `AC-086` (NFR-020, NFR-032) Start with `--config` naming a file that does not exist → the file is written with every key of [data/config.example.json](data/config.example.json) at its default (without `$schema`), group-writable, and the application starts.
- `AC-094` (RCP-003, RCP-007) A recipe file in the old format, with `goals` at the top level instead of in `mashing_process` → the application starts, logs an error naming the file and recipe and saying to move `goals` into `mashing_process`, and that recipe is missing from the recipe list; the other recipes are listed.

## 11.8 End-to-end (simulation, accelerated)

- `AC-090` Load `Dummy`, start, answer all requests (`Yes` for iodine, `Confirm` for lautering) → process reaches `Finished`; chart shows the heat-up to 62, a hold at 62 for 10 (simulated) min, heat-up to 78, cool-down during lautering, heat-up to 97.
- `AC-091` Full `ZORA` run in simulation → all 11 goals `Done`, final state `Finished`, heater and pump off.
