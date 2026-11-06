# ADR-0003: Cloud Sync, a One-Way Recipe Mirror from the Brewer's Cloud Storage

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** Benedikt Hauptmann (product owner); proposal by Claude Code
- **Question:** `Q-35`. Spec: [14 — Cloud Sync](../specification/14-cloud-sync.md). Decided by the product owner
  on 2026-11-06:
  - the feature is called **Cloud Sync**;
  - it works one way, from the cloud to the device;
  - while it is connected, the recipe directory is a mirror of the cloud, and local changes are overwritten;
  - the only provider so far is **Google Drive**, and others (OneDrive, Dropbox, …) may follow.

## Context

The recipe editor (`tinkerius/brewberrypi-recipeeditor`, https://recipes.brewberrypi.org) is a static site
with no backend. A signed-in brewer keeps recipes in their own Google Drive, in the folder `BrewberryPi/recipes`:
- one file `<id>.json` per recipe, in the format this application reads (`RCP-007`);
- the folders found by `appProperties.brewberrypi` = `root` / `recipes`;
- Google scope `drive.file`.

Before Cloud Sync, the brewer brought a recipe to the device by downloading it and copying it into the device's
file share. Cloud Sync makes that automatic, in one direction only. The device never writes to the cloud.

What constrains the design:
- **The application owns the recipe directory**, its format version and migration (`RCP-001`, `RCP-006`,
  `RCP-008`..`RCP-011`; runtime contract v2: `/config` is "the app's own folder"). So Cloud Sync is part of
  the application, not of BrewberryPi OS.
- **The brewer can also edit the directory through the share** (`Q-29`). Sharing the folder between hand
  edits and a sync gives cases that are hard to explain (option 3 below), so while Cloud Sync is connected it
  alone owns the folder.
- **A loaded process keeps its recipe** (`RCP-006`). Cloud Sync never touches the HAL, and brewing never
  waits for the network. All outputs go off at startup before anything else (spec 10 §10.1); Cloud Sync
  starts later and cannot delay that.
- **No browser on the device, no backend in the cloud.** The device must sign in to the provider by itself.
- **The Pi has no real-time clock** (`NFR-005`). TLS needs a correct wall clock, so the first requests after
  boot can fail until NTP has run. They are retried.
- **Spec 01 had "remote (internet) access" out of scope**, and the OS records the wish "local only: no vendor
  cloud". Cloud Sync is optional and off until the brewer connects. Brewing never depends on it, and the only
  cloud involved is the brewer's own storage.

## Options considered

1. **The OS syncs into `/data/config/app/recipes`** (a systemd timer with rclone or similar). It doesn't know
   the format version or migration. It would make two writers in one folder, which OS ADR-0005 rejected. It
   wouldn't work with `--dummymode` or plain Docker. Rejected.
2. **The editor pushes to the device.** The editor runs on HTTPS. The device is plain HTTP on the LAN, so the
   browser blocks the requests (mixed content and Chrome's local-network-access rules). It would also only
   sync while the editor is open. Rejected.
3. **The app pulls and merges with local files**: a ledger records the files the sync wrote, and the sync
   never overwrites or removes a file the brewer made or changed. Rejected after review, because it leaves
   cases that are hard to explain:
   - a synced file edited on the share silently stops syncing;
   - a deleted synced file comes back;
   - a local file of the same id hides the cloud recipe for good;
   - a lost ledger stops all updates;
   - changing the account leaves the old account's recipes behind.
4. **Like 3, with a separate folder owned by the sync** (`recipes/drive/`). Clear ownership, but two recipe
   folders, id clashes between them, and a change of `RCP-001`. More than this needs. Rejected.
5. **The app pulls and mirrors the cloud folder into the recipe directory.** While connected, the directory
   holds exactly the cloud recipes. Local changes are overwritten, with a copy kept in `.backup`. One rule,
   with no ledger to trust and no id clashes. **Chosen.**

## Decision

### Parts

`src/brewberrypi/cloud_sync/`:
- `provider.py`: the provider interface, `CloudProvider`. Every provider:
  - signs in with a device code (start, then poll until the brewer allowed access), and ends the sign-in;
  - lists one folder as `(file id, name, revision)`, and downloads a file.

  Nothing in it writes to the cloud. Errors are typed: `ProviderOffline` (retry), `ProviderError`,
  `AccessRevoked`, `FolderNotFound`, `SignInPending`, `SignInFailed`.
- `google_drive.py`: the Google Drive provider, the only one so far.
- `mirror.py`: the mirror rule (`SYNC-011`), a pure function `plan(local files, wanted recipes, last written)`
  that returns the actions, and `apply()` that carries them out.
- `service.py`: `CloudSync`. It holds the sign-in flow, the sync loop and its timing, the status (`IF-090`),
  the commands (`SYNC-030`), the credentials file and the notice file.

The status carries the provider's name (`provider`, e.g. `Google Drive`); the UI and every message use it. A
second provider adds one module and a choice in the configuration and the UI. The mirror, the timing, the
status and the interface stay as they are.

### Sign-in with a device code

The device shows a code and an address; the brewer enters the code on a phone or PC. Google and Microsoft
(OneDrive) offer this flow (OAuth 2.0 device authorization grant). Dropbox, as far as is known today, does
not: it shows a code after the brewer allows access, which the brewer then types into the device. A Dropbox
provider would need a second variant of the sign-in step (`signIn` with a field to type the code into). That
is left to when it is added.

**Google Drive:**
- An OAuth client of type *TVs and Limited Input devices* in the **same Google Cloud project as the editor**.
- Scopes: `drive.file`, plus `openid email` for the account shown in the UI. All three are non-sensitive.
- Google does not treat the client secret of such a client as confidential, so it is built into the
  application. It is not kept in git, though: the release build writes it into the package from the
  repository's Actions settings (`GOOGLE_DRIVE_CLIENT_ID`, `GOOGLE_DRIVE_CLIENT_SECRET`; amended 2026-11-06,
  see [deployment/cloud-sync-google.md](../deployment/cloud-sync-google.md)). `cloudSync.googleDrive.clientId`
  and `clientSecret` in `config.json` replace it. A build without a client has Cloud Sync unavailable
  (`SYNC-001`).
- Google lets the brewer untick single scopes. A sign-in without `drive.file` is refused with
  `Access to Google Drive files was not allowed. Connect again and allow it.`

### What the device sees

- The Google Drive provider finds the folder `recipes` by `appProperties brewberrypi = recipes` under the
  folder with `brewberrypi = root`, the same queries as the editor's `DriveStore.init()`. It keeps the
  folder's id. When a listing comes back empty, it looks the folder up again before trusting "empty".
- With `drive.file`, Google lists only files of this Cloud project: files the editor created, or that the
  brewer picked in the editor with "Add files from Drive". A file uploaded straight through drive.google.com
  is not seen until it is picked in the editor.

### The mirror rule

As spec 14 `SYNC-011`..`SYNC-016`:
- **Contents are compared as bytes.** A recipe is only written when its file differs.
- **Changes on the device are detected from the files themselves**, never from a record. The service
  remembers in memory what it last wrote for each recipe. That only decides whether a differing file gets a
  `.local` copy, meaning it was changed on the device. After a restart that memory is empty, so a recipe
  changed in the cloud while the app was down gets a needless `.local` copy, which is harmless.
- **Downloads are cached in memory** by file id and revision, so a sync without changes is one listing
  request. A restart downloads everything once.
- **Cloud files in an older format version** are migrated before they are written (`SYNC-012`), so the
  recipe directory never migrates or rewrites them. Files the reader rejects are not written and are listed
  in `issues` with the reader's own message (`SYNC-013`).
- **Nothing changes unless the listing and every download succeeded** (`SYNC-014`).
- **Writes run without an `await` in between**, so a disconnect cannot cut a sync in half. A sync that
  finishes after a disconnect writes nothing.

### Timing

As spec 14 `SYNC-020`, `SYNC-021`:
- A sync runs at startup, after Connect, every `cloudSync.intervalSeconds` (default 30), and at once on
  Sync now. Times use the event clock, which is real time also in simulation.
- After failures, the wait doubles up to 300 s.
- The device has to poll: Drive's change notifications go to a public HTTPS address, and the device is on the
  LAN. A sync without changes is one small `files.list` request: about 3,000 a day, well inside Drive's free
  quota.

### Credentials

`.cloud-sync/credentials.json` in the configuration file's directory holds the provider, the refresh token and
the account. It is written atomically and group-writable like every file there (`NFR-032`). The access token
stays in memory.

On BrewberryPi OS that directory is in the brewer's file share, so anyone with the device password can read the
token. The product owner accepted this (`Q-35`):
- the token only reaches the files of this Google Cloud project in that brewer's Drive (`drive.file`);
- the share password is the device's admin password;
- the token can be revoked at https://myaccount.google.com/permissions or with Disconnect.

A secrets mount outside the share would need runtime contract version 3 in both repositories.

### Interface (ADR-0002: REST plus SSE)

- `GET /api/cloud-sync` returns `CloudSyncStatus` (`IF-090`). It is pushed as the event `cloudSync` and is
  part of the snapshot.
- `POST /api/cloud-sync/connect`, `/disconnect` and `/sync` run the commands `cloudSyncConnect`,
  `cloudSyncDisconnect` and `cloudSyncNow` (`IF-091`), with replies as in `SYNC-030`. Cross-origin requests are
  refused like every other command (`Q-17`).
- The sign-in code reaches the UI through the status, not the reply. Connect answers at once, and the code
  follows when the provider sent it.
- HTTP client: `httpx` (async, timeouts). Its `MockTransport` gives the provider tests a fake Google without
  network access.

## Consequences

- Positive:
  - One rule the brewer can rely on: while Cloud Sync is on, the device brews exactly what is in the cloud
    folder.
  - Nothing is lost: every file Cloud Sync replaces or removes has a copy in `.backup`.
  - A recipe saved in the editor is on the device within `cloudSync.intervalSeconds`, or at once with Sync
    now.
  - No new recipe folder. No runtime contract change and no OS change: the container already has
    `Network=host`, so outbound HTTPS works.
  - It works the same in `--dummymode` and in plain Docker.
- Negative / risks:
  - Hand edits over the share are overwritten while Cloud Sync is on. The connect dialog and the notice file
    `DO-NOT-EDIT-Cloud-Sync.txt` say so.
  - `brewberry-config import` on the OS also writes recipe files. While Cloud Sync is on, it is overwritten
    the same way; the OS user manual should say so.
  - **Two Google facts must be checked on the real client** (`developers.google.com` was not reachable when
    this was written):
    1. **`drive.file` is allowed in the device flow.** Google's list of allowed scopes for limited-input
       devices is believed to include it. If it doesn't, the device can't use the device flow.
    2. **A `drive.file` grant is shared by all clients of one Cloud project.** The Picker's `setAppId` takes
       the project number, which suggests it is. If the grant is per client, the device sees none of the
       editor's files: the device then gets "folder not found".
  - The refresh token sits in the file share (see Credentials).
  - Google shows the project's name on the consent screen. "BrewberryPi Recipe Editor" reads oddly on the
    device; rename the project's branding to "BrewberryPi".
  - The app talks to the internet for the first time. The privacy notes of the editor should say that the
    device reads the Drive folder.
- Follow-ups:
  - Check the two facts above with the real client
    ([deployment/cloud-sync-google.md](../deployment/cloud-sync-google.md)).
  - Recipe editor:
    - help text "Your BrewberryPi picks up saved recipes within 30 seconds (Cloud Sync)";
    - keep the Drive layout stable (already a rule there);
    - privacy page.
  - OS user manual: recipes in the share are overwritten while Cloud Sync is on.
  - Later providers: OneDrive (device code, app folder), Dropbox (code typed into the device, app folder).
  - Later, if wanted: runtime contract v3 with a secrets mount for the token.
