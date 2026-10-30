# 08 — Client/Server Interface

## 8.1 Communication model

- `IF-001` `[NEW]` Browser and server use **two interaction styles** (`Q-28`):
  - **Request/response** for everything the client asks or orders: reading the current state (§8.2) and the
    commands (§8.3). Every request gets exactly **one** reply, to that client only, with an outcome (`IF-052`).
  - **Push** for everything the server tells all clients: a snapshot on connect, then only **changes** (§8.4).

  A command answers the client that sent it; the state change it causes is pushed to every client.
  (Legacy: CometD/Bayeux publish/subscribe for both; commands and periodic broadcasts on named topics.)
- `IF-002` The concrete transport is a phase-2 technology decision ([ADR-0002](../adr/0002-rest-and-server-sent-events.md)).
  Requirements: works in current browsers without plugins; the push channel reconnects automatically after
  network loss or a server restart; works over plain HTTP in a LAN; one server port (default **8080**, config
  `server.port`) serves the web UI, the requests and the push channel.
  (Legacy: CometD/Bayeux long-polling at `/cometd`, WebSocket disabled on the client.)
- `IF-003` The names below (resources, commands, events) are **logical names**; the concrete addressing is defined
  in ADR-0002. Legacy topic → new name: `/hardware/status` → `hardware`, `/process/state` → `process`,
  `/process/recipes` → `recipes`, `/process/chart` → `chart`, `/service/process/<command>` → command `<command>`,
  `/service/process/request` → `respond`, `/process/reply` → the reply of the request itself.
- `IF-004` All payloads are validated against [data/messages.schema.json](data/messages.schema.json).
- `IF-005` A newly connected client MUST receive the current value of all state (hardware, process, recipes,
  chart) within **1 s** (`IF-071`).

## 8.2 State (read on request, pushed on change)

| Resource | Payload | Req |
|---|---|---|
| `hardware` | `HardwareStatus` | `IF-010` |
| `process` | `ProcessState` | `IF-020` |
| `recipes` | `RecipeList` | `IF-030` |
| `recipe` (one, by id) | `RecipeDetail` | `IF-031` |
| `chart` | `ChartData` | `IF-040` |
| `ui` | `UiSettings` | `IF-080` |

Each resource can be read at any time; `hardware`, `process`, `recipes` and `chart` are also pushed (§8.4).

### `IF-010` HardwareStatus

```json
{ "temperature": 62.5, "pumpstatus": true, "heatingstatus": 0.5 }
```

| Field | Type | Meaning |
|---|---|---|
| `temperature` | number (1 decimal) or `null` | Current kettle temperature; `null` if INVALID `[FIX-02, FIX-16]` |
| `pumpstatus` | boolean | Pump on |
| `heatingstatus` | number 0…1 | Requested heating power |

Legacy: `temperature` was a **string** formatted with the server locale (`"62,5"` on a German system) and showed the error sentinel as a huge negative number. `[FIX-16]`

### `IF-020` ProcessState

No process loaded `[FIX-18]` (legacy sent the string `"null"`):

```json
{ "state": "NoProcess" }
```

Process loaded:

```json
{
  "processid": "ZORA",
  "processname": "München 12.6 - SUD1:ZORA",
  "state": "Running",
  "goals": [
    { "name": "Add Water", "description": "Pour in the brewing water.",
      "status": "Done", "statuspercent": 100, "statusmessage": "100%" },
    { "name": "Heat Up", "description": "Heat up mash to 40.0 degrees (C).",
      "status": "Active", "statuspercent": 37, "statusmessage": "37%" },
    { "name": "Mash In", "description": "Now it's your job to mash in the malt.",
      "status": "Waiting", "statuspercent": 0, "statusmessage": "0%" }
  ]
}
```

A goal with an active request additionally contains:

```json
"activeRequest": { "id": "7", "message": "Iodine test successfull?", "options": ["Yes", "No"] }
```

- `state` is `[NEW, FIX-20]`; all other field names are legacy.
- `activeRequest` is **omitted** when there is no request (legacy behaviour).
- `activeRequest.id` `[NEW]` identifies the request; an answer names it (`DOM-035`, `IF-054`).
- Snapshot consistency: a published ProcessState MUST reflect one consistent point in time (`ENG-040`).

### `IF-030` RecipeList

```json
[ { "id": "ZORA", "name": "München 12.6 - SUD1:ZORA" }, { "id": "HANCOCK", "name": "München 12.6 - SUD3:HANCOCK" } ]
```

Order = catalogue order (`DOM-002`, `RCP-002`).

### `IF-031` RecipeDetail `[NEW]`

One recipe with its goals exactly as in its recipe file's `mashing_process` ([07](07-recipes.md) §7.2, §7.3; the message keeps them at the top level, `Q-31`), so the UI can show a recipe
before it is loaded. Unknown id → not found (`IF-052`).

```json
{ "id": "WaterBoil", "name": "Water Boiling",
  "goals": [ { "type": "HeatUpForHopsCooking" },
             { "type": "CookWater", "minutes": 20, "name": "Cooking Water", "description": "Cooking water for 20min" } ] }
```

### `IF-040` ChartData

```json
[ { "key": "Kettle Temperature", "values": [ { "x": 1718197200000, "y": 62.5 } ] } ]
```

- `IF-041` The server samples the temperature every chart period (1 s, `chart.sampleMilliseconds`) and appends `{x: epoch milliseconds, y: °C}` **if the reading is valid** (legacy: if `> 0`). `[FIX-17]`
- `IF-042` History covers the time since **server start** and is shared by all clients; it is **not** reset when a process is loaded (legacy; see `Q-08`).
- `IF-043` **Thinning**: after appending, while the number of points exceeds **200** (`chart.maxPoints`):
  find the adjacent pair `(i, i+1)` with the **smallest time gap** `x[i+1] − x[i]` (first such pair on ties) and **remove point `i+1`**. The first point is never removed. This keeps the full time range with decreasing resolution for older data.
- `IF-044` The whole series is sent only on request and in the snapshot (`IF-071`); after that each sample is pushed as a **ChartUpdate** (`IF-073`). Applying the updates gives exactly the series of `IF-043`. A read MAY ask for only the points after a given `x`. (Legacy: the whole series was broadcast every second; `Q-28`.)

### `IF-080` UiSettings `[NEW]`

Settings of the web UI from the configuration (`Q-30`), read by the UI on every (re)connection; not pushed.
`deviceLink` is `ui.deviceLink` with `{host}` not yet replaced, or `null` when it is empty (`UI-002`).

```json
{ "deviceLink": "http://{host}:8081/" }
```

## 8.3 Commands (request/response)

| Command | Arguments | Engine command |
|---|---|---|
| `load` | `{ "recipe": "<recipeId>" }` | `load` (`ENG-010`) |
| `unload` | – | `unload` (`ENG-011`) |
| `start` | – | `start` (`ENG-012`) |
| `pause` | – | `pause` (`ENG-013`) |
| `resume` | – | `resume` (`ENG-016`) |
| `stop` | – | `stop` (`ENG-014`) |
| `respond` | request id + `{ "response": "<option>" }` | `respond` (`ENG-015`, `IF-054`) |

### `IF-050` CommandReply `[FIX-15]`

Every command gets exactly one reply, sent only to the issuing client:

```json
{ "command": "load", "success": true, "message": "Process München 12.6 - SUD1:ZORA (ZORA) loaded." }
```

- Legacy only answered `load` (on a topic the UI did not listen to), and the UI listened for replies of unload/start/pause/stop that were never sent — so no feedback was ever shown.
- `IF-051` Malformed arguments (missing/invalid fields, not JSON) → `success=false` with an explanatory message; never a server-side crash.
- `IF-052` `[NEW]` Every reply carries an **outcome** besides `success` (ADR-0002 maps them to HTTP status codes):

  | Outcome | When |
  |---|---|
  | accepted | the command was carried out |
  | invalid | malformed arguments (`IF-051`), a missing recipe id, an answer that is not one of the options (`DOM-032`) |
  | not found | unknown recipe, unknown command or resource |
  | conflict | not allowed in the current state (matrix in [03](03-brew-process-engine.md) §3.2), or the request is no longer open (`IF-054`) |
  | forbidden | refused by the same-origin rule (`IF-061`) |

  A reply that is not a command's own reply (forbidden, unknown command) has only `success=false` and `message`.
- `IF-053` `[NEW]` The state change a command causes is pushed to every client (`IF-072`) **before** the command's
  reply is sent, so the sender's own view is never behind its reply.
- `IF-054` `[NEW]` `respond` names the request it answers (its `id`, `DOM-035`). An answer for a request that is no
  longer the active one (answered, removed, or replaced by a new question) → conflict,
  `"This request is no longer open."`; the active request stays unanswered.

## 8.4 Push channel (events)

- `IF-070` `[NEW]` A client opens one push channel and receives these events, each with a JSON payload:

  | Event | Payload | When |
  |---|---|---|
  | `snapshot` | `Snapshot`: `hardware`, `process`, `recipes`, `chart`, `heartbeatMilliseconds` | first event of every connection (`IF-071`) |
  | `hardware` | `HardwareStatus` | when it changed (`IF-072`) |
  | `process` | `ProcessState` | when it changed |
  | `recipes` | `RecipeList` | when it changed |
  | `chart` | `ChartUpdate` `{ "add": {x, y}, "remove": [x, …] }` | after each chart sample that added a point (`IF-073`) |
  | `ping` | `{}` | every heartbeat period (`IF-074`) |
- `IF-071` Every (re)connection starts with a `snapshot` of the whole state, so a client never needs the events it
  missed while disconnected.
- `IF-072` State events are sent **only when the payload changed**. The server compares the current payload with the
  last pushed one every `events.checkMilliseconds` (250 ms) and at once after every command (`IF-053`). A failure while
  building one payload is logged and MUST NOT stop the push channel (legacy behaviour).
  (Legacy: every topic was broadcast every 500 ms, changed or not; `Q-28`.)
- `IF-073` A `chart` event carries the new point and the `x` of every point that thinning (`IF-043`) removed with it.
  A client appends the new point and **then** removes those points: once the chart is full, the new point is usually
  among them (thinning removes the later point of the closest pair), so it must not stay.
- `IF-074` A `ping` event is sent every `events.heartbeatMilliseconds` (5000 ms; the snapshot tells the client the
  period). A client that receives no event for **2.5** heartbeat periods treats the connection as dead and reconnects.
- `IF-075` A client that falls too far behind (more than 100 pending events) is disconnected; it reconnects and gets a
  new snapshot. Pending state events of the same kind are merged, so only the latest is sent.
- `IF-076` On shutdown (`NFR-022`) the server ends every push connection at once, so open connections do not delay
  switching the outputs off.

## 8.5 Security

- `IF-060` No authentication/authorisation (legacy). Intended for a trusted LAN only. See `Q-10`.
- `IF-061` `[NEW]` Same-origin rule (`Q-17`): the server refuses requests and push connections from other web pages
  (an `Origin` other than the server itself) → forbidden. Commands MUST be sent as JSON, which browsers send cross-site
  only after a preflight check the server never allows. Requests without an `Origin` (command-line tools, scripts)
  are accepted.
