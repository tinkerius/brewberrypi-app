# Implementation Plan (phase 2)

Technology-neutral milestone plan. Each milestone ends with its acceptance
criteria green. Tick the boxes as milestones are completed.

| # | Milestone | Spec | Done when | Status |
|---|---|---|---|---|
| M0 | **Technology decision** — fill in ADR-0001, set up the project skeleton, build, test runner, linter, Containerfile, CI (test, build multi-arch image, sign with cosign v2, push to GHCR, verify the signature against the device policy) | ADR-0001, NFR-063 | ADR accepted ✅ (2026-10-09); project skeleton (source + test folders per the chosen stack's conventions) builds and runs an empty test suite ✅; `make image` builds ✅; first release `v0.1.0` pushed, signed and verified ✅ (2026-10-11); CLAUDE.md commands updated ✅ | ☑ |
| M1 | **Configuration & recipe loading** — config file + schema validation + defaults; recipe file + validation | NFR-020, NFR-030, RCP-001..005, 07 | AC-080, AC-081, AC-082 | ☑ |
| M2 | **HAL interface + mock HAL + controllable clock** | HW-001..003, NFR-060 | Unit tests can drive time and temperature | ☑ |
| M3 | **Temperature controller & progress** | 05 | AC-001..007: controller parts ✅ (goal, interface and UI parts in M4, M7, M8) | ☑ |
| M4 | **Goals & operator requests** (incl. reminder loop) | 02, 04, DOM-033 | AC-050..057: goal and reminder parts ✅; goal-level parts of AC-004..007 ✅ (engine parts in M5) | ☑ |
| M5 | **Engine lifecycle & execution loop** | 03 | AC-030..039 ✅; engine parts of AC-050..057 (command replies, short beep after a goal, `Finished`, reminders stop on stop/pause/unload) ✅ | ☑ |
| M6 | **Simulation HAL** (physics, beeps, time scale) + `--dummymode` | HW-060..064 | AC-090 (accelerated) ✅; the chart itself in M7/M8 | ☑ |
| M7 | **Client/server interface** (REST reads and commands, server-sent events, chart thinning; reworked for `Q-28`, ADR-0002) | 08 | AC-070..074 ✅, AC-058, AC-060, AC-078, AC-079 ✅; AC-005 `hardware.temperature` is `null` ✅ | ☑ |
| M8 | **Web UI** | 09 | AC-075..077 ✅, AC-092 ✅ (automated), AC-005 UI shows `Sensor error` ✅, manual UI check against 09 §9.1 ✅ (product owner, 2026-10-11) | ☑ |
| M9 | **Raspberry Pi HAL** (GPIO, duty cycle, DS18B20, buzzer, safety) | 06, CTL-040..046 | AC-010..012, AC-020..023, AC-117 ✅ in tests with simulated GPIO, sensor and clock; AC-010..012, AC-020..023, AC-117, AC-083 on real hardware (open) | ☐ |
| M10 | **Startup/shutdown, logging, runtime contract (application side)** — startup order, signals, exit codes, `READY`/`WATCHDOG`/`STOPPING` notifications, `/healthz`. The service unit, pin reset and installation are delivered by `tinkerius/brewberrypi-os` | NFR-001..005, NFR-020..024, NFR-040, [runtime contract](deployment/runtime-contract.md) | Contract tests green ✅; on BrewberryPi OS: crash-safe outputs verified (`kill -9` and hang while heating → relays off) (open, needs `brewberrypi-os` and hardware) | ☐ |
| M11 | **Auxiliary tools** | 12 | TOOL-001..003 implemented and tested in simulation ✅; run on the Pi (open) | ☐ |
| M12 | **End-to-end** — full recipe in simulation, one real water-only brew on hardware | 11 §11.8 | AC-090 ✅, AC-091 ✅ (simulation); [hardware test protocol](deployment/hardware-test-protocol.md) signed off (open) | ☐ |
| M13 | **Cloud Sync** — one-way mirror of the recipes from Google Drive (device-code sign-in, mirror rule, status, commands, Cloud Sync control in the top bar; ADR-0003) | 14, IF-090, IF-091, UI-080..085 | AC-102..AC-116 ✅ with a fake provider and a fake Google; the real Google client and a check on hardware ([deployment/cloud-sync-google.md](deployment/cloud-sync-google.md)) open | ☐ |

Suggested order is top to bottom; M6 can run in parallel to M7/M8, M9 needs hardware.

## Working rules

- Every commit / PR references requirement IDs (e.g. `ENG-012, AC-033`).
- Tests are named after acceptance criteria where possible (`AC-033 start twice is rejected`).
- Spec changes go through `docs/specification/13-open-questions.md` → product-owner answer → spec edit → then code.
