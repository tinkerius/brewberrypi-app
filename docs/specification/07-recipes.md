# 07 — Recipes

## 7.1 Recipe catalogue

- `RCP-001` Recipes are read only from a **recipe directory** with **one file per recipe** (`Q-27`): the file `<id>.json` holds the recipe with that `id`, so the file name is the recipe id (letters, digits, `_` and `-`) and the file itself has no `id`. The directory is `recipes` by default, configurable with `recipes.directory`, relative to the configuration file's directory (`Q-19`). Only files ending in `.json` are read; hidden files (name starting with `.`), other files and subdirectories are ignored. If the recipe directory does not exist, the application creates it and writes the shipped recipes (`RCP-002`) into it, one file each, so a first start already offers recipes; a recipe file the brewer deletes later is not written again (`Q-29`). If the recipe directory cannot be created or read, the application refuses to start. Format of one file: §7.2 and [data/recipe.schema.json](data/recipe.schema.json). `[NEW]` (legacy: recipes hard-coded in source code)
- `RCP-002` The shipped recipes MUST be exactly the 8 legacy recipes of [data/recipes/](data/recipes/), one file each. The recipe list is sorted by recipe id in character-code order (upper case before lower case): `BENIWEISS`, `Dummy`, `HANCOCK`, `HIPA`, `TEST`, `WIT`, `WaterBoil`, `ZORA` (`Q-27`; legacy: the order of §7.4).
- `RCP-003` Invalid recipe file (syntax, schema, unknown goal type, missing parameter, file name that is not a valid id, unreadable file, old format with `goals` at the top level — see §7.2) → the application logs a precise error (file, recipe id, goal index) and **skips that recipe**; all other recipes are loaded (`Q-27`). A recipe directory without valid recipes gives an empty recipe list.
- `RCP-004` The catalogue is read-only at runtime (no recipe editing in the UI; see `Q-09`).
- `RCP-005` The list of recipes (`id`, `name`) is published to clients (`IF-030`).
- `RCP-006` `[NEW]` While it runs, the application checks the recipe directory every `recipes.checkMilliseconds` (default 1000) and reads it again when a recipe file was added, changed or removed (`Q-29`); `RCP-003` applies to the files read again. The new recipe list is pushed to every client (`IF-072`), and `load` uses it. A loaded process keeps the recipe it was loaded from. A recipe directory that disappears is created again as in `RCP-001`; while it cannot be read, the recipe list stays as it is and the error is logged once. (Legacy: recipes changed only with a new program version.)

## 7.2 Recipe file format

- `RCP-007` `[NEW]` A recipe file is a JSON object with the display `name` and the section `mashing_process`, whose `goals` are the recipe's ordered, non-empty list of goal definitions (§7.3). Sections with recipe design information (e.g. grain bill, hops) may be added next to `mashing_process` later; until the schema defines them, any other key is invalid (`Q-31`). The older format with `goals` at the top level is invalid: the error says to move `goals` into `mashing_process` (`RCP-003`).

```json
{
  "$schema": "../recipe.schema.json",
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

## 7.3 Goal definition format

```json
{ "type": "HeatUp", "temperature": 62 }
{ "type": "Rest", "temperature": 62, "minutes": 30,
  "name": "Verzuckerungsrast", "description": "Verzuckerungsrast @62 for 30min" }
{ "type": "IodineTest", "temperature": 62, "minutes": 10 }
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

## 7.4 Legacy recipe catalogue (human-readable)

Recipe texts (including German names and legacy typos) are reproduced **verbatim**. The numbering is the
legacy order; the recipe list is sorted by id (`RCP-002`). Recipe `ZORA` is the file `data/recipes/ZORA.json`, and so on.

### 1. `ZORA` — `München 12.6 - SUD1:ZORA`
1. PourInBrewingWater
2. HeatUp 40
3. MashIn
4. HeatUp 50
5. Rest 50 °C / 25 min — `Maltoserast` / `Maltoserast @50 for 25min`
6. HeatUp 62
7. Rest 62 °C / 30 min — `Verzuckerungsrast` / `Verzuckerungsrast @62 for 30min`
8. HeatUp 70
9. Rest 70 °C / 40 min — `2. Verzuckerungsrast` / `2. Verzuckerungsrast @70 for 40min`
10. IodineTest 68 °C / 10 min  *(note: 68 after a 70 °C rest — see `Q-05`)*
11. HeatUp 76

### 2. `HANCOCK` — `München 12.6 - SUD3:HANCOCK`
1. PourInBrewingWater
2. HeatUp 40
3. MashIn
4. HeatUp 50
5. Rest 50 °C / 30 min — `Eiweißrast` / `Eiweißrast @50 for 30min`
6. HeatUp 66
7. Rest 66 °C / 110 min — `Maltoserast` / `Maltoserast @66 for 110min`
8. IodineTest 66 °C / 10 min
9. HeatUp 78

### 3. `HIPA` — `München 12.6 - SUD4:HIPA`
1. PourInBrewingWater
2. HeatUp 68
3. MashIn
4. Rest 68 °C / 60 min — `Verzuckerungsrast` / `Verzuckuerungsrast @68 for 60min`
5. IodineTest 68 °C / 10 min

### 4. `WIT` — `München 12.6 - SUD5:WIT`
1. PourInBrewingWater
2. HeatUp 40
3. MashIn
4. HeatUp 65
5. Rest 65 °C / 50 min — `Maltoserast` / `Maltoserast @65 for 50min`
6. IodineTest 65 °C / 10 min
7. HeatUp 78

### 5. `BENIWEISS` — `München 12.6 - SUD2:BENIWEISS`
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

### 6. `WaterBoil` — `Water Boiling`
1. HeatUpForHopsCooking (97 °C, boost)
2. CookWater 20 min — `Cooking Water` / `Cooking water for 20min`

### 7. `Dummy` — `My Dummy Process`
1. HeatUp 62
2. Rest 62 °C / 10 min (default texts)
3. IodineTest 62 °C / 10 min
4. HeatUp 78
5. Lautering
6. HeatUpForHopsCooking

### 8. `TEST` — `Test Process`
1. IodineTest 25 °C / 1 min

> All brewing recipes end after heating up for lautering; lautering and boiling
> were never added (legacy `// ...` / `TODO go on`). After the last goal the
> engine switches everything off (`ENG-025`).

## 7.5 Test fixture recipe (from legacy unit test)

Used by acceptance tests only; not part of the production catalogue.

`PID` — `TEST PROCESS`: PourInBrewingWater, HeatUp 25, MashIn, HeatUp 40,
Rest 40/2 (default texts), IodineTest 40/1, HeatUp 50, Lautering, HeatUpForHopsCooking.
