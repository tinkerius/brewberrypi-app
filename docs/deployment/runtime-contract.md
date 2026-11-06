# Runtime Contract: App Image ↔ BrewberryPi OS

**Contract version 2** (draft, 2026-10-23; changes in §7). This is what the BrewberryPi container image (this repository)
guarantees and what it expects from the host. The host is BrewberryPi OS, in the separate repository
`tinkerius/brewberrypi-os`. Both sides must implement this version. A change needs a new contract
version and updates in both repositories. The image carries the version as the label
`org.brewberrypi.runtime-contract`.

Technology choices: [ADR-0001](../adr/0001-technology-stack.md). Parts marked *pending* depend on
open specification questions in [13-open-questions.md](../specification/13-open-questions.md).

## 1. Image

| Item | Value |
|---|---|
| Name | `ghcr.io/tinkerius/brewberrypi-app` (private registry) |
| Release tags | Semantic versions `MAJOR.MINOR.PATCH`, or pre-releases `MAJOR.MINOR.PATCH-alpha.N`, `-beta.N`, `-rc.N`, ordered as in SemVer 2.0 (a pre-release sorts before its release: `0.1.0-alpha.2` < `0.1.0`). The tag equals the label `org.opencontainers.image.version` and the `version` of `/healthz`. Devices never use `latest`. |
| Platforms | `linux/arm64` (device), `linux/amd64` (simulation on developer machines) |
| Signature | cosign key-pair signature in the legacy tag format `sha256-<digest>.sig` (cosign v2.x); public key pinned in the host's `policy.json` |
| Labels | `org.opencontainers.image.version`, `org.brewberrypi.runtime-contract=2` |

## 2. Process

- **Command:** `brewberrypi --config /config/config.json` (`NFR-020`), plus `--dummymode` for simulation.
- **User:** runs as a non-root UID inside the container (`NFR-024`). The host adds its numeric `gpio` group ID (`GroupAdd=`).
- **Root filesystem:** the image works with a read-only container root filesystem. It needs only `/tmp` as tmpfs.
- **Signals:** on `SIGTERM` or `SIGINT`, the image switches outputs off, releases GPIO, sends `STOPPING=1` and exits 0 within 5 s (`NFR-022`, `HW-021`). Outputs-off runs in the web framework's shutdown hook.
- **Exit codes:**

| Code | Meaning | Host reaction |
|---|---|---|
| 0 | Clean shutdown | none |
| 1 | Unexpected error | restart; counts towards safe mode |
| 2 | Invalid configuration, or a missing `config.json` that cannot be written (`NFR-020`, `NFR-021` step 1) | do **not** roll back the image; report a configuration error |
| 3 | Recipe directory cannot be created or read (`RCP-001`). An invalid recipe file is only logged and skipped (`RCP-003`). | same as 2 |
| 4 | Hardware initialisation failed | restart; counts towards safe mode |

## 3. Mounts, devices, network

| Item | Requirement |
|---|---|
| `/config` (read-write) | The app's own folder; on BrewberryPi OS `/data/config/app`, which the brewer also edits over the network (`Q-29`). It holds `config.json` and the recipe directory `recipes/` (one file `<id>.json` per recipe, `RCP-001`). Relative paths in the configuration resolve against the configuration file's directory (`Q-19`). The host creates the folder before the start and gives the image's user write access through a group: the folder belongs to that group with the set-group-ID bit (mode `2775`), and the host adds the group's numeric ID (`GroupAdd=`). The image writes a missing `config.json` with every key at its default (`NFR-020`) and creates a missing `recipes/` with the shipped recipes (`RCP-001`); recipes deleted later are not written again. Everything the image creates there is group-writable (`NFR-032`). The image reads `recipes/` again when it changes (`RCP-006`) and `config.json` only at start. The host only reads `config.json`. |
| `/sys` | The container runtime's default read-only sysfs. The image reads `/sys/bus/w1/devices` (`HW-031`). |
| GPIO devices | **All** `/dev/gpiochip*` nodes present on the host. The image finds the header chip by label, because chip numbers change between kernels. |
| Network | Listens on `server.port` (default 8080) on all interfaces. The host uses host networking or publishes the port. Outbound HTTPS is used only by Cloud Sync, when the brewer connects it (spec 14): to `oauth2.googleapis.com` and `www.googleapis.com`. Without it, Cloud Sync reports `offline` and nothing else changes. |
| `/update` (read-write) | *Pending* `Q-16`: directory for update requests and the available-versions list. Without it the update UI is disabled. |

## 4. Supervision (systemd notify protocol)

If `NOTIFY_SOCKET` is set:
- `READY=1` after startup step 6 of `NFR-021`. The HAL is initialised with outputs off, the first sensor reading has arrived or timed out (`HW-034`), and the web server is listening.
- `WATCHDOG=1` every `BREWBERRYPI_WATCHDOG_MS` milliseconds (environment variable; the container does not receive `WATCHDOG_USEC`). Pings are sent only while the engine loop and the event stream loops are alive, so a hang stops them.
- `STOPPING=1` when shutdown begins.

## 5. HTTP endpoints for the host

- `GET /healthz` → `200 {"status": "ok", "version": "<semver>", "engine": "<engine state>"}`.
- *Pending* `Q-16`: `updateAllowed` (true only in engine state `NoProcess`) and the update request flow.

## 6. Host responsibilities (implemented in `brewberrypi-os`)

- **Pin reset after every stop**, including crashes and kills (`NFR-003`):
  1. Remove the container first, so the GPIO lines are released.
  2. Drive every output from the app's `config.json` (`hardware.*.pin`, `activeLow`; the defaults of `config.example.json` while there is none yet) to its off level with `pinctrl`.
- **Kernel and firmware:**
  - `dtparam=strict_gpiod`;
  - `gpio=<relay pins>=op,dh` in `config.txt` for the boot window;
  - `dtoverlay=w1-gpio`;
  - SPI disabled, because BCM 10 is the buzzer.
- **Supervision:**
  - systemd watchdog (`WatchdogSec=`) plus the hardware watchdog (`RuntimeWatchdogSec=`);
  - a restart limit;
  - safe mode, which falls back to the previous image version after repeated failures with exit codes 1 or 4.
- **Image signatures:** only images whose signature matches the pinned key are pulled.

## 7. Changes

- **Version 2** (2026-10-23, `Q-29`): `/config` is the app's own folder and read-write, made writable through a group with `GroupAdd=`; the image creates a missing `config.json` and a missing `recipes/` (with the shipped recipes) and reads `recipes/` again when it changes. Version 1 mounted `/config` read-only, and the image used its built-in recipes while `recipes/` was missing. A host implements exactly one version: BrewberryPi OS refuses images with another `org.brewberrypi.runtime-contract` label. The image is renamed from `ghcr.io/tinkerius/brewberrypi` to `ghcr.io/tinkerius/brewberrypi-app` (2026-10-27, with the repository).
