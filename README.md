# BrewberryPi

Raspberry Pi controller for a home-brewing mash kettle. It switches the heater and
the circulation pump, reads a DS18B20 temperature probe, works through a recipe
step by step (heat up, rest, mash in, iodine test, …) and is operated from any
browser in the local network.

## Status

| Phase | Content | Status |
|---|---|---|
| 1 — Specification | Technology-neutral functional specification, recovered from the original 2014 Java implementation | ✅ done |
| 2 — Implementation | Technology decision (ADR-0001), then implementation milestone by milestone ([plan](docs/implementation-plan.md)) | ⏳ in progress |

## Documentation

| Document | Purpose |
|---|---|
| [docs/specification/00-index.md](docs/specification/00-index.md) | Functional specification (start here) |
| [docs/specification/11-acceptance-criteria.md](docs/specification/11-acceptance-criteria.md) | Testable acceptance criteria |
| [docs/specification/13-open-questions.md](docs/specification/13-open-questions.md) | Open decisions for the product owner |
| [docs/recovery/bug-and-deviation-log.md](docs/recovery/bug-and-deviation-log.md) | Where and why the spec deviates from the original implementation |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/implementation-plan.md](docs/implementation-plan.md) | Milestones M0–M12 |
| [docs/hardware/wiring.md](docs/hardware/wiring.md) | Wiring guide and diagram (default pins) |

## Working with Claude Code

Open this folder in VS Code, install the recommended extensions when prompted,
and start Claude Code (Claude Code panel, or `claude` in the integrated terminal).
Claude Code loads [CLAUDE.md](CLAUDE.md) automatically.

Project skills (in `.claude/skills/`):

| Command | Purpose |
|---|---|
| `/choose-stack` | Prepare and record the technology decision (ADR-0001) |
| `/implement-milestone M<n>` | Implement one milestone test-first against its acceptance criteria |
| `/verify-spec [prefix]` | Check the implementation against requirements and acceptance criteria |
| `/spec-question <text>` | Record an open question instead of guessing |

## Development

Technology stack: [ADR-0001](docs/adr/0001-technology-stack.md) and
[ADR-0002](docs/adr/0002-rest-and-server-sent-events.md). In short: Python (Starlette + uvicorn, REST and
server-sent events) and a Vite/Preact front end, shipped as a signed container image. On the Raspberry Pi the image
runs under Podman on BrewberryPi OS, which is developed in the separate repository `tinkerius/brewberrypi-os`.
The interface between the two is the [runtime contract](docs/deployment/runtime-contract.md).

### Development environment

**Recommended: the devcontainer.** Open the folder in VS Code and choose **Reopen in Container**. You need Docker and the
Dev Containers extension. The container has the same OS and Python as the runtime image (Debian Trixie,
Python 3.13, `python3-libgpiod`), plus uv, Node 22, make, cosign v2, the GitHub CLI, Claude Code (CLI and VS Code
extension) and access to the host's Docker (Docker's own `docker-ce-cli`, because Moby packages do not exist
for Trixie). On first start it runs `make setup`. The devcontainer feature versions are pinned in
`.devcontainer/devcontainer-lock.json`; to update them, run `devcontainer upgrade` (Dev Containers CLI) or
*Dev Containers: Update Feature Versions* and commit the new lock file.

**Without the devcontainer** you need Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), Node.js ≥ 22,
make and Docker (or Podman: `make image CONTAINER=podman`).

### Commands

| Command | What it does |
|---|---|
| `make setup` | Install the Python (`uv sync`) and front-end (`npm ci`) dependencies from the lock files |
| `make lint` | ruff (lint + format check), mypy (strict), TypeScript type check |
| `make test` | pytest and Vitest |
| `make build` | Build the front end into the package and the Python wheel (`dist/`) |
| `make image` | Build the container image `ghcr.io/tinkerius/brewberrypi-app:<version>` for the local platform |
| `make run` | Run in simulation: `uv run brewberrypi --dummymode --config .run/config.json` (configuration and recipes are created in `.run/`) |
| `make dev` | `make run` plus the Vite dev server with live reload on http://localhost:5173; Ctrl+C stops both |
| `make format` | Auto-format and auto-fix lint findings |

In VS Code the same commands are available as tasks (*Terminal → Run Task…*, `.vscode/tasks.json`), and
*Run and Debug* (F5, `.vscode/launch.json`) starts the back end in simulation under the debugger, optionally
together with the Vite dev server.

CI (`.github/workflows/ci.yml`) runs lint, test and build on every push and pull request, and builds the image
for `linux/arm64` and `linux/amd64`. A tag `v<version>` (matching `pyproject.toml`) also pushes the image to
GHCR and signs it with cosign v2. It then verifies the image against the device's signature policy; see
[deploy/signing/README.md](deploy/signing/README.md). Finally it creates a GitHub release: a **draft** for
`v1.2.3`, published by hand once the [hardware test protocol](docs/deployment/hardware-test-protocol.md) has
passed, or a published **pre-release** with an untested-on-hardware warning for `v1.2.3-alpha.N`, `-beta.N`
or `-rc.N` (runtime contract §1).

To release, bump the version in `pyproject.toml` (e.g. `0.1.0-alpha.3`), `uv.lock` and
`frontend/package*.json`, merge, then push the tag, or run the manual workflow *Tag release*
(`.github/workflows/tag-release.yml`), which tags `main` with that version and starts CI on the tag.
To retire a version, run *Remove release* (`.github/workflows/remove-release.yml`): it deletes its
registry image and signatures, its GitHub release and its tag. Devices keep an installed copy.

### Configuration

`brewberrypi --config <file>` reads a JSON configuration file, by default `config.json` in the working
directory. If the file does not exist, the application writes it with every key at its default, so it can be
edited from there. Every key is optional. The format is
[config.schema.json](docs/specification/data/config.schema.json), and
[config.example.json](docs/specification/data/config.example.json) lists every key with its default (the legacy
wiring). Unknown keys are ignored with a warning; invalid values stop the start with exit code 2.

The heater relay runs a duty cycle at partial power with phases of at least 10 s
(`hardware.heating.minPhaseMilliseconds`, default `10000`; 10 s on + 10 s off at 50 %), so a mechanical relay
does not wear out ([FIX-26](docs/recovery/bug-and-deviation-log.md)). Earlier versions of the example
configuration set `500`. If your `config.json` still sets `"minPhaseMilliseconds": 500`, remove the key or set
it to `10000`. Otherwise the relay keeps switching about once per second.

The top bar links to the BrewberryPi OS device web interface (`ui.deviceLink`, default
`http://{host}:8081/`, where `{host}` is the host the page was opened with). Set it to `""` to hide
the link, for example when the app does not run on BrewberryPi OS.

In simulation (`--dummymode`) a simulated kettle replaces the hardware: it heats by 0.085 °C per second at
full power and cools by 0.005 °C per second (section `simulation`). `simulation.timeScale` speeds up the
whole application, e.g. `60` runs a 10-minute rest in 10 seconds. Beeps play on the computer's audio output
if `paplay`, `aplay`, `afplay` or Windows sound is available, otherwise they are only logged.

The recipes come from the directory `recipes/` next to the configuration file (`recipes.directory`), one file per
recipe: `recipes/ZORA.json` holds the recipe `ZORA`, and the file name is the recipe id. The UI lists the recipes
sorted by id. If the directory does not exist, the application creates it with the 8 legacy recipes
([docs/specification/data/recipes/](docs/specification/data/recipes/)); recipes you delete there are not written
again. A recipe file you add, change or remove shows up in every open UI within a second
(`recipes.checkMilliseconds`), without a restart; a loaded process keeps its recipe. An invalid recipe file is
skipped, and the log names the file, recipe and goal; the other recipes are still loaded. A recipe directory that
cannot be created or read stops the start, with exit code 3. Everything the application writes there is
group-writable, so the brewer can edit it as another user of the same group (on BrewberryPi OS: the share's
`app` folder). The format of one file is
[recipe.schema.json](docs/specification/data/recipe.schema.json); see [07-recipes.md](docs/specification/07-recipes.md).
The goals are in the section `mashing_process`, so that recipe design information can be added next to it later:

```json
{
  "name": "Water Boiling",
  "mashing_process": {
    "goals": [
      { "type": "HeatUpForHopsCooking" },
      { "type": "CookWater", "minutes": 20, "name": "Cooking Water", "description": "Cooking water for 20min" }
    ]
  }
}
```

Recipe files in the older format, with `goals` at the top level, are skipped, and the log says to move `goals` into
`mashing_process`. To update them, do that in each file, or delete the recipe directory so that the next start
writes the shipped recipes again (recipes of your own are then lost).

### API

The server serves the web UI, a REST API and an event stream on one port (`server.port`, default 8080); see
[08-client-server-interface.md](docs/specification/08-client-server-interface.md) and
[ADR-0002](docs/adr/0002-rest-and-server-sent-events.md).

- **REST** for reads and commands. Each command answers with a status (200, or 404, 409, 422) and
  `{"command", "success", "message"}`:

  ```sh
  curl http://brewberrypi.local:8080/api/process
  curl -X PUT http://brewberrypi.local:8080/api/process -H 'Content-Type: application/json' -d '{"recipe": "Dummy"}'
  curl -X POST http://brewberrypi.local:8080/api/process/start -H 'Content-Type: application/json' -d '{}'
  ```

  Also `GET /api/hardware`, `/api/recipes`, `/api/recipes/{id}`, `/api/chart`, `/api/ui`, `DELETE /api/process` (unload),
  `POST /api/process/pause|resume|stop`, and `POST /api/process/requests/{id}` with `{"response": "Yes"}` to
  answer a question.
- **Server-sent events** at `GET /api/events` (`curl -N`): a `snapshot` first, then `hardware`, `process`,
  `recipes` and `chart` only when they change, and a `ping` every 5 seconds.
- Commands must be sent as JSON, and requests from other web pages are refused.

### Running as a service

The container image implements the application side of the
[runtime contract](docs/deployment/runtime-contract.md): exit codes, graceful shutdown on SIGTERM/SIGINT
with all outputs off, the systemd notify protocol (`READY=1`, `STOPPING=1`, `WATCHDOG=1` every
`BREWBERRYPI_WATCHDOG_MS` while the engine and the event stream are alive) when `NOTIFY_SOCKET` is set, and
`GET /healthz`. `tests/test_contract.py` checks this against the real program.

Before the first real brew, run the [hardware test protocol](docs/deployment/hardware-test-protocol.md) on the Pi.

### Diagnostic tools

Three command-line tools for installation and diagnosis on the Pi ([12-auxiliary-tools.md](docs/specification/12-auxiliary-tools.md)).
They read the same configuration (`--config`), and accept `--dummymode` for a test run without hardware:

| Command | What it does |
|---|---|
| `brewberrypi-temperature-logger [FILE]` | Appends `yyyy/MM/dd HH:mm:ss;<°C>` (or `ERROR`) to `temperatureLog.csv` every second |
| `brewberrypi-sensor-test` | Prints the DS18B20 temperature about once per second, failures on stderr |
| `brewberrypi-gpio-test [--duty-cycle]` | Switches heating, pump and buzzer in a 2-second cycle to check the wiring; all off on Ctrl+C. `--duty-cycle` steps the heating power through 0.0 … 1.0 instead, two duty periods per step (about 15 minutes per round) |

The logger and the sensor test only read the sensor, so they can run next to the main application. The GPIO test
needs the GPIO lines, so stop the main application first.
