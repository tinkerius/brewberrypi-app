# 14 — Cloud Sync `[NEW]`

Cloud Sync keeps the recipe directory (`RCP-001`) a copy of a folder in the brewer's cloud storage, where the
recipe editor (https://recipes.brewberrypi.org) saves recipes (`Q-35`). It works **one way**: from the cloud to
the device. The device never writes to the cloud. The only provider so far is **Google Drive**. The technical
design, including how a provider signs in, is in [ADR-0003](../adr/0003-cloud-sync.md).

Cloud Sync is optional and off until the brewer connects it. Brewing never depends on it: a process runs the same
with or without a network.

## 14.1 Connecting

- `SYNC-001` Cloud Sync is **available** when `cloudSync.enabled` is `true` (default) and the provider is set up
  (it has a client id, built in or from `cloudSync.googleDrive.clientId`). When it is not available, its state is
  `unavailable`: the UI does not show it, its commands are rejected (`SYNC-030`), and the application makes no
  request to the provider.
- `SYNC-002` **Connect** signs in without a browser on the device: the application asks the provider for a
  sign-in code, and the state becomes `connecting` with the code, the address to enter it at, and when it expires
  (`IF-090`). The brewer opens the address on another device, enters the code and allows access. The application
  asks the provider every few seconds (at the interval the provider sets) whether access was allowed. When it was,
  the state becomes `idle` with the connected account (its e-mail address), the sign-in is stored (`SYNC-004`), the
  notice file is written (`SYNC-016`), and a sync runs at once.
- `SYNC-003` A sign-in that does not succeed ends in state `disconnected` with a message (`IF-090`), and nothing on
  disk changes:

  | Case | Message |
  |---|---|
  | The code expired | `The sign-in code expired. Connect again.` |
  | The brewer did not allow access | `Access was not allowed. Connect again.` |
  | Access to the files was not allowed (only the account was) | `Access to Google Drive files was not allowed. Connect again and allow it.` |
  | The provider cannot be reached or answers with an error | `Sign-in failed: <reason>.` |

  **Connect** while `connecting` starts a new sign-in with a new code.
- `SYNC-004` The sign-in (the provider's long-lived token and the account) is stored in the file
  `.cloud-sync/credentials.json` in the configuration file's directory, written like every file there
  (`NFR-032`). After a restart, Cloud Sync is connected again without a new sign-in. The token, the sign-in codes
  and the recipe contents are never logged.
- `SYNC-005` **Disconnect** (also while `connecting`) ends the sign-in at the provider (if that fails, the
  token is still deleted on the device), deletes `.cloud-sync/credentials.json`, removes the notice file, and the
  state becomes `disconnected`. The recipe files stay as they are; from then on they are ordinary recipe files
  that the brewer can change, and the application does not write deleted ones again (`RCP-001`).
- `SYNC-006` When the provider says the stored sign-in is no longer valid (the brewer removed the access at the
  provider, or it expired), Cloud Sync disconnects as in `SYNC-005`, with the message
  `Access to Google Drive was revoked. Connect again.`

## 14.2 The mirror

- `SYNC-010` **Cloud folder.** The application syncs the folder `BrewberryPi/recipes` that the recipe editor
  creates in the brewer's Google Drive, and only the files of it that the editor created or that the brewer picked
  in the editor. It finds the folder the way the editor marks it, even if the brewer renamed it. It never creates,
  changes or deletes anything in the cloud.
- `SYNC-011` **The mirror rule.** While connected, after each successful sync the recipe files of the recipe
  directory (`*.json`, not hidden, `RCP-001`) are exactly the recipes of the cloud folder that this application
  can read (`SYNC-013`), each with the content of `SYNC-012`:

  | On the device before the sync | After the sync |
  |---|---|
  | A cloud recipe is missing (new in the cloud, or deleted on the device) | its file is written |
  | A recipe file differs from the cloud recipe (changed in the cloud, or on the device) | its file is copied to `.backup/<id>.local.json` (only if it was changed on the device), then overwritten |
  | A recipe file has no cloud recipe (deleted, trashed or renamed in the cloud, or added on the device) | it is moved to `.backup/<id>.removed.json` |
  | Other files, hidden files, subdirectories | left as they are |

  `.backup` is the hidden subdirectory of the recipe directory that also holds the originals of migrated files
  (`RCP-010`). A copy replaces an older copy of the same name. Every file is written atomically and
  group-writable (`NFR-032`). The recipe list then changes as for any change of the directory (`RCP-006`), and a
  loaded process keeps its recipe.
- `SYNC-012` **Content.** A cloud file in the latest format version is written byte for byte. A file in an older
  version is migrated (`RCP-010`) and written in the latest version, without a `.backup/<id>.v<N>.json` copy (the
  original stays in the cloud), so the recipe directory never has to migrate it again.
- `SYNC-013` **Cloud files that are not synced.** A cloud file that this application cannot read is not on the
  device (a recipe file of that id is removed as in `SYNC-011`) and is listed in the status's `issues` (`IF-090`)
  with its message:

  | Case | Message |
  |---|---|
  | Name does not end in `.json` | not synced, not listed |
  | Name is not `<id>.json` with a valid recipe id (`RCP-001`) | `Not a valid recipe file name.` |
  | More than one file with the same name | `More than one file in Google Drive has this name.` |
  | Invalid or in a newer format version (`RCP-003`, `RCP-009`) | the error the recipe directory would log for it |
- `SYNC-014` **Nothing changes on an error.** The recipe directory is only changed after the cloud folder was found
  and listed completely, and every file that changed was downloaded. If any of that fails, the sync stops and the
  recipe directory stays as it is, so the device brews with the recipes of the last successful sync:

  | Case | State | Message |
  |---|---|---|
  | No network, the provider does not answer, or it answers that it is busy or failing | `offline` | `Google Drive cannot be reached: <reason>.` |
  | The cloud folder does not exist | `error` | `The folder BrewberryPi/recipes was not found in Google Drive. Save a recipe in the recipe editor first.` |
  | Any other error of the provider | `error` | `Google Drive answered with an error: <reason>.` |

  A cloud folder that exists and is empty empties the recipe directory (`SYNC-011`).
- `SYNC-015` A recipe file that cannot be written, copied or moved is logged as a warning and does not stop the
  sync of the others; the next sync tries again.
- `SYNC-016` **Notice file.** While connected, the recipe directory holds the file `DO-NOT-EDIT-Cloud-Sync.txt`. It
  is not a recipe file (`RCP-001`). It is written again if it is missing or differs, and removed on disconnect
  (`SYNC-005`). Its text (`<account>` is the connected account), each sentence on its own line:

  ```text
  This folder is a copy of the folder BrewberryPi/recipes in Google Drive (<account>).
  Edit the recipes at https://recipes.brewberrypi.org.
  Changes made here are overwritten at the next sync; replaced files are kept in .backup.
  To edit the recipes here instead, disconnect Cloud Sync in the BrewberryPi app.
  ```

## 14.3 Timing

- `SYNC-020` While connected, a sync runs:
  - at startup, once the outputs are off (`NFR-021`), without delaying the startup;
  - right after a successful **Connect**;
  - every `cloudSync.intervalSeconds` (default 30, minimum 10) after the end of the previous sync;
  - at once on **Sync now**.

  Times are measured with the monotonic clock (`NFR-005`).
- `SYNC-021` After a sync that failed (`SYNC-014`), the wait before the next one doubles, starting from
  `cloudSync.intervalSeconds`, up to 300 s. A successful sync sets it back to `cloudSync.intervalSeconds`.
  **Sync now** runs a sync at once in any case.
- `SYNC-022` Cloud Sync never touches the outputs, the sensor or the engine, and a failure in it never stops
  anything else. Errors are logged once when the state or message changes, not on every sync.

## 14.4 Commands

- `SYNC-030` Commands (`IF-091`) and their replies:

  | Command | When | Outcome | Message |
  |---|---|---|---|
  | `cloudSyncConnect` | `disconnected` or `connecting` | accepted | `Connecting to Google Drive.` |
  | `cloudSyncConnect` | connected (`idle`, `syncing`, `offline`, `error`) | conflict | `Cloud Sync is already connected.` |
  | `cloudSyncDisconnect` | `connecting` or connected | accepted | `Cloud Sync disconnected.` |
  | `cloudSyncDisconnect` | `disconnected` | conflict | `Cloud Sync is not connected.` |
  | `cloudSyncNow` | connected | accepted | `Cloud Sync started.` |
  | `cloudSyncNow` | `disconnected` or `connecting` | conflict | `Cloud Sync is not connected.` |
  | any | `unavailable` | conflict | `Cloud Sync is not available.` |

  The sign-in code arrives with the status (`IF-090`), not in the reply.
