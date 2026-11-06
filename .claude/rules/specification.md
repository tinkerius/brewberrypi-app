---
paths:
  - "docs/**/*.md"
  - "docs/specification/data/**"
---

# Editing the specification and docs

- Keep `docs/specification/` technology-neutral. Technology choices belong in `docs/adr/`.
- Never change specified behaviour silently. A behaviour change needs a product-owner answer recorded in `docs/specification/13-open-questions.md` (or an ADR) first; then update the affected sections and, if legacy behaviour changes, add a line to `docs/recovery/bug-and-deviation-log.md`.
- Requirement IDs are stable: never renumber or reuse an ID. New requirements take the next free number of their prefix (prefix table in `docs/specification/00-index.md`).
- Mark behaviour that did not exist in the legacy system with `[NEW]` and deliberate legacy bug fixes with `[FIX-nn]`.
- Keep `docs/specification/data/*.json` valid against their schemas, and keep the human-readable tables in `07-recipes.md` in sync with `data/recipes/*.json`.
- A change of the recipe file format, `data/recipe.schema.json` or the shipped recipes also has to be followed in the recipe editor (`tinkerius/brewberrypi-recipeeditor`, its `/follow-app-format`, after the app release).
- Use relative Markdown links between documents.
