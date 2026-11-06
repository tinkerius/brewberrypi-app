# ADR-0003: One-Way Recipe Sync from Google Drive

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Benedikt Hauptmann (product owner); proposal by Claude Code
- **Question:** `Q-34` (direction decided 2026-11-06: one way, from the cloud to the device)

## Context

The recipe editor (`tinkerius/brewberrypi-recipeeditor`, https://recipes.brewberrypi.org) is a static site
with no backend. A signed-in brewer keeps recipes in their own Google Drive, in the folder `BrewberryPi/recipes`:
- one file `<id>.json` per recipe, in the format this application reads (`RCP-007`);
- the folders found by `appProperties.brewberrypi` = `root` / `recipes`;
- Google scope `drive.file`.

Today the brewer brings a recipe to the device by downloading the file and copying it into the device's file
share (`\\<hostname>.local\config\app\recipes`). The sync makes that automatic, in one direction only: what
is in Drive appears on the device. The device never writes to Drive.

What constrains the design:
- **The application owns the recipe directory**, its format version and migration (`RCP-001`, `RCP-006`,
  `RCP-008`..`RCP-011`; runtime contract v2: `/config` is "the app's own folder"). So the sync is part of the
  application, not of BrewberryPi OS.
- **The brewer still edits the same directory by hand** through the share (`Q-29`). The sync must never
  destroy such a file.
- **A loaded process keeps its recipe** (`RCP-006`). The sync never touches the HAL, and brewing never waits
  for the network. All outputs go off at startup before anything else (spec 10 §10.1); the sync starts later
  and cannot delay that.
- **No browser on the device, no backend in the cloud.** The device must sign in to Google by itself.
- **The Pi has no real-time clock** (`NFR-005`). TLS needs a correct wall clock, so the first requests after
  boot can fail until NTP has run.
- **Spec 01 lists "remote (internet) access" as out of scope**, and the OS records the wish "local only: no
  vendor cloud". The sync is optional and off until the brewer connects. Brewing never depends on it, and the
  only cloud involved is the brewer's own Drive.

## Options considered

1. **The OS syncs into `/data/config/app/recipes`** (a systemd timer with rclone or similar). It doesn't know
   the format version or migration. It would make two writers in one folder, which OS ADR-0005 rejected. It
   wouldn't work with `--dummymode` or plain Docker. Rejected.
2. **The editor pushes to the device.** The editor runs on HTTPS. The device is plain HTTP on the LAN, so the
   browser blocks the requests (mixed content and Chrome's local-network-access rules). It would also only
   sync while the editor is open. Rejected.
3. **The app pulls from Drive, signed in with Google's OAuth device flow, into the existing recipe
   directory, with a ledger of the files it wrote.** The app knows the format, the brewer sees synced recipes
   like any other, and there is no new folder or contract change. Chosen.
4. Like 3, but into a **separate folder** (e.g. `cloud-recipes/`). That changes `RCP-001` (two
   directories, id clashes between them) and the brewer can't see in one place what the device brews from.
   The ledger in option 3 gives the same protection. Rejected.

## Decision

### Sign-in: OAuth 2.0 device flow ("TVs and Limited Input devices")

- A second OAuth client of type *TVs and Limited Input devices* in the **same Google Cloud project as the
  editor**. Scopes: `drive.file`, plus `openid email`, so the UI can show which account is connected. All
  three are non-sensitive, so Google's verification is not repeated.
- The client ID and the device-flow "secret" are built into the application. Google does not treat a
  device-flow secret as confidential. They can be overridden in `config.json` for anyone who builds their
  own.
- **Connect** in the app's UI asks Google for a device code. The UI then shows the code, the address
  `https://www.google.com/device` and a QR code. The brewer enters the code on a phone or PC and allows
  access. The app polls Google's token endpoint at the interval Google sets and stores the **refresh token**.
- **Disconnect** revokes the token at Google and deletes it on the device. Synced recipe files stay; they
  become ordinary local files (their ledger entries are removed).

### What the device sees (`drive.file`)

- The app finds the folder `recipes` by `appProperties brewberrypi = recipes` under the folder with
  `brewberrypi = root`, the same queries as the editor's `DriveStore.init()`. If the folder doesn't exist,
  the status says so. The app **never creates** folders or files in Drive.
- It reads the `*.json` files directly in that folder, and only those the project can see: files the editor
  created, or that the brewer picked in the editor with "Add files from Drive". A file uploaded straight
  through drive.google.com is not seen until it is picked in the editor. The UI and the editor's help say so.

### What a sync does

A sync runs:
- 10 s after the app has started and the brewer is connected;
- then every `sync.intervalSeconds` (default 300; timed with the monotonic clock, `NFR-005`);
- right away on **Sync now**.

It is one pass:

1. **List** the recipes folder: `files.list` with `fileId`, `name`, `version`, `trashed`; one request, paged.
2. For every Drive file whose recipe id (`<id>` of `<id>.json`, a valid id per `RCP-001`) is new, or whose
   Drive `version` changed since the last sync:
   - **download** it (`alt=media`);
   - **check** it with the same reader as the recipe directory (`read_recipe`: schema, goal types, format
     version);
   - **migrate** it to the latest format if it is older (`RCP-010`).
   
   The file written to disk is always in the latest version, so the directory watcher never has to migrate
   it again and never rewrites it.
3. **Write** it to `recipes/<id>.json` (atomically and group-writable, `NFR-032`) **only if** one of
   these holds:
   - no file `<id>.json` exists; or
   - the ledger says the sync wrote this file and its content still has the SHA-256 the ledger recorded.
     The brewer has not changed it since.
4. **Remove** `recipes/<id>.json` when its Drive file is gone (deleted, trashed, or renamed to another id),
   **only if** the ledger says the sync wrote it and it is unchanged. It is moved to
   `.backup/<id>.removed.json`, not deleted.
5. The directory watcher (`RCP-006`) picks up the changes as usual: the new recipe list reaches every client,
   and a loaded process keeps its recipe.

**Skipped, and reported** in the status per recipe. The local file always stays as it is:

| Case | Status text (to be fixed in the spec) |
|---|---|
| A local file of that id exists that the sync didn't write, or the brewer changed a synced file | "kept the file on the device" |
| Drive file in a newer format version than this app reads (`RCP-009`) | "needs a newer app version" |
| Drive file invalid (syntax, schema, goal type, migration fails) | the reader's precise error (`RCP-003`) |
| File name that is not a valid recipe id | "not a valid recipe file name" |

To let Drive win again for a recipe the brewer changed or created locally, they delete the local file. The
next sync writes the Drive version.

**Ledger** `recipes/.sync/state.json`: a hidden subdirectory, so it is never read as a recipe (`RCP-001`),
and it is written atomically. It holds:
- the Drive folder id;
- and for each synced recipe: the Drive `fileId`, the Drive `version`, and the SHA-256 of the bytes the sync
  wrote.

If the ledger is lost, nothing is overwritten or removed: every existing file counts as local, and only new
ids are written.

### Errors and offline

- No network, DNS failure, TLS failure before NTP has run, Google 5xx or 429: the sync stops, the status
  shows "offline since …", and it is retried with backoff (at most `sync.intervalSeconds`). Nothing on disk
  changes.
- `invalid_grant` (token revoked by the brewer at Google, or expired): the status becomes "disconnected,
  connect again". The token file is deleted. Synced files stay.
- Sync errors are logged once per change of state, never every pass. Tokens, codes and file contents are
  never logged.
- The sync runs in its own asyncio task with timeouts on every request. An exception in it ends that pass
  only. It never touches the engine, the HAL or the control loop, and startup never waits for it.

### Credentials

The token file is `/config/.sync/google-token.json` (the refresh token, the account e-mail). It is written
atomically and group-writable like every file in `/config` (`NFR-032`).

On BrewberryPi OS, `/config` is in the brewer's file share, so anyone with the device password can read the
token. That is accepted for this version:
- the token only reaches the files of this Google Cloud project in that brewer's Drive (`drive.file`);
- the share password is the device's admin password;
- the token can be revoked at https://myaccount.google.com/permissions or with Disconnect.

A separate secrets mount outside the share would need runtime contract version 3 in both repositories. It is
a follow-up, not part of this decision.

### Configuration (`config.json`, new section `sync`)

| Key | Default | Meaning |
|---|---|---|
| `sync.enabled` | `true` | `false` hides the sync in the UI and turns it off (for devices that must stay offline) |
| `sync.intervalSeconds` | `300` | time between syncs, minimum 60 |
| `sync.google.clientId`, `sync.google.clientSecret` | built in | the OAuth client, for own builds |

### Interface (ADR-0002: REST plus SSE)

- `GET /api/sync` → `SyncStatus`:
  - `state`: one of `disconnected`, `connecting`, `idle`, `syncing`, `offline`, `error`;
  - `account`: the connected e-mail;
  - while connecting: `userCode`, `verificationUrl`, `expiresAt`;
  - `lastSync`: wall-clock time, display only;
  - `issues`: `[{recipeId, message}]`.
- `POST /api/sync/connect`, `POST /api/sync/disconnect`, `POST /api/sync/run`. Each has one reply and an
  outcome (`Q-28`). Cross-origin requests are refused like every other command (`Q-17`).
- Push event `sync` with the `SyncStatus` when it changes. It is also part of the `snapshot`.
- UI: a "Recipe sync" panel under the recipe list. It shows the state, the connected account, the time of the
  last sync, Connect / Disconnect / Sync now, the device code with its QR code, and the issues.

### Implementation sketch

- New module `src/brewberrypi/sync/`, with:
  - `google_auth.py`: device flow, refresh, revoke;
  - `drive.py`: list and download, read only;
  - `ledger.py`;
  - `syncer.py`: the pass above, a pure function of Drive listing, ledger and directory state, so it is easy
    to test.
- HTTP client: `httpx` (async; `MockTransport` gives the tests a fake Google without network access).
- Reuse `read_recipe`, `recipe_migrations`, `files.write_file` and `files.sync_directory`. Don't add a second
  validator.
- Tests use the fake Google, a temporary recipe directory and the controllable clock. There is one test per
  new acceptance criterion: new, changed, removed, local-wins, changed-locally-wins, ledger lost, newer
  version, invalid file, offline, revoked token, loaded process unchanged.

## Consequences

- Positive:
  - A recipe saved in the editor is on the device within `sync.intervalSeconds`, or at once with Sync now.
  - No new folder and no change to `RCP-001`. No runtime contract change and no OS change: the container
    already has `Network=host`.
  - Brewer edits over the share are never overwritten.
  - It works the same in `--dummymode` and in plain Docker.
- Negative / risks:
  - **Two Google facts must be checked first** (a spike before the milestone; `developers.google.com` was
    not reachable from the session that wrote this ADR):
    1. **`drive.file` is allowed in the device flow.** Google's list of allowed scopes for limited-input
       devices is believed to include `drive.file`. If it doesn't, the device can't use the device flow.
       Fallback: the brewer opens a sign-in page on a PC with a loopback redirect, which is much clumsier.
    2. **A `drive.file` grant is shared by all clients of one Cloud project.** The Picker's `setAppId`
       takes the project number, which suggests it is. If the grant is per client, the device sees none of
       the editor's files. Fallbacks: the device asks for `drive.readonly` (a restricted scope, which needs
       Google's security assessment), or the editor and the device share one client (not possible with
       different client types).
  - The refresh token sits in the file share (see Credentials).
  - Google shows the project's name on the consent screen. "BrewberryPi Recipe Editor" reads oddly on the
    device; rename the project's branding to "BrewberryPi".
  - The app talks to the internet for the first time. The privacy notes of the app and the editor must say
    what the device reads and where the token is kept.
- Follow-ups:
  - Answer `Q-34` and write the requirements into spec 07, new section 7.6, with acceptance criteria. Lift
    the out-of-scope line in spec 01 for this feature.
  - Spec 08: `SyncStatus` and the event `sync`.
  - Add `sync` to `config.schema.json`.
  - A milestone in `docs/implementation-plan.md`.
  - Recipe editor: help text "Your BrewberryPi picks up saved recipes within 5 minutes"; keep the Drive layout
    stable (already a rule there); privacy page.
  - Later, if wanted: runtime contract v3 with a secrets mount for the token.
