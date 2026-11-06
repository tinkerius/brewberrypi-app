"""Recipe file format versions and their migrators (RCP-008..011, Q-33, spec 7.2.1).

Every recipe file is in a format version, `version` (a file without it is in version 1). The latest version is
the one `data/recipe.schema.json` describes; each older version `N` keeps its frozen schema
`data/recipe.v<N>.schema.json` and a migrator `N → N+1`. A file in an older version is checked against its
own version's schema, migrated one version at a time and checked against the latest schema
(`brewberrypi.recipes`).

To change the format (RCP-011): copy `recipe.schema.json` to `recipe.v<LATEST_VERSION>.schema.json`, change
`recipe.schema.json` (with `"version": {"const": <new>}`), add the migrator `MIGRATORS[<old>]`, raise
`LATEST_VERSION`, update the shipped recipes and the version table of spec 7.2.1, and copy the data to
`src/brewberrypi/data/`.
"""

import copy
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib.resources import files
from typing import Any

import fastjsonschema

RecipeData = dict[str, Any]
"""A recipe file's JSON object."""

Migrator = Callable[[RecipeData], RecipeData]
"""Turns a recipe of version `N` into one of version `N+1`; may change its argument. `version` is set by
`RecipeFormat.migrate`."""

Validator = Callable[[Any], Any]
"""Checks a recipe against the schema of one version; raises `fastjsonschema.JsonSchemaValueException`."""

LATEST_VERSION = 2
"""The recipe file format version this application reads (spec 7.2.1)."""


def _v1_to_v2(raw: RecipeData) -> RecipeData:
    """Version 2 allows an empty `mashing_process.goals` (Q-34); every version-1 recipe is valid as it is."""
    return raw


MIGRATORS: dict[int, Migrator] = {1: _v1_to_v2}
"""The migrator from version `N` (the key) to `N+1`, one for each version below `LATEST_VERSION`."""


class MigrationError(Exception):
    """A migrator failed; the recipe is skipped and its file left as it is (RCP-010)."""


@cache
def schema_validator(version: int) -> Validator:
    """The compiled schema of a format version: the latest is `recipe.schema.json`, older ones are frozen."""
    name = "recipe.schema.json" if version == LATEST_VERSION else f"recipe.v{version}.schema.json"
    schema = json.loads(files("brewberrypi").joinpath(f"data/{name}").read_text("utf-8"))
    validator: Validator = fastjsonschema.compile(schema)
    return validator


@dataclass(frozen=True)
class RecipeFormat:
    """The latest format version with the migrators and schemas of every version up to it."""

    latest: int = LATEST_VERSION
    migrators: Mapping[int, Migrator] = field(default_factory=lambda: MIGRATORS)
    validator: Callable[[int], Validator] = schema_validator

    def __post_init__(self) -> None:
        if self.latest < 1 or sorted(self.migrators) != list(range(1, self.latest)):
            missing = sorted(set(range(1, self.latest)) - set(self.migrators))
            raise ValueError(f"recipe format {self.latest}: migrators missing ({missing}) or extra")

    def validate(self, raw: Any, version: int) -> None:
        """Check `raw` against the schema of `version`."""
        self.validator(version)(raw)

    def migrate(self, raw: RecipeData, version: int) -> RecipeData:
        """`raw` of `version` migrated one version at a time to the latest; `raw` itself is not changed."""
        migrated = copy.deepcopy(raw)
        for step in range(version, self.latest):
            try:
                migrated = self.migrators[step](migrated)
            except Exception as error:  # a migrator meets the brewer's data: any failure skips the recipe
                raise MigrationError(f"{_step(step)} failed: {error!r}") from None
            if not isinstance(migrated, dict):
                raise MigrationError(f"{_step(step)} gave no JSON object")
            migrated = with_version(migrated, step + 1)
        return migrated


def _step(version: int) -> str:
    return f"migration from format version {version} to {version + 1}"


def with_version(raw: RecipeData, version: int) -> RecipeData:
    """`raw` with `version`, placed right after `$schema` (first without `$schema`, RCP-008)."""
    result: RecipeData = {}
    if "$schema" in raw:
        result["$schema"] = raw["$schema"]
    result["version"] = version
    result.update((key, value) for key, value in raw.items() if key not in ("$schema", "version"))
    return result


CURRENT_FORMAT = RecipeFormat()
