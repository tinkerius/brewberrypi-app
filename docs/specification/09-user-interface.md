# 09 — User Interface

Single-page web application served by the BrewberryPi server at `/` (`index`).
Responsive (usable on phone, tablet and desktop). Language of UI texts: English
(legacy); recipe texts as defined in the recipe files.

## 9.1 Layout (top to bottom)

```text
┌──────────────────────────────────────────────────────────────┐
│ BrewberryPi  <host> · <version>  [☁✓ Synced 14:32] Device settings ↗ │
├──────────────────────────────────────────────────────────────┤
│ [Hardware Status]                                             │
│  Temperature: 62.5°C   Pump: on   Heating: 50%   Process: …   │
├──────────────────────────────────────────────────────────────┤
│ [Chart]                                                       │
│  line chart "Kettle Temperature" over time (height ≈ 300 px)  │
├──────────────────────────────────────────────────────────────┤
│ [Brewing Process]                                             │
│  <process name>                                               │
│  ┌ goal list item (one per goal) ───────────────────────────┐ │
│  │ Heat Up                                      [Active]    │ │
│  │ Heat up mash to 62.0 degrees (C).                        │ │
│  │ [██████████░░░░░░░░░ 50%]                                │ │
│  │ Iodine test successfull?  [Yes] [No]                     │ │
│  └──────────────────────────────────────────────────────────┘ │
│  [Load Process ▾] [Unload Process]                            │
│  [Start Process] [Pause Process] [Stop Process]               │
└──────────────────────────────────────────────────────────────┘
          (modal "Information" with [Confirm] for replies)
```

Page title: `BrewberryPi`, also shown as the page heading (product-owner decision 2026-10-27;
the legacy title was `Brewberry Pi`). A visual refresh is
allowed as long as all elements and behaviours below are kept.

Phase 2 uses such a refresh (product-owner decision 2026-10-11; styling technology in
[ADR-0001, Amendments](../adr/0001-technology-stack.md#amendments)): a light/dark
scheme following the operating system, cards instead of panels, and the status
colours of `UI-031`. It uses the BrewberryPi brand (product-owner decision 2026-10-27): the
colours, logo, favicons and the Zilla Slab typeface of
[tinkerius/brewberrypi `brand/README.md`](https://github.com/tinkerius/brewberrypi/blob/main/brand/README.md),
served locally (`UI-070`); `UI-031`'s green and amber are the brand's leaf and brass. (Legacy look, for reference: Bootstrap 3 panels ("panel-primary")
with blue headers and a dark navbar titled **Brewberry Pi**.)

- `UI-001` `[NEW]` The top bar shows the page heading `BrewberryPi` as the BrewberryPi logo, the host name the page was
  opened with and the application version (`version` of `GET /healthz`,
  [runtime contract §5](../deployment/runtime-contract.md)), as `<host> · <version>`; the version
  is read again after every reconnection. The top bar follows the BrewberryPi brand
  (product-owner decision 2026-10-27; it replaces the decision of 2026-10-17 that it looks like
  the top bar of the BrewberryPi OS device web interface).
- `UI-002` `[NEW]` The top bar ends with a link `Device settings` to the BrewberryPi OS device web
  interface, opened in a new tab (`Q-30`). Its address is `ui.deviceLink` (default
  `http://{host}:8081/`, `IF-080`) with `{host}` replaced by the host name the page was opened
  with; with an empty `ui.deviceLink` there is no link. It is read again after every reconnection.

## 9.2 Hardware Status panel

- `UI-010` Shows, updated live from `hardware` (`IF-010`):
  - `Temperature: <t>°C` — one decimal; `--` before the first snapshot; **`Sensor error`** when `temperature` is `null` `[FIX-02]`.
  - `Pump: on|off` — `--` before the first snapshot.
  - `Heating: <p·100>%` — e.g. `0%`, `50%`, `100%`; `--` before the first snapshot.
- `UI-011` `[NEW, FIX-20]` Shows the engine state (`NoProcess`, `Loaded`, `Running`, `Paused`, `Stopped`, `Finished`) from `process` (`IF-020`; e.g. as a label next to the process name or in this panel).

## 9.3 Chart panel

- `UI-020` Line chart of the `chart` data (`IF-040`, updated by `chart` events, `IF-073`): x axis labelled `Time`, ticks formatted `HH:MM` (local time of the browser); y axis labelled `Temperature`, ticks with 1 decimal; one series `Kettle Temperature`.
- `UI-021` Interactive guideline/tooltip on hover showing time and temperature.
- `UI-022` Before the first snapshot, the chart is **empty** (legacy showed hard-coded demo data with a fake target profile). `[FIX-17]`
- `UI-023` The chart updates without resetting user interaction more than necessary (legacy re-rendered on every update).

## 9.4 Brewing Process panel

- `UI-030` Heading: `processname` of the loaded process; nothing when no process is loaded.
- `UI-031` One list item per goal, in order, showing:
  - badge with the goal `status` (`Waiting`/`Active`/`Done`);
  - `name` as heading;
  - `description` — line breaks (`\n`) rendered as line breaks `[FIX-24]`;
  - colour: `Done` → green (success), `Active` → yellow/amber (warning), `Waiting` → neutral;
  - **progress bar** only for the `Active` goal: width = `statuspercent` %, label = `statusmessage`;
  - if `activeRequest` exists: its `message` and **one button per option** (label = option text).
- `UI-032` Clicking an option button sends the command `respond` for that request (its `id`, `DOM-035`) with `{ "response": "<option text>" }`.
- `UI-033` The active goal SHOULD be scrolled into view / visually emphasised (long recipes have 11 goals).

## 9.5 Process controls

- `UI-040` **Load Process** dropdown: one entry per recipe from `recipes` (`IF-030`, label = `name`); selecting sends the command `load` with `{ "recipe": "<id>" }`. Before the first snapshot arrives, the dropdown is empty / shows "loading…" (legacy showed a hard-coded list of 3 recipes). `[FIX-17]`
- `UI-041` Buttons **Unload Process**, **Start Process**, **Pause Process**, **Resume Process** `[NEW]`, **Stop Process** send the corresponding commands (no arguments). **Resume Process** continues a paused process (`ENG-016`).
- `UI-042` `[NEW]` Buttons SHOULD be disabled when the command is not accepted in the current engine state (matrix in [03](03-brew-process-engine.md)). The server remains authoritative.
- `UI-043` `[NEW]` **Stop Process** SHOULD ask for confirmation ("Stop the running process? Heating and pump will be switched off.") because a stopped process cannot be resumed.

## 9.6 Information modal

- `UI-050` Every command reply (`IF-050`) is shown in a modal dialog titled **Information** with the reply `message` and a **Confirm** button that closes it. `[FIX-15]` A command that does not reach the server shows `No connection to the server.` `[NEW]`
  (Phase 2 MAY show successful replies as a non-blocking toast and only errors as modal — see `Q-11`.)

## 9.7 Connection handling

- `UI-060` On connection failure the UI shows a clear, non-blocking "disconnected — reconnecting…" indicator and reconnects automatically (legacy: `alert('sad')` on failed handshake). The UI counts as connected from the first `snapshot` of a push connection on (`IF-071`), and as disconnected after an error or a missing heartbeat (`IF-074`).
- `UI-061` While disconnected, displayed values are marked as stale and command buttons are disabled.
- `UI-062` `[NEW]` While the page is hidden (another tab, minimised window), the UI closes its push connection, and opens it again with a fresh snapshot (`IF-071`) when the page is shown. Browsers allow only a few connections per server; open background tabs must not use them up, or commands from the visible tab would wait.

## 9.8 Assets

- `UI-070` The UI MUST work without internet access (all scripts, styles and fonts are served locally by the Pi). The legacy page referenced two CDN scripts only for IE < 9; these are not required.

## 9.9 Cloud Sync in the top bar `[NEW]`

Cloud Sync ([14](14-cloud-sync.md), `Q-35`) is shown in the top bar, before the `Device settings` link: a pictogram
with a short text, and a menu with the details and the commands (product-owner decision 2026-11-06; it replaces a
panel below the Brewing Process panel). Its content follows the `cloudSync` status (`IF-090`).

- `UI-080` The control is shown only when the state is not `unavailable` (`SYNC-001`). It is a button with a
  pictogram and a short text (the text may be hidden on narrow screens; the button's accessible name is
  `Cloud Sync: <short text>`):

  | State | Pictogram | Short text |
  |---|---|---|
  | `disconnected` | cloud, crossed out (grey) | `Cloud Sync off` |
  | `connecting` | cloud with three dots (amber) | `Connecting…`, then `Enter the code` once the code is there |
  | `idle` | cloud with a tick (green) | `Synced <HH:MM>` of `lastSync`, or `Not synced yet` |
  | `syncing` | two arrows going round, spinning (`UI-085`) | `Syncing…` |
  | `offline` | cloud, crossed out (amber) | `Offline` |
  | `error` | cloud with an exclamation mark (red) | `Sync error` |

  With issues (`SYNC-013`), a small badge on the button shows their number. The button opens and closes a menu
  titled `Cloud Sync`; `Escape` or a click outside closes it.
- `UI-081` Menu, `disconnected`: the text `Off. Connect to mirror the recipes from your Google Drive, where the
  recipe editor saves them.` and a button `Connect Google Drive`. The button closes the menu and opens a dialog
  titled `Connect Google Drive` with the text `The recipes on this device will be replaced by the recipes in your
  Google Drive. Recipes that are only on this device are moved to recipes/.backup. Changes made on the device are
  overwritten while Cloud Sync is on.` and the buttons `Connect` (sends `cloudSyncConnect`) and `Cancel`.
- `UI-082` Menu, `connecting`: the text `Open <url> and enter this code:`, where `<url>` is a link opened in a new
  tab, then the code in large letters, then `The code expires at <time>.` and a button `Cancel` (sends
  `cloudSyncDisconnect`). The menu opens by itself when the code arrives.
- `UI-083` Menu, connected (`idle`, `syncing`, `offline`, `error`): the text `Connected to Google Drive
  (<account>).`, then `Last sync: <time>` (`never` before the first one; `syncing…` during one), and the buttons
  `Sync now` (`cloudSyncNow`, disabled during a sync) and `Disconnect` (`cloudSyncDisconnect`). The issues
  (`SYNC-013`) are listed under the heading `Not synced`, one line `<recipe>: <message>` each.
- `UI-084` A `message` (`IF-090`) is shown in the menu as an alert in every state. Times are shown in the
  browser's local time (`HH:MM:SS`). The replies of the Cloud Sync commands appear like every reply (`UI-050`).
- `UI-085` A sync in progress is visible in the top bar: the pictogram spins while the state is `syncing`, and for
  at least 1.5 s after a sync started, so a sync that takes only a moment is still seen.
