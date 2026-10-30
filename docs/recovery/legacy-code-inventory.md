# Legacy Code Inventory & Traceability

Legacy project: `com.brewberrypi` (Eclipse Java project, Java 7) — kept outside this repository; this file records provenance only.
All first-party files were read completely during recovery (2026-09-24).
`bin/` contains compiled classes and a byte-identical copy of `src/webcontent/`.
`lib/` contains only third-party libraries.

## First-party source → specification

| Legacy file | Responsibility | Spec |
|---|---|---|
| `src/com/brewberrypi/web/Start.java` | Main entry: `--dummymode`, Jetty on port 8080, static files from classpath `webcontent/`, CometD servlet `/cometd/*`, creates HAL/engine/services/channels/broadcast threads, ready beep, shutdown hook | NFR-020..022, IF-002, HW-002 |
| `src/com/brewberrypi/web/cometd/CometdChannels.java` | Channel names | 08 §8.2, §8.3 |
| `src/com/brewberrypi/web/cometd/BrewberryPiCometdServices.java` | Command handlers load/unload/start/pause/stop/request | ENG-010..015, IF-050 |
| `src/com/brewberrypi/web/cometd/StatusBroadcastWorker.java` | 500 ms broadcast of hardware status, process state, recipe list | IF-010, IF-020, IF-030 |
| `src/com/brewberrypi/web/cometd/ChartBroadcastWorker.java` | 1 s temperature sampling, 200-point thinning, chart broadcast | IF-040..044 |
| `src/com/brewberrypi/mash/MashEngine.java` | Holds one process, load/unload/start/pause(unsupported)/stop | 03 |
| `src/com/brewberrypi/mash/MashWorker.java` | Sequential goal loop, 500 ms tick, beep on goal done, all off at end | ENG-020..026 |
| `src/com/brewberrypi/mash/BrewProcess.java` | Process id/name/goals, active goal lookup, JSON | DOM-010..013, IF-020 |
| `src/com/brewberrypi/mash/goals/base/MashGoalBase.java` | Goal base: status, requests (Confirmation, YesNo), reminder beeps, hysteresis controller, countdown, progress, JSON | DOM-020..034, CTL-010..031 |
| `src/com/brewberrypi/mash/goals/base/KeepTemperatureForTimerangeBase.java` | Hold temperature + countdown | GOAL-004, GOAL-008 |
| `src/com/brewberrypi/mash/goals/HeatUpGoal.java` | Heat up to T | GOAL-002 |
| `src/com/brewberrypi/mash/goals/Rest.java` | Rest T/M | GOAL-004 |
| `src/com/brewberrypi/mash/goals/CookWaterGoal.java` | Rest at 100 °C | GOAL-008 |
| `src/com/brewberrypi/mash/goals/HeatUpForHopsCooking.java` | Heat to 97 °C with boost | GOAL-007 |
| `src/com/brewberrypi/mash/goals/MashInGoal.java` | Confirmation "Mash in now" | GOAL-003 |
| `src/com/brewberrypi/mash/goals/PourInBrewingWaterGoal.java` | Confirmation "Pour in brewing water" | GOAL-001 |
| `src/com/brewberrypi/mash/goals/IodineTestGoal.java` | Yes/No + additional rest loop | GOAL-005 |
| `src/com/brewberrypi/mash/goals/LauteringGoal.java` | All off + confirmation "Lauter now" | GOAL-006 |
| `src/com/brewberrypi/mash/goals/RecipeDatabase.java` | 8 hard-coded recipes | 07, data/recipes/ |
| `src/com/brewberrypi/hardware/HardwareController.java` | HAL interface | HW-001 |
| `src/com/brewberrypi/hardware/RaspberryPiHardwareControllerBase.java` | GPIO provisioning (WiringPi 4/3/12), active-low relays, sensor loop 100 ms, beeps, shutdown hook | HW-010..043 |
| `src/com/brewberrypi/hardware/RaspberryPiHardwareControllerDutyCycleImpl.java` | Duty-cycle heating (used by main app) | CTL-040..045 |
| `src/com/brewberrypi/hardware/RaspberryPiHardwareControllerImpl.java` | On/off heating (used only by temperature logger & a test) | CTL-046 |
| `src/com/brewberrypi/hardware/DummyHardwareControllerImpl.java` | Simulation physics + audio beeps | HW-060..063 |
| `src/org/raspberrypi/ds18b20/IDS18b20Sensor.java`, `DS18b20SensorImpl.java` | 1-Wire sysfs sensor discovery & parsing | HW-013, HW-030..033 |
| `src/org/raspberrypi/Util.java` | Read text file | (implementation detail) |
| `src/com/brewberrypi/tools/TemperatureLoggerStart.java` | CSV temperature logger | TOOL-001 |
| `test/GPIOTest.java` | GPIO smoke test (main program) | TOOL-002 |
| `test/TemperatureSensorTest.java` | Sensor print loop (main program) | TOOL-003 |
| `test/com/brewberrypi/mash/MashEngineTest.java` | Endless simulation run of fixture recipe | 07 §7.5, AC-09x |
| `test/com/brewberrypi/hardware/raspberrypi/RaspberryPiHardswareControllerTest.java` | Endless temperature print | TOOL-003 |
| `src/webcontent/index.html` | Single page UI, CometD client wiring, button handlers, info modal | 09, 08 |
| `src/webcontent/js/angularjs/brewberrypi.angularjs-controllers.js` | AngularJS controllers, chart options, demo data, model update functions | 09 |
| `src/webcontent/js/cometd/brewberrypi.cometd-channelnames.js` | Channel names (client copy) | 08 |
| `src/webcontent/js/cometd/brewberrypi.cometd.js` | Empty file | – |
| `src/webcontent/css/template.css` | `body { padding-top: 50px }` | 09 |
| `brewberrypi.sh`, `GPIOTest.sh`, `TemperatureSensorTest.sh` | Start scripts (`sudo java -cp …`) | NFR-023/024 |
| `com.brewberrypi.launch`, `com.brewberrypi DUMMYMODE.launch` | Eclipse launch configs (normal / `--dummymode`) | NFR-020 |
| `.project`, `.classpath`, `.settings/…` | Eclipse project, Java 1.7 compliance | legacy-technology-notes |

## Third-party content (not specified, replaced in phase 2)

| Path | Content |
|---|---|
| `lib/cometd/` | CometD 2.8.0 server, Jetty 7.6.14, Jackson 1.9.13, Spring 3.2.7 (core/beans/context/web/aop/expression — only `ClassPathResource` is used), log4j 1.2.17, slf4j 1.7.6, JUnit 4.11, Hamcrest 1.3 |
| `lib/jetty-distribution-8.1.14.v20131031/` (+ `.zip`) | Full Jetty 8 distribution (only jars are on the classpath of Eclipse; the start script uses Jetty 7 jars from `lib/cometd`) |
| `lib/pi4j/` | Pi4J 1.0.0-SNAPSHOT (core, device, gpio-extension, service) — uses WiringPi |
| `src/webcontent/js/…`, `css/…`, `fonts/…` | jQuery 1.11.0, Bootstrap 3.1.1 (+ Glyphicons), AngularJS 1.2.15, D3 3.5.6, NVD3 1.8.1-dev, angular-nvd3 1.0.3-dev, CometD JS client (jQuery binding + ack/reload/timesync/timestamp extensions), jquery.cookie |

## Other files in `Recovery/`

- `text.md` — contains only "this is a test only." (not part of the project).
