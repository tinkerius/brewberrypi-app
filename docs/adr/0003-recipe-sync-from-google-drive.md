# ADR-0003: One-Way Recipe Sync from Google Drive

- **Status:** Proposed
- **Date:** 2026-11-06
- **Deciders:** Benedikt Hauptmann (product owner); proposal by Claude Code
- **Question:** `Q-34`. Decided by the product owner on 2026-11-06:
  - one way, from the cloud to the device;
  - while the sync is connected, the recipe directory is a mirror of the cloud, and local changes are
    overwritten.

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
- **The brewer can also edit the directory through the share** (`Q-29`). Sharing the folder between
  hand edits and the sync gives cases that are hard to explain (see Options), so while the sync is connected
  the sync alone owns the folder.
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
3. **The app pulls from Drive and merges with local files**: a ledger records the files the sync wrote, and
   the sync never overwrites or removes a file the brewer made or changed. Rejected after review, because it
   leaves cases that are hard to explain:
   - a synced file edited on the share silently stops syncing;
   - a deleted synced file comes back;
   - a local file of the same id hides the Drive recipe for good;
   - a lost ledger stops all updates;
   - changing the Google account leaves the old account's recipes behind.
4. **Like 3, with a separate folder owned by the sync** (`recipes/drive/`). Clear ownership, but two recipe
   folders, id clashes between them, and a change of `RCP-001`. More than this needs. Rejected.
5. **The app pulls from Drive and mirrors it into the recipe directory.** While connected, the directory holds
   exactly the Drive recipes. Local changes are overwritten, with a copy kept in `.backup`. One simple rule,
   with no ledger to trust and no id clashes. Chosen.

## Decision

### Sign-in: OAuth 2.0 device flow ("TVs and Limited Input devices")

- A second OAuth client of type *TVs and Limited Input devices* in the **same Google Cloud project as the
  editor**. Scopes: `drive.file`, plus `openid email`, so the UI can show which account is connected. All
  three are non-sensitive, so Google's verification is not repeated.
- The client ID and the device-flow "secret" are built into the application. Google does not treat a
  device-flow secret as confidential. They can be overridden in `config.json` for anyone who builds their
  own.
- **Connect** in the app's UI first warns:

  > The recipes on this device will be replaced by the recipes in your Google Drive. Recipes that are only
  > on this device are moved to `recipes/.backup`. Changes made on the device are overwritten while the sync
  > is on.

  Then it asks Google for a device code. The UI shows the code, the address `https://www.google.com/device`
  and a QR code. The brewer enters the code on a phone or PC and allows access. The app polls Google's token
  endpoint at the interval Google sets and stores the **refresh token**.
- **Disconnect** revokes the token at Google and deletes it on the device. The recipe files stay as they
  are and become ordinary local files again. The notice file (below) is removed.

### What the device sees (`drive.file`)

- The app finds the folder `recipes` by `appProperties brewberrypi = recipes` under the folder with
  `brewberrypi = root`, the same queries as the editor's `DriveStore.init()`. The app **never creates**
  folders or files in Drive.
- It reads the `*.json` files directly in that folder, and only those the project can see: files the editor
  created, or that the brewer picked in the editor with "Add files from Drive". A file uploaded straight
  through drive.google.com is not seen until it is picked in the editor. The UI and the editor's help say so.

### The mirror rule

While the sync is connected, the recipe files (`*.json`) in the recipe directory are exactly the recipes of
the Drive folder that this application can read, each in the latest format version. In detail:

| On the device | After the next sync |
|---|---|
| A Drive recipe is missing (new in Drive, or deleted on the device) | written |
| A Drive recipe differs (changed in Drive, or changed on the device) | overwritten; a changed device file is first copied to `.backup/<id>.local.json` |
| A recipe file that is not in Drive (deleted, trashed or renamed there, or added on the device) | moved to `.backup/<id>.removed.json` |
| Other files (not `*.json`), hidden files, subdirectories | left alone (`RCP-001` ignores them anyway) |

Drive files that this application cannot read are **not on the device**, and the status lists them:

| Case | Status text (to be fixed in the spec) |
|---|---|
| Format version newer than this application reads (`RCP-009`) | "needs a newer app version" |
| Invalid (syntax, schema, goal type, migration fails) | the reader's precise error (`RCP-003`) |
| File name that is not a valid recipe id (`RCP-001`) | "not a valid recipe file name" |

The editor validates before every save, so the last two only happen to files changed outside the editor.

**Notice file.** While the sync is connected, the recipe directory holds `DO-NOT-EDIT-synced-from-Google-Drive.txt`.
It is a `.txt` file, so it is never read as a recipe (`RCP-001`). It is written again if deleted and removed
on Disconnect. Its text:

> The recipes in this folder are a copy of the folder BrewberryPi/recipes in the Google Drive of
> <account>. Change them at https://recipes.brewberrypi.org. Changes made here are overwritten within
> 30 seconds; the files they replace are kept in .backup. To edit recipes here instead, disconnect the
> recipe sync in the BrewberryPi app.

`.backup` already holds the originals of migrated files (`RCP-010`). The sync's copies use their own name
suffixes, `.local` and `.removed`, and replace an older copy of the same name.

### When a sync runs, and what one pass does

A sync runs:
- as soon as the app has finished starting (outputs off, server listening), if the brewer is connected, and
  right after **Connect** succeeds;
- then every `sync.intervalSeconds` (default 30; timed with the monotonic clock, `NFR-005`);
- right away on **Sync now**.

The device has to poll: Drive's change notifications are sent to a public HTTPS address, and the device is
on the LAN. A pass where nothing changed is one small `files.list` request, so 30 s costs little: about
3,000 requests a day, well inside Drive's free quota, and a few MB of traffic a month.

One pass:
1. **List** the Drive folder: `files.list` with `fileId`, `name` and `version`; one request, paged.
2. **Download** each file whose Drive `version` differs from the cache (`alt=media`). **Check** it with the
   directory's own reader (`read_recipe`) and **migrate** it to the latest format (`RCP-010`). The text to
   write is cached in `recipes/.sync/` (hidden, so never read as a recipe) together with the Drive `version`.
   The written file is in the latest version, so the watcher never migrates or rewrites it.
3. **Compare** the directory with the cached texts, byte for byte, and apply the mirror rule. Every write is
   atomic and group-writable (`NFR-032`).
4. The directory watcher (`RCP-006`) picks up the changes as usual: the new recipe list reaches every client,
   and a loaded process keeps its recipe.

The cache is only an optimisation. If it is lost, the next pass downloads every recipe again and nothing
else changes. A local change is found by comparing the files themselves, not by trusting a record.

**Safety net against emptying the device.** If the Drive folder is not found, cannot be listed, or Google
answers with an error, the pass stops and the directory is left as it is. Only a successful, complete
listing changes files. A Drive folder that is really empty does empty the device. That is the mirror rule,
and the files are in `.backup`.

### Errors and offline

- No network, DNS failure, TLS failure before NTP has run, Google 5xx or 429: the pass stops, the status
  shows "offline since …", and it is retried with backoff. The wait doubles after each failure, up to
  5 minutes, and goes back to `sync.intervalSeconds` after a pass that succeeds. Nothing on disk changes, so
  the device brews with the recipes of the last successful pass.
- `invalid_grant` (token revoked by the brewer at Google, or expired): the status becomes "disconnected,
  connect again". This counts as a Disconnect: the token is deleted, the recipe files stay, and the notice
  file is removed.
- Sync errors are logged once per change of state, never on every pass. Tokens, codes and file contents are
  never logged.
- The sync runs in its own asyncio task with timeouts on every request. An exception in it ends that pass
  only. It never touches the engine, the HAL or the control loop, and startup never waits for it.

### Credentials

The token file is `/config/.sync/google-token.json` (the refresh token and the account e-mail). It is
written atomically and group-writable like every file in `/config` (`NFR-032`).

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
| `sync.intervalSeconds` | `30` | time between syncs, minimum 10 |
| `sync.google.clientId`, `sync.google.clientSecret` | built in | the OAuth client, for own builds |

### Interface (ADR-0002: REST plus SSE)

- `GET /api/sync` returns a `SyncStatus`:
  - `state`: one of `disconnected`, `connecting`, `idle`, `syncing`, `offline`;
  - `account`: the connected e-mail address;
  - while connecting: `userCode`, `verificationUrl` and `expiresAt`;
  - `lastSync`: wall-clock time, display only;
  - `issues`: `[{recipeId, message}]` for Drive files not on the device.
- `POST /api/sync/connect`, `POST /api/sync/disconnect` and `POST /api/sync/run`. Each has one reply and an
  outcome (`Q-28`). Cross-origin requests are refused like every other command (`Q-17`).
- Push event `sync` with the `SyncStatus` when it changes. It is also part of the `snapshot`.
- UI: a "Recipe sync" panel under the recipe list. It shows:
  - the state, the connected account and the time of the last sync;
  - Connect (with the warning above), Disconnect and Sync now;
  - the device code with its QR code;
  - the issues.
  
  While connected, the recipe list says "from Google Drive".

### Implementation sketch

- New module `src/brewberrypi/sync/`:
  - `google_auth.py`: device flow, refresh, revoke;
  - `drive.py`: list and download, read only;
  - `mirror.py`: the mirror rule, a pure function from (Drive listing, cached texts, directory contents) to
    a list of actions, so it is easy to test;
  - `syncer.py`: the task, its timing and the status.
- HTTP client: `httpx` (async; `MockTransport` gives the tests a fake Google without network access).
- Reuse `read_recipe`, `recipe_migrations`, `files.write_file` and `files.sync_directory`. Don't add a second
  validator.
- Tests use the fake Google, a temporary recipe directory and the controllable clock. There is one test per
  new acceptance criterion. Cases to cover:
  - Drive changes: new, changed, removed, renamed;
  - device changes: file changed, deleted, added;
  - the first connect with local recipes;
  - the cache lost;
  - Drive files: newer version, invalid, bad file name;
  - Drive folder missing, listing fails, offline;
  - token revoked;
  - Disconnect leaves the files;
  - the notice file;
  - a loaded process is unchanged.

## Consequences

- Positive:
  - One rule the brewer can rely on: while the sync is on, the device brews exactly what is in Drive.
  - Nothing is lost: every file the sync replaces or removes has a copy in `.backup`.
  - A recipe saved in the editor is on the device within `sync.intervalSeconds`, or at once with Sync now.
  - No new folder. No runtime contract change and no OS change: the container already has `Network=host`.
  - It works the same in `--dummymode` and in plain Docker.
- Negative / risks:
  - Hand edits over the share are overwritten while the sync is on. That is the point, but it surprises
    anyone who skips the warning and the notice file.
  - `brewberry-config import` on the OS also writes recipe files. While the sync is on, it is overwritten
    the same way; the OS user manual should say so.
  - A recipe that only lives on the device has to be uploaded in the editor before connecting, or it ends
    up in `.backup`.
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
  - Answer `Q-34` and write the requirements into spec 07, new section 7.6, with acceptance criteria:
    - `RCP-001`: a file deleted by the brewer is not written again, except while the sync is connected;
    - `RCP-004`: unchanged;
    - lift the out-of-scope line in spec 01 for this feature.
  - Spec 08: `SyncStatus` and the event `sync`.
  - Add `sync` to `config.schema.json`.
  - A milestone in `docs/implementation-plan.md`.
  - Recipe editor:
    - help text "Your BrewberryPi picks up saved recipes within 30 seconds";
    - keep the Drive layout stable (already a rule there);
    - privacy page.
  - OS user manual: recipes in the share are read-only in practice while the sync is on.
  - Later, if wanted: runtime contract v3 with a secrets mount for the token.
