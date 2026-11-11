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
- `AC-117` (HW-031, HW-032, FIX-27) Last successful reading 65.0 °C, then a reading of 85.0 °C → skipped: `getTemperature()` stays 65.0, warning logged, no error beep; the next reading 65.1 °C is valid. 85.0 °C for more than 5 s after the reading of 65.0 °C → INVALID, warning logged, error beep played. Last successful reading 84.0 °C (also with a failed read in between) → 85.0 °C is valid. No successful reading since startup → 85.0 °C is INVALID with an error beep.

## 11.4 Engine lifecycle and commands

- `AC-030` (ENG-010) **Given** `NoProcess` **When** load `BENIWEISS` **Then** state `Loaded`, 11 goals all `Waiting`, reply `success=true`, message `Process München 12.6 - SUD2:BENIWEISS (BENIWEISS) loaded.`
- `AC-031` (FIX-10) load `NOPE` → `success=false`, state unchanged. load without `recipe` → `success=false`, message `parameter 'recipe' not set.`
- `AC-032` (FIX-04) start in `NoProcess` → `success=false`, no crash.
- `AC-033` (FIX-05) start twice → second is rejected; exactly one execution loop (verified by goal tick count).
- `AC-034` (FIX-06) stop in `Loaded` → `success=false`, no crash.
- `AC-035` (ENG-014) **Given** `Running` with heater 1.0, pump on **When** stop **Then** within 500 ms: heating 0, pump off, state `Stopped`, active goal still `Active`, no further beeps.
- `AC-036` (FIX-07) **Given** Rest 62/10 running, 3 min elapsed **When** pause **Then** heating 0, pump off, state `Paused`, countdown frozen at `07:00 left`. **When** 5 min pass and resume (`ENG-016`) **Then** state `Running`, countdown continues from `07:00 left`. `start` while paused is rejected (conflict).
- `AC-037` (FIX-08) Run the test recipe `IODINE` ([07](07-recipes.md) §7.5) to `Finished`, load it again → all goals `Waiting`, iodine timer not started; starting it asks the question again.
- `AC-038` (FIX-09) load or unload while `Running` → rejected; running process unaffected.
- `AC-039` (ENG-025) Process reaching the end → heating 0, pump off, state `Finished`, short beep after the last goal.

## 11.5 Goals and operator requests

- `AC-050` (GOAL-001, DOM-033) PourInBrewingWater active → request `Pour in brewing water` / `["Confirm"]` visible; long beep immediately and every 10 s. **When** respond `Confirm` **Then** goal Done within one tick, reminders stop, short beep.
- `AC-051` (DOM-032, FIX-11) respond `confirm` (lowercase) → `success=false`, request still active.
- `AC-052` (FIX-11) respond with no process loaded → `success=false`, `There is no active request to respond to.`
- `AC-053` (GOAL-005) Test recipe `IODINE`: IodineTest 25/1. Answer `No` → request removed, description gains line `Rest at 25.0 degrees (C) for 1min.`, countdown 1 min runs with temperature held at 25; after 1 min the question is asked again (new reminders). Answer `Yes` → Done → process `Finished`.
- `AC-054` (FIX-13) During the iodine question, with T dropping below 24.9 → heater switched on (0.5); T ≥ 25 → off.
- `AC-055` (GOAL-006) Lautering active → heating 0 and pump off on every tick, request `Lauter now`.
- `AC-056` (GOAL-003) MashIn active after HeatUp → heater off, pump stays on, no temperature control until confirmed.
- `AC-057` (FIX-12) Stop while a request is pending → reminder beeps stop; no reminder loop continues after stop/unload/pause.
- `AC-058` (DOM-035, IF-054) Test recipe `IODINE`: answer `No` to the iodine question with its id; when the question is asked again (a new id), an answer naming the first id → `success=false`, `This request is no longer open.`, outcome conflict, the new question stays unanswered; an answer naming the new id is accepted.

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
- `AC-078` (IF-052) Outcomes: `load BENIWEISS` → accepted; `start` twice → the second is a conflict, `Process is already running.`; `load NOPE` → not found; `respond confirm` (lowercase) → invalid; `load` with `{"recipe": 7}` → invalid. Every reply has `command`, `success`, `message`.
- `AC-079` (IF-072) With nothing changing, no state event is pushed (only `ping`); a changed temperature is pushed as one `hardware` event within one check period.
- `AC-060` (IF-061) A command from another web page (`Origin` not the server) → forbidden and nothing changes; a command not sent as JSON → refused; the same command from the server's own page or without `Origin` (command-line tool) is carried out.

## 11.7 Recipes and configuration

- `AC-080` (RCP-002) The shipped recipe files equal [data/recipes/](data/recipes/) (2 files `<id>.json`); the recipe list shows 2 recipes in the order BENIWEISS, WaterBoil.
- `AC-081` (RCP-003) A recipe file with a `Rest` lacking `minutes` → the application starts, logs an error naming the file, recipe and goal index, and that recipe is missing from the recipe list; the other recipes are listed.
- `AC-082` (HW-011) With the example config, the heating relay is BCM 23 active-low, pump BCM 22 active-low, buzzer BCM 10 active-high.
- `AC-083` (NFR-001/HW-020) On startup with real hardware, heating and pump relays are off before the web server accepts connections.
- `AC-084` (RCP-001) First start without a recipe directory → the directory is created with the 2 shipped recipe files, and the recipe list shows them. After one of the files is deleted, the next start lists 1 recipe and does not write the deleted file again.
- `AC-085` (RCP-006) While a recipe is loaded, a recipe file is added and the loaded recipe's file is changed → within `recipes.checkMilliseconds` every client receives the new recipe list; the loaded process keeps its name and goals. Removing the file again removes the recipe from the list.
- `AC-086` (NFR-020, NFR-032) Start with `--config` naming a file that does not exist → the file is written with every key of [data/config.example.json](data/config.example.json) at its default (without `$schema`), group-writable, and the application starts.
- `AC-094` (RCP-003, RCP-007) A recipe file in the old format, with `goals` at the top level instead of in `mashing_process` → the application starts, logs an error naming the file and recipe and saying to move `goals` into `mashing_process`, and that recipe is missing from the recipe list; the other recipes are listed.
- `AC-095` (RCP-008) A recipe file without `version` → read as version 1, so it is migrated and written back like any version-1 file (`AC-101`). A recipe file in the latest version is not written (unchanged content and modification time), and no `.backup` directory is created.
- `AC-096` (RCP-009) A recipe file with a `version` greater than the latest version → the application starts, logs an error naming the file, the recipe, the file's version and the latest version and saying to update the application, and that recipe is missing from the recipe list; the other recipes are listed; the file is unchanged.
- `AC-097` (RCP-010) With a recipe format whose latest version is 4 and migrators `1 → 2`, `2 → 3`, `3 → 4`: a valid version-1 recipe file is listed with the content produced by running the three migrators in that order; its file is written back with `"version": 4`, and `.backup/<id>.v1.json` holds the original file unchanged. Read again, the file is not migrated or written again.
- `AC-098` (RCP-010) A migrator that fails, or a migrated recipe that is not valid in the latest format → the recipe is skipped with an error naming the file, the recipe and the migration step; its file is unchanged and no copy is written to `.backup`. A version-1 file that is invalid in version 1 is skipped with the error of that check, before any migrator runs. If the migrated file cannot be written, the migrated recipe is listed, the file is unchanged and a warning is logged.
- `AC-099` (RCP-009) A recipe file with `"version"` `0`, `-1`, `1.5`, `"1"`, `true` or `null` → skipped, the error says `version` must be an integer ≥ 1.
- `AC-100` (RCP-007, ENG-010, Q-34) A recipe file with `"mashing_process": {"goals": []}` → listed; `GET /api/recipes/<id>` answers its `goals` as `[]`; `load` of it → `success=false`, outcome conflict (HTTP 409), `Recipe <name> (<id>) has no goals.`, and a process loaded before stays loaded. A file without `mashing_process` is still skipped.
- `AC-101` (RCP-010, Q-34) A valid version-1 recipe file (with or without `"version": 1`) → listed with the same name and goals; its file is written back in the latest version (`"version": 3`) right after `$schema`, otherwise unchanged, and `.backup/<id>.v1.json` holds the original file unchanged.

## 11.8 End-to-end (simulation, accelerated)

- `AC-090` Load the test recipe `DEMO` ([07](07-recipes.md) §7.5), start, answer all requests (`Yes` for iodine, `Confirm` for lautering) → process reaches `Finished`; chart shows the heat-up to 62, a hold at 62 for 10 (simulated) min, heat-up to 78, cool-down during lautering, heat-up to 97.
- `AC-091` Full `BENIWEISS` run in simulation → all 11 goals `Done`, final state `Finished`, heater and pump off.

## 11.9 Cloud Sync (`[NEW]`, [14](14-cloud-sync.md))

The criteria use a fake provider: no network, a controllable clock, and a cloud folder the test fills.

- `AC-102` (SYNC-002, SYNC-004, SYNC-016) `cloudSyncConnect` while disconnected → accepted, `Connecting to Google Drive.`; the status becomes `connecting` with the provider's code, address and expiry. Once the brewer allows access → `idle` with the account, `.cloud-sync/credentials.json` written, the notice file `DO-NOT-EDIT-Cloud-Sync.txt` written with the account in its text, and the recipe directory mirrors the cloud folder.
- `AC-103` (SYNC-011) A recipe added or changed in the cloud → written to the recipe directory at the next sync; a sync without changes in the cloud downloads nothing and writes nothing (files unchanged, modification times unchanged).
- `AC-104` (SYNC-011) A synced recipe file changed on the device → at the next sync it has the cloud content again and `.backup/<id>.local.json` holds the changed file. A synced recipe file deleted on the device → written again.
- `AC-105` (SYNC-011) A recipe file with no cloud recipe (removed or renamed in the cloud, or added on the device) → moved to `.backup/<id>.removed.json`; other files, hidden files and subdirectories of the recipe directory are unchanged.
- `AC-106` (SYNC-012, SYNC-013) A cloud file in format version 1 → written in the latest version, without a `.backup/<id>.v1.json` copy. A cloud file in a newer format version, an invalid one, one named `bad name.json`, and two files with the same name → none of them is on the device (an existing file of that id is moved to `.backup`), each is listed in `issues` with the message of `SYNC-013`; a file not ending in `.json` is ignored.
- `AC-107` (SYNC-014) The provider cannot be reached → state `offline` with `Google Drive cannot be reached: …`; the cloud folder is missing → state `error` with the message of `SYNC-014`; a download fails after the listing → the recipe directory is unchanged in every case. An empty cloud folder that exists → every recipe file moved to `.backup`.
- `AC-108` (SYNC-020, SYNC-021) Connected at startup → a sync runs without a new sign-in. Then a sync every `cloudSync.intervalSeconds`; `cloudSyncNow` → accepted, `Cloud Sync started.`, a sync at once. After failed syncs the waits are 30, 60, 120, 240, 300, 300 s (interval 30); after a successful one, 30 s again.
- `AC-109` (SYNC-005) `cloudSyncDisconnect` → accepted, `Cloud Sync disconnected.`; the provider is asked to end the sign-in, the credentials file and the notice file are deleted, the recipe files stay; after a restart Cloud Sync is `disconnected` and the files are not changed.
- `AC-110` (SYNC-006) The provider rejects the stored sign-in → `disconnected` with `Access to Google Drive was revoked. Connect again.`, credentials and notice file deleted, recipe files unchanged.
- `AC-111` (SYNC-003) The sign-in code expires, the brewer denies access, or allows the account but not the files → `disconnected` with the message of `SYNC-003`; no credentials file and no change to the recipe directory.
- `AC-112` (SYNC-030) The replies of `SYNC-030`: `cloudSyncConnect` while connected, `cloudSyncDisconnect` and `cloudSyncNow` while disconnected → conflict (HTTP 409) with their messages; every command while `unavailable` → conflict, `Cloud Sync is not available.`
- `AC-113` (SYNC-001) `cloudSync.enabled` `false`, or no client id for the provider → state `unavailable`, no request to the provider, and the UI shows no Cloud Sync control.
- `AC-114` (SYNC-022, RCP-006) A process is loaded from a synced recipe, then the recipe changes in the cloud and is synced → the recipe list shows the new recipe, the loaded process keeps its goals; the sync never switches an output.
- `AC-115` (SYNC-004) After connecting, syncing and disconnecting, the log contains no token, sign-in code or recipe content.
- `AC-116` (IF-090, IF-091, UI-080..085) The `cloudSync` status is part of the snapshot and pushed when it changes, the start and end of a sync at once. The top bar shows the Cloud Sync control for each state as in §9.9 (pictogram, short text, issue count); its menu shows the connect dialog with its text, the code and its link while connecting (opened by itself), account, last sync and issues while connected, and the message. A sync shorter than 1.5 s is shown as syncing for 1.5 s. A successful Cloud Sync command opens no Information window; one that did not succeed does (UI-084).
- `AC-118` (RCP-010, RCP-011, Q-37) A valid version-2 recipe file → listed with the same name and goals; its file is written back with `"version": 3` right after `$schema`, otherwise unchanged, and `.backup/<id>.v2.json` holds the original file unchanged.
- `AC-119` (RCP-012, Q-37) A version-3 recipe file with a valid `mashing` (e.g. `MaltoseRest` 63 °C 45 min and `MashOut` 78 °C 10 min) → listed, its goals and details as without `mashing`, the file not written. `mashing` that is not an object, or with an unknown rest id, a rest without `temperature` or `minutes`, a `temperature` outside 0–110, non-integer or negative `minutes`, or another key in a rest → the recipe is skipped (`RCP-003`).
- `AC-120` (RCP-013, Q-38) A version-3 recipe file with valid `ingredients` (two malts, one hop; also an empty `hops` list, or only `malts`) → listed, its goals and details as without `ingredients`, the file not written. `ingredients` that is not an object, or with another key, a list that is not a list, an entry without `name` or `grams`, an empty `name`, `grams` that is not an integer ≥ 1, or another key in an entry → the recipe is skipped (`RCP-003`).
- `AC-121` (RCP-013, RCP-014, Q-39) A version-3 recipe file with malts with `ebc` (3.5, 0), hops with `alpha` (5.5, 0, 100) and `minutes` (60, 0), and `batch` with `liters` 20 and `plato` 12.6 (also either alone, or `batch` empty) → listed as without them, the file not written. A negative `ebc`; `alpha` below 0, above 100 or a string; non-integer or negative `minutes`; `ebc` on a hop or `alpha` on a malt; `batch` not an object, with another key, `liters` 0 or negative, or `plato` below 0 or above 40 → the recipe is skipped (`RCP-003`).
- `AC-122` (RCP-015, Q-40) A version-3 recipe file with `water` `mash` 16, `sparge` 14, `top_up` 2 (also one of them alone, `top_up` 0, or `water` empty) → listed as without it, the file not written. `water` not an object, with another key, or with a negative or non-number volume → the recipe is skipped (`RCP-003`).
- `AC-123` (RCP-014, Q-41) A version-3 recipe file with `batch` `final_plato` 2.5 (also 0 or 40, alone or with `plato`) → listed as without it, the file not written; `final_plato` below 0, above 40 or not a number → the recipe is skipped (`RCP-003`).
- `AC-124` (RCP-013, Q-42) A version-3 recipe file with `ingredients.others` (Irish Moss 10 g 5 min; also without `minutes`, or an empty list) → listed as without it, the file not written; an other addition with `alpha`, without `name` or `grams`, or with non-integer `minutes` → the recipe is skipped (`RCP-003`).
- `AC-125` (RCP-013, Q-43) A version-3 recipe file with a hop with two `additions` (60 min 20 g, 10 min 10 g; also one without `minutes`) → listed as without it, the file not written; a hop without `additions`, with an empty `additions` list, an addition without `grams`, with `grams` 0, with another key, or a hop with `grams` or `minutes` of its own → the recipe is skipped (`RCP-003`).
- `AC-126` (RCP-016, Q-44) A version-3 recipe file with a `description` (one line, several lines, or empty) → listed as without it, the file not written; a `description` that is not a string → the recipe is skipped (`RCP-003`).
- `AC-127` (RCP-017, Q-45) A version-3 recipe file with `fermentation` `yeast` `{name: "WB-06", grams: 11.5}` and `co2` 6.5 (also the yeast without `grams`, either alone, or `fermentation` empty) → listed as without it, the file not written; `fermentation` not an object or with another key, a yeast without `name`, with an empty `name`, with `grams` 0 or a string, or with another key, a `co2` of 0 or a string → the recipe is skipped (`RCP-003`).
