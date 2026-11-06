# Hardware Test Protocol

Confirms on a real Raspberry Pi what the automated tests can only simulate: the GPIO HAL (M9), crash safety
under BrewberryPi OS (M10), the diagnostic tools (M11) and one water-only brew (M12). Run it once per
hardware setup and before the first real brew. Fill in the *Result* column (✅ / ❌ + note) and sign at the end.

| | |
|---|---|
| Application version (`/healthz`) | |
| BrewberryPi OS version | |
| Raspberry Pi model | |
| Relay board / sensor | |
| Tester, date | |

## Safety

- **Water only.** Never leave the kettle unattended while the heater can switch on.
- The heater runs on mains power: the wiring must be done and checked by a qualified person, with a residual-current device (RCD) in the circuit.
- Keep a way to cut the heater's power at hand during every test (for example a switched outlet).
- If a relay switches on when it should not: cut the power first, then write down what happened.

## Setup

- BrewberryPi OS installed with the application image, service `brewberrypi` running
  (`sudo systemctl status brewberrypi`).
- Wiring as in `HW-011` ([06-hardware-abstraction.md](../specification/06-hardware-abstraction.md),
  diagram in the [wiring guide](../hardware/wiring.md)) or as
  configured in `/config/config.json`:

| Function | BCM | Header pin | Active level |
|---|---|---|---|
| Heating relay | 23 | 16 | low |
| Pump relay | 22 | 15 | low |
| Buzzer | 10 | 19 | high |
| DS18B20 data (1-Wire) | 4 | 7 | – |

- A reference thermometer, a stopwatch (or phone), and a phone or laptop with a browser in the same network.
- Diagnostic tools run inside the application container, for example
  `sudo podman exec -it <container> brewberrypi-sensor-test --config /config/config.json`.
  The container name comes from the Quadlet unit in `brewberrypi-os`.

## Tests

| # | Checks | Procedure | Expected | Result |
|---|---|---|---|---|
| H-01 | `AC-083`, `HW-020`, `NFR-002` | Power the Pi off and on, watching the relay LEDs. Then `sudo systemctl restart brewberrypi` and open the UI at `http://<pi>:8080`. | Heater and pump relays stay off during the restart. When the UI first loads, they are already off. Note any click during the first seconds after power-on (known boot window, `Q-18`). | |
| H-02 | `AC-020`, `AC-023`, `TOOL-003` | Run `brewberrypi-sensor-test` with the probe in water next to the reference thermometer, at room temperature and in warm water. | One line per second, `yyyy/MM/dd HH:mm:ss;<°C>`. The sensor is found automatically. Values within ±0.5 °C of the reference. | |
| H-03 | `AC-021`, `AC-022`, `HW-033`, `AC-005` | With a process heating (H-11 running), disconnect the sensor's data wire for about 10 s, then reconnect it. | Within about 5 s the UI shows `Sensor error`, the heater switches **off**, and the error beep repeats. After reconnecting, readings and temperature control resume. | |
| H-04 | `TOOL-002` | `sudo systemctl stop brewberrypi`, then run `brewberrypi-gpio-test` and watch one full cycle. Stop it with Ctrl+C. | Every 2 s: heater on, heater off, pump on, pump off, a short buzzer pulse, each printed with its BCM pin. After Ctrl+C: `all outputs off`, and all relays are off. | |
| H-05 | `AC-010`, `AC-011`, `CTL-042` | Run `brewberrypi-gpio-test --duty-cycle` (service still stopped). Time the heater relay for each power step. One round takes about 15 minutes; each step prints its power and length. | 0.1: on 10 s / off 90 s. 0.3: on 10 s / off about 23 s. 0.5: 10 s / 10 s. 0.7: about 23 s / 10 s. 0.9: 90 s / 10 s. No phase is shorter than 10 s. Each step shows two full, regular cycles. | |
| H-06 | `AC-012`, `CTL-044` | Same run as H-05: watch the switch from 0.9 to 1.0, and from 1.0 to 0.0 at the start of the next cycle. | At 1.0 the relay switches on at once, also in the middle of a 0.9 off-phase, and **stays on** for the whole 20 s step. At 0.0 it switches off at once, also in the middle of an on-phase, and stays off. | |
| H-07 | `TOOL-001` | `brewberrypi-temperature-logger /tmp/t.csv` for about 2 minutes, then Ctrl+C, then look at the file. | One line per second with a 24-hour timestamp and one decimal (`ERROR` lines only while the sensor is disconnected). | |
| H-08 | `NFR-022`, contract §2 | Start the service again, start the WaterBoil recipe and wait until the heater is on. Then `sudo systemctl stop brewberrypi`. | The relays switch off at once. The journal shows `Shutting down BrewberryPi.` and a clean exit (status 0). | |
| H-09 | `NFR-003`, contract §6 | Start the service and the WaterBoil recipe again, heater on. Kill the application hard: `sudo podman kill -s KILL <container>`. | The relays are off within a few seconds (host pin reset). The service restarts. | |
| H-10 | contract §4 (watchdog) | Heater on again. Freeze the application: `sudo podman pause <container>`. Wait longer than the configured `WatchdogSec`. | systemd detects the hang and restarts the service. The relays are off at the latest after the restart. Note the time until they are off. | |
| H-11 | `AC-090`, `AC-050`, `AC-053`, `AC-055`, `AC-036` | **Water-only brew:** copy the test recipe `tests/recipes/DEMO.json` of this repository into the recipe folder (on BrewberryPi OS: the share's `app/recipes/`), fill the kettle with water, load **Demo Process** in the UI and start. Answer `Yes` for the iodine test and `Confirm` for lautering. Pause once during the rest for about 1 minute, then resume. | Heat-up to 62 °C, a 10-minute hold at about 62 °C (heater cycling, pump on), the iodine question with a long beep every 10 s, heat-up to 78 °C, heater and pump off during lautering, heat-up to 97 °C, then `Finished` with everything off. During the pause, heater and pump are off and the countdown stands still. The chart shows the whole curve. | |
| H-12 | `AC-077`, `UI-060` | With the UI open on a phone, `sudo systemctl restart brewberrypi`. | The UI shows `disconnected — reconnecting…`, then reconnects and shows live data without a page reload. | |

## Result and sign-off

| | |
|---|---|
| All tests passed (or deviations accepted, see notes) | yes / no |
| Notes | |
| Signed (name, date) | |

After sign-off, tick the hardware criteria of M9–M12 in [implementation-plan.md](../implementation-plan.md)
and close the milestone issues.
