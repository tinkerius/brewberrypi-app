# BrewberryPi — Functional Specification (Index)

> **Status:** Recovered from the legacy code base `com.brewberrypi` (Java 7, ca. 2014/2015).
> **Scope of this specification:** *what* the system does. It is deliberately
> **technology-neutral**. The technology decision is made in phase 2
> (see [ADR-0001](../adr/0001-technology-stack.md) and [ADR-0002](../adr/0002-rest-and-server-sent-events.md)).
>
> **Fidelity rule:** the legacy *behaviour* is preserved. Defects found in the
> legacy code are **fixed at specification level**. Every such fix is marked
> with a **`[FIX-nn]`** tag and traced in
> [recovery/bug-and-deviation-log.md](../recovery/bug-and-deviation-log.md).
> Behaviour that is **new** compared to the legacy system is marked **`[NEW]`**.

## Documents

| # | Document | Content |
|---|----------|---------|
| 01 | [Overview](01-overview.md) | Purpose, scope, actors, system context, glossary |
| 02 | [Domain Model](02-domain-model.md) | Recipe, brew process, goal, goal state, operator request |
| 03 | [Brew Process Engine](03-brew-process-engine.md) | Process lifecycle, commands, worker loop |
| 04 | [Goal Types](04-goal-types.md) | Behaviour of every goal (heat-up, rest, mash-in, iodine test, …) |
| 05 | [Temperature Control](05-temperature-control.md) | Hysteresis controller, heat boost, duty-cycle heating, progress computation |
| 06 | [Hardware Abstraction](06-hardware-abstraction.md) | Actuators, sensor, buzzer, simulation mode, configuration, safety |
| 07 | [Recipes](07-recipes.md) | Recipe catalogue (2 shipped recipes), one file per recipe, file format |
| 08 | [Client/Server Interface](08-client-server-interface.md) | Reads and commands (request/response), pushed changes, payload schemas |
| 09 | [User Interface](09-user-interface.md) | Web UI layout and interaction |
| 10 | [Non-Functional Requirements](10-non-functional-requirements.md) | Safety, configuration, logging, startup/shutdown, deployment |
| 11 | [Acceptance Criteria](11-acceptance-criteria.md) | Testable Given/When/Then scenarios |
| 12 | [Auxiliary Tools](12-auxiliary-tools.md) | Temperature logger, GPIO smoke test, sensor test |
| 13 | [Open Questions](13-open-questions.md) | Decisions still to be confirmed by the product owner |

## Reference data (machine-readable)

| File | Content |
|------|---------|
| [data/recipes/](data/recipes/) | The 2 shipped recipes, one file `<id>.json` per recipe |
| [data/recipe.schema.json](data/recipe.schema.json) | JSON Schema of one recipe file |
| [data/config.example.json](data/config.example.json) | Default configuration (legacy wiring and constants) |
| [data/config.schema.json](data/config.schema.json) | JSON Schema of the configuration file |
| [data/messages.schema.json](data/messages.schema.json) | JSON Schema of all client/server message payloads |

## Requirement ID scheme

`<AREA>-<nnn>`, e.g. `ENG-004`. Areas:

| Prefix | Area | Document |
|--------|------|----------|
| `OV` | Overview / scope | 01 |
| `DOM` | Domain model | 02 |
| `ENG` | Brew process engine | 03 |
| `GOAL` | Goal types | 04 |
| `CTL` | Temperature control | 05 |
| `HW` | Hardware abstraction | 06 |
| `RCP` | Recipes | 07 |
| `IF` | Client/server interface | 08 |
| `UI` | User interface | 09 |
| `NFR` | Non-functional | 10 |
| `AC` | Acceptance criteria | 11 |
| `TOOL` | Auxiliary tools | 12 |

Keywords **MUST**, **MUST NOT**, **SHOULD**, **MAY** are used as in RFC 2119.
