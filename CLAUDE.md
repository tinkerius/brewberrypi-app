# BrewberryPi

Raspberry Pi controller for a home-brewing mash kettle: heater and pump relays,
a DS18B20 temperature sensor and a buzzer, driven by recipes (ordered brewing
steps) and operated through a live web UI. This repository is a rebuild; the
behaviour is fully specified in `docs/specification/`.

## Project status

- Phase 1 (done): technology-neutral functional specification.
- Phase 2 (current): technology stack decided (`docs/adr/0001-technology-stack.md`, *Accepted* 2026-10-09). Implement milestone by milestone (`docs/implementation-plan.md`), starting with `/implement-milestone M0`.
- This repository (`tinkerius/brewberrypi-app`, renamed from `brewberrypi` on 2026-10-27) is the **application** only. The application ships as a container image. The operating-system image, Podman/Quadlet unit, update service and safe mode live in the separate repository `tinkerius/brewberrypi-os`. The interface between the two is `docs/deployment/runtime-contract.md`.

## Layout

- `docs/specification/` — the specification (`00-index.md` first). Requirement IDs like `ENG-012`, acceptance criteria like `AC-033`.
- `docs/specification/data/` — normative reference data: `recipes/` (one file per recipe), `config.example.json`, JSON schemas for a recipe, config and messages.
- `src/brewberrypi/data/` — copies of the spec's JSON schemas and `recipes/`, shipped in the package; a test fails if they differ from `docs/specification/data/` (copy them when the spec data changes).
- Recipe editor: the public web editor (`tinkerius/brewberrypi-recipeeditor`, https://recipes.brewberrypi.org) copies `recipe.schema.json` and `data/recipes/` and records this repository's commit; when they change, run its `scripts/sync-upstream.sh` (a new goal type or recipe format version also needs its `src/model.ts` and `src/validate.ts`).
- `docs/recovery/bug-and-deviation-log.md` — `[FIX-nn]` entries: legacy defects the spec deliberately fixes.
- `docs/adr/` — architecture decision records (copy `0000-template.md`).
- `docs/implementation-plan.md` — milestones M0–M12 and their acceptance criteria.
- `docs/deployment/runtime-contract.md` — what the container image guarantees to and expects from BrewberryPi OS.
- Source and test folders: created in milestone M0 (Python package with `uv`, front end with Vite).

## Commands

Development happens in the devcontainer (`.devcontainer/`: Debian Trixie, Python 3.13, uv, Node 22, make, cosign v2, gh, Claude Code, host Docker).

- Setup: `make setup` (`uv sync` + `npm ci`)
- Build: `make build` (front end + Python wheel); container image: `make image`
- Test: `make test` (pytest + Vitest)
- Lint: `make lint` (ruff, mypy, tsc)
- Run in simulation: `uv run brewberrypi --dummymode` (or `make run`); with the Vite dev server: `make dev`
- Run on the Raspberry Pi: install BrewberryPi OS (`tinkerius/brewberrypi-os`); the image runs as the Quadlet service `brewberrypi` (`sudo systemctl status brewberrypi`)

## Rules

- The specification is the single source of truth. If it is ambiguous or seems wrong, do not guess: ask, or record it with `/spec-question`.
- Safety first — this controls a mains-powered heater. Heater off on every path that is not actively controlling temperature, all outputs off before anything else at startup, heater off when the temperature reading is invalid (`docs/specification/10-non-functional-requirements.md` §10.1). Never weaken this, not even in tests or simulation.
- Never re-introduce a defect listed in `docs/recovery/bug-and-deviation-log.md`.
- Every change of the recipe file format, also one that only adds something, raises its `version` and adds a migrator from the previous version (`RCP-011`, spec 07 §7.2.1, `src/brewberrypi/recipe_migrations.py`). The recipe editor (`tinkerius/brewberrypi-recipeeditor`) must follow every change of the format, the schema or the shipped recipes (its `/follow-app-format`); release the app first, then merge the editor, so the deployed editor never writes files the released app skips.
- User-visible texts (goal names, descriptions, request and reply messages) must match the specification verbatim, including legacy spellings, until question `Q-07` is decided.
- Every acceptance criterion gets an automated test named after its ID (e.g. `AC-033 start twice is rejected`). Unit tests use the mock HAL and a controllable clock — no real sleeps.
- GPIO pin numbers are BCM numbers. GPIO access goes through the own libgpiod v2 HAL (chip found by label); do not use gpiozero or lgpio (ADR-0001).
- Commit messages reference the requirement or AC IDs they implement.
- Keep the GitHub Project board [BrewberryPi](https://github.com/users/tinkerius/projects/2) in sync with the work. There is one issue per milestone; set it to *In Progress* when work starts and *Done* + closed when all its criteria pass (`.claude/skills/implement-milestone/board.py`). Pull requests reference the milestone issue (`Refs #n` / `Closes #n`).
