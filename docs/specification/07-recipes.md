# 07 — Recipes

## 7.1 Recipe catalogue

- `RCP-001` Recipes are read only from a **recipe directory** with **one file per recipe** (`Q-27`): the file `<id>.json` holds the recipe with that `id`, so the file name is the recipe id (letters, digits, `_` and `-`) and the file itself has no `id`. The directory is `recipes` by default, configurable with `recipes.directory`, relative to the configuration file's directory (`Q-19`). Only files ending in `.json` are read; hidden files (name starting with `.`), other files and subdirectories are ignored. If the recipe directory does not exist, the application creates it and writes the shipped recipes (`RCP-002`) into it, one file each, so a first start already offers recipes; a recipe file the brewer deletes later is not written again (`Q-29`). If the recipe directory cannot be created or read, the application refuses to start. Format of one file: §7.2 and [data/recipe.schema.json](data/recipe.schema.json). `[NEW]` (legacy: recipes hard-coded in source code)
- `RCP-002` The shipped recipes MUST be exactly the 2 recipes of [data/recipes/](data/recipes/), `BENIWEISS` and `WaterBoil`, one file each (`Q-32`; legacy: 8 recipes, the other 6 are no longer shipped). The recipe list is sorted by recipe id in character-code order (upper case before lower case): `BENIWEISS`, `WaterBoil` (`Q-27`).
- `RCP-003` Invalid recipe file (syntax, schema, unknown goal type, missing parameter, file name that is not a valid id, unreadable file, old format with `goals` at the top level, format `version` that is invalid or newer than the application supports, failed migration — see §7.2) → the application logs a precise error (file, recipe id, goal index) and **skips that recipe**; all other recipes are loaded (`Q-27`). A recipe directory without valid recipes gives an empty recipe list.
- `RCP-004` The catalogue is read-only at runtime (no recipe editing in the UI; see `Q-09`). Recipe files are written with the separate web editor (https://recipes.brewberrypi.org) or by hand.
- `RCP-005` The list of recipes (`id`, `name`) is published to clients (`IF-030`).
- `RCP-006` `[NEW]` While it runs, the application checks the recipe directory every `recipes.checkMilliseconds` (default 1000) and reads it again when a recipe file was added, changed or removed (`Q-29`); `RCP-003` applies to the files read again. The new recipe list is pushed to every client (`IF-072`), and `load` uses it. A loaded process keeps the recipe it was loaded from. A recipe directory that disappears is created again as in `RCP-001`; while it cannot be read, the recipe list stays as it is and the error is logged once. (Legacy: recipes changed only with a new program version.)

## 7.2 Recipe file format

- `RCP-007` `[NEW]` A recipe file is a JSON object with the display `name` and the section `mashing_process`, whose `goals` are the recipe's ordered list of goal definitions (§7.3). The list may be empty (`Q-34`): such a recipe is listed and its details can be read, but it cannot be loaded (`ENG-010`); `mashing_process` itself is always required. Sections with recipe design information (e.g. grain bill, hops) may be added next to `mashing_process` later; until the schema defines them, any other key is invalid (`Q-31`). The older format with `goals` at the top level is invalid: the error says to move `goals` into `mashing_process` (`RCP-003`).

```json
{
  "$schema": "../recipe.schema.json",
  "version": 2,
  "name": "Water Boiling",
  "mashing_process": {
    "goals": [
      { "type": "HeatUpForHopsCooking" },
      { "type": "CookWater", "minutes": 20, "name": "Cooking Water", "description": "Cooking water for 20min" }
    ]
  }
}
```

`$schema` is optional and ignored by the application.

### 7.2.1 Format version and migration `[NEW]` (`Q-33`)

- `RCP-008` A recipe file states the version of the recipe file format it is written in, `version`: an integer
  (1, 2, 3, …). It is optional; a file without `version` is in version 1. The format described in this section
  is the **latest version**, the one the application reads (table below). When the application writes a
  recipe file, `version` comes right after `$schema` (first if there is no `$schema`).
- `RCP-009` A `version` that is not an integer ≥ 1 is invalid (`RCP-003`). A `version` greater than the latest
  version → the recipe is skipped (`RCP-003`); the error names the file, the recipe, the file's version and the
  latest version, and says to update the application.
- `RCP-010` A file in an older version is first checked against the format of its own version (the frozen
  schema `data/recipe.v<N>.schema.json`), then migrated one version at a time with the migrators of the table
  below (`N → N+1`, `N+1 → N+2`, … up to the latest version), then checked against the latest format. Errors
  in the first check are reported like those of a file in the latest version; a migrator that fails or a
  migrated recipe that is not valid in the latest format → the recipe is skipped (`RCP-003`) and its file is
  left as it is. Only when all of it succeeds the application writes the file back in the latest version:
  first a copy of the original file, unchanged, to `.backup/<id>.v<N>.json` in the recipe directory (a hidden
  subdirectory, so never read as a recipe, `RCP-001`), replacing an older copy of that name, then the migrated
  recipe to `<id>.json`. Both are written atomically and group-writable (`NFR-023`). If either cannot be
  written, the migrated recipe is still used, the original file stays as it is and a warning is logged. A file
  already in the latest version (also one without `version` while the latest version is 1) is never written.
  The rewritten file counts as a changed recipe file (`RCP-006`) and is read again without migration.
  > Consequence: after a rollback to an application version with an older latest version, the recipes it
  > finds in a newer version are skipped (`RCP-009`); the brewer can put the copies from `.backup` back.
- `RCP-011` Every change of the recipe file format — also one that only adds something, because an
  application rejects keys it does not know (`RCP-007`) — raises the latest version by one and, in the same
  change: freezes the schema of the previous version as `data/recipe.v<N>.schema.json`, adds the migrator
  `N → N+1` with its rule to the table below, updates `data/recipe.schema.json` and the shipped recipes
  (`RCP-002`) to the new version, and adds acceptance criteria for the migration.

| Version | Since | Change | Migrator from the previous version |
|---|---|---|---|
| 1 | 2026-11-06 (`Q-31`, `Q-33`) | `name` and `mashing_process` with `goals`; `version` introduced | – (first version; a file without `version` is in version 1) |
| 2 | 2026-11-06 (`Q-34`) | `mashing_process.goals` may be empty | `1 → 2`: no change but `version` (every version-1 recipe is valid in version 2) |

## 7.3 Goal definition format

```json
{ "type": "HeatUp", "temperature": 73 }
{ "type": "Rest", "temperature": 73, "minutes": 40,
  "name": "Verzuckerungsrast", "description": "Verzuckerungsrast @73 for 40min" }
{ "type": "IodineTest", "temperature": 73, "minutes": 10 }
{ "type": "CookWater", "name": "Cooking Water", "description": "Cooking water for 20min", "minutes": 20 }
{ "type": "PourInBrewingWater" } { "type": "MashIn" } { "type": "Lautering" } { "type": "HeatUpForHopsCooking" }
```

| type | required | optional |
|---|---|---|
| `PourInBrewingWater` | – | – |
| `HeatUp` | `temperature` | – |
| `MashIn` | – | – |
| `Rest` | `temperature`, `minutes` | `name`, `description` (both or neither; default texts per `GOAL-004`) |
| `IodineTest` | `temperature`, `minutes` | – |
| `Lautering` | – | – |
| `HeatUpForHopsCooking` | – | – |
| `CookWater` | `name`, `description`, `minutes` | – |

Temperatures in °C (number, 0…110), minutes integer ≥ 0.

## 7.4 Shipped recipe catalogue (human-readable)

Recipe texts (including German names and legacy typos) are reproduced **verbatim**. The recipe list is sorted by
id (`RCP-002`). Recipe `BENIWEISS` is the file `data/recipes/BENIWEISS.json`, and so on.

### 1. `BENIWEISS` — `München 12.6 - SUD2:BENIWEISS`
1. PourInBrewingWater
2. HeatUp 40
3. MashIn
4. HeatUp 52
5. Rest 52 °C / 30 min — `Eiweißrast` / `Eiweißrast @52 for 30min`
6. HeatUp 65
7. Rest 65 °C / 30 min — `Maltoserastr` / `Maltoserastr @65 for 30min`
8. HeatUp 73
9. Rest 73 °C / 40 min — `Verzuckerungsrast` / `Verzuckerungsrast @73 for 40min`
10. IodineTest 73 °C / 10 min
11. HeatUp 78

### 2. `WaterBoil` — `Water Boiling`
1. HeatUpForHopsCooking (97 °C, boost)
2. CookWater 20 min — `Cooking Water` / `Cooking water for 20min`

> The brewing recipe ends after heating up for lautering; lautering and boiling
> were never added (legacy `// ...` / `TODO go on`). After the last goal the
> engine switches everything off (`ENG-025`).

## 7.5 Test recipes (not shipped)

Used by acceptance tests only; not part of the production catalogue.

- `DEMO` — `Demo Process`: HeatUp 62, Rest 62/10 (default texts), IodineTest 62/10, HeatUp 78, Lautering,
  HeatUpForHopsCooking. Every kind of operator request in a short run (`AC-090`).
- `IODINE` — `Iodine Test`: IodineTest 25/1 (`AC-037`, `AC-053`, `AC-058`).

From the legacy unit test:

`PID` — `TEST PROCESS`: PourInBrewingWater, HeatUp 25, MashIn, HeatUp 40,
Rest 40/2 (default texts), IodineTest 40/1, HeatUp 50, Lautering, HeatUpForHopsCooking.
