"""Recipe catalogue: loading and validation (RCP-001..005, spec 07).

Recipes live in a directory, one file per recipe: `<id>.json` holds the recipe with that id, in the format
`data/recipe.schema.json`; its goals are in the section `mashing_process` (spec 7.2, Q-31). Recipes are only
ever read from that directory. When it does not exist, the application creates it with the legacy catalogue
shipped as `data/recipes/` (identical to `docs/specification/data/recipes/`, RCP-002); a recipe the brewer
deletes later is not written again (Q-29). An invalid recipe file is logged and skipped; the other recipes are
loaded (RCP-003); a file in the old format, with `goals` at the top level, is invalid too (Q-31). A directory
that cannot be created or read raises `RecipeDirectoryError` (startup exit code 3).

Each file states its format version, `version` (1 without it, RCP-008). A newer version than the
application knows is invalid (RCP-009); an older one is migrated to the latest version and written back, with
a copy of the original in `.backup/` (RCP-010, `brewberrypi.recipe_migrations`).

`RecipeLibrary` reads the directory again whenever its files change (RCP-006).
"""

import json
import logging
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Literal, Protocol

import fastjsonschema

from brewberrypi.config import RecipesConfig
from brewberrypi.files import make_directory, sync_directory, write_file
from brewberrypi.recipe_migrations import CURRENT_FORMAT, MigrationError, RecipeData, RecipeFormat

log = logging.getLogger(__name__)

GoalType = Literal[
    "PourInBrewingWater",
    "HeatUp",
    "MashIn",
    "Rest",
    "IodineTest",
    "Lautering",
    "HeatUpForHopsCooking",
    "CookWater",
]

RECIPE_ID = re.compile(r"[A-Za-z0-9_-]+")
"""A recipe id, which is also the recipe's file name without `.json`."""

BACKUP_DIRECTORY = ".backup"
"""Subdirectory of the recipe directory with the originals of migrated recipe files (RCP-010)."""


class RecipeError(Exception):
    """A recipe file cannot be read or is invalid; that recipe is skipped (RCP-003)."""


class RecipeDirectoryError(Exception):
    """The recipe directory cannot be created or read; the application refuses to start (RCP-001, Q-29)."""


@dataclass(frozen=True)
class GoalDefinition:
    """One step of a recipe as written in the recipe file (spec 7.3)."""

    type: GoalType
    temperature: float | None = None
    minutes: int | None = None
    name: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Recipe:
    id: str
    name: str
    goals: tuple[GoalDefinition, ...]


@dataclass(frozen=True)
class ParsedRecipe:
    """A recipe read from its file, and what it was migrated from (RCP-010)."""

    recipe: Recipe
    version: int
    """The format version the file is in."""
    migrated: RecipeData | None = None
    """The file's content in the latest format version if the file is in an older one."""


@dataclass(frozen=True)
class RecipeCatalogue:
    """Read-only list of recipes, sorted by id (RCP-002, RCP-004)."""

    recipes: tuple[Recipe, ...]
    source: str
    """The recipe directory the catalogue was read from."""

    def __iter__(self) -> Iterator[Recipe]:
        return iter(self.recipes)

    def __len__(self) -> int:
        return len(self.recipes)

    def get(self, recipe_id: str) -> Recipe | None:
        return next((recipe for recipe in self.recipes if recipe.id == recipe_id), None)

    def summaries(self) -> list[dict[str, str]]:
        """`id` and `name` of each recipe, as published to the clients (RCP-005)."""
        return [{"id": recipe.id, "name": recipe.name} for recipe in self.recipes]


class RecipeSource(Protocol):
    def get(self, recipe_id: str) -> Recipe | None: ...


class RecipeLibrary:
    """The recipes on offer: the recipe directory's catalogue, read again when its files change (RCP-006).

    A loaded process keeps the recipe it was loaded from; only the recipe list and later loads see a change.
    """

    def __init__(
        self,
        catalogue: RecipeCatalogue,
        directory: Path | None = None,
        signature: tuple[tuple[str, int, int, int], ...] = (),
    ) -> None:
        """`directory` is watched by `refresh()`, starting from its `signature` taken before `catalogue` was
        read; without a directory the catalogue never changes."""
        self._catalogue = catalogue
        self._directory = directory
        self._signature = signature
        self._failing = False

    @classmethod
    def open(cls, config: RecipesConfig) -> "RecipeLibrary":
        """Create the recipe directory if it is missing (Q-29) and read it (RCP-001)."""
        directory = config.directory
        prepare_recipe_directory(directory)
        try:
            signature = _signature(directory)
            catalogue = read_catalogue(directory, str(directory))
        except OSError as error:
            message = f"{directory}: cannot read recipe directory: {error.strerror}"
            raise RecipeDirectoryError(message) from None
        log.info("%d recipes read from %s", len(catalogue), directory)
        return cls(catalogue, directory, signature)

    @property
    def catalogue(self) -> RecipeCatalogue:
        return self._catalogue

    def get(self, recipe_id: str) -> Recipe | None:
        return self._catalogue.get(recipe_id)

    def refresh(self) -> bool:
        """Read the directory again if a recipe file was added, changed or removed; `True` if it was.

        A directory that disappeared is created again like at startup. While the directory cannot be read,
        the last catalogue stays (logged once)."""
        directory = self._directory
        if directory is None:
            return False
        try:
            prepare_recipe_directory(directory)
            signature = _signature(directory)
            if signature == self._signature:
                return False
            catalogue = read_catalogue(directory, str(directory))
        except (OSError, RecipeDirectoryError) as error:
            if not self._failing:
                log.error("cannot read the recipe directory, the recipe list stays as it is: %s", error)
                self._failing = True
            return False
        self._failing = False
        self._signature = signature
        changed = catalogue.recipes != self._catalogue.recipes
        self._catalogue = catalogue
        if changed:
            log.info("recipe directory changed, recipes: %s", ", ".join(recipe.id for recipe in catalogue))
        return changed


def load_recipes(config: RecipesConfig) -> RecipeCatalogue:
    """The catalogue of the recipe directory, created first if it is missing (RCP-001, Q-29)."""
    return RecipeLibrary.open(config).catalogue


def prepare_recipe_directory(directory: Path) -> bool:
    """Create a missing recipe directory with the shipped recipes; `True` if it was created (Q-29).

    The directory is filled under a temporary name and then renamed, so it never appears half filled. An
    existing directory is left alone: recipes the brewer deleted are not written again."""
    if directory.is_dir():
        return False
    if directory.exists() or directory.is_symlink():
        raise RecipeDirectoryError(f"{directory}: recipe directory not found (not a directory)")
    staging = directory.with_name(f".{directory.name}.new")
    try:
        directory.parent.mkdir(parents=True, exist_ok=True)
        if staging.exists():
            shutil.rmtree(staging)  # left over from an interrupted start
        make_directory(staging)
        shipped = sorted(
            (file for file in _BUILT_IN_DIRECTORY.iterdir() if file.name.endswith(".json")),
            key=lambda file: file.name,
        )
        for file in shipped:
            write_file(staging / file.name, file.read_text(encoding="utf-8"))
        os.replace(staging, directory)
        sync_directory(directory.parent)
    except OSError as error:
        raise RecipeDirectoryError(f"{directory}: cannot create recipe directory: {error.strerror}") from None
    log.info("recipe directory %s created with the %d shipped recipes", directory, len(shipped))
    return True


def _signature(directory: Path) -> tuple[tuple[str, int, int, int], ...]:
    """Name, inode, size and modification time of each recipe file: changes when any recipe file does."""
    entries = []
    with os.scandir(directory) as scan:
        for entry in scan:
            if _is_recipe_file_name(entry.name):
                stat = entry.stat()
                entries.append((entry.name, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    return tuple(sorted(entries))


def _is_recipe_file_name(name: str) -> bool:
    """Files ending in `.json`; hidden files (such as `._X.json` written by macOS) are not recipes."""
    return name.endswith(".json") and not name.startswith(".")


def read_catalogue(
    directory: Traversable, source: str, recipe_format: RecipeFormat = CURRENT_FORMAT
) -> RecipeCatalogue:
    """Each `<id>.json` file of the directory, sorted by id; invalid ones are logged and skipped (RCP-003).

    A file in an older format version is written back in the latest one (RCP-010)."""
    recipes: list[Recipe] = []
    for file in directory.iterdir():
        if not _is_recipe_file_name(file.name) or not file.is_file():
            continue  # other files, hidden files and subdirectories are not recipes
        location = f"{source}/{file.name}"
        try:
            try:
                text = file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as error:
                raise RecipeError(f"{location}: cannot read recipe file: {error}") from None
            parsed = read_recipe(file.name.removesuffix(".json"), text, location, recipe_format)
            if parsed.migrated is not None and isinstance(file, Path):
                _write_migrated(file, text, parsed, location)
            recipes.append(parsed.recipe)
        except RecipeError as error:
            log.error("invalid recipe file, recipe skipped: %s", error)
    recipes.sort(key=lambda recipe: recipe.id)
    return RecipeCatalogue(recipes=tuple(recipes), source=source)


def _write_migrated(file: Path, text: str, parsed: ParsedRecipe, location: str) -> None:
    """Keep the original in `.backup/<id>.v<N>.json`, then write the migrated recipe to the file (RCP-010).

    If either cannot be written, the file stays as it is and the migrated recipe is used all the same."""
    migrated = parsed.migrated
    assert migrated is not None
    backup = file.parent / BACKUP_DIRECTORY / f"{file.stem}.v{parsed.version}.json"
    try:
        if not backup.parent.is_dir():
            make_directory(backup.parent)
        write_file(backup, text)
        write_file(file, json.dumps(migrated, indent=2, ensure_ascii=False) + "\n")
    except OSError as error:
        log.warning(
            "%s: recipe %r migrated from format version %d, but the file cannot be written (%s); "
            "the migrated recipe is used, the file stays as it is",
            location,
            parsed.recipe.id,
            parsed.version,
            error,
        )
        return
    log.info(
        "%s: recipe %r migrated from format version %d to %d, original kept as %s",
        location,
        parsed.recipe.id,
        parsed.version,
        migrated["version"],
        backup,
    )


def parse_recipe(recipe_id: str, text: str, source: str) -> Recipe:
    """Parse and validate one recipe file; errors name the file, recipe id and goal (RCP-003)."""
    return read_recipe(recipe_id, text, source).recipe


def read_recipe(
    recipe_id: str, text: str, source: str, recipe_format: RecipeFormat = CURRENT_FORMAT
) -> ParsedRecipe:
    """Parse, validate and, if it is in an older format version, migrate a recipe file (RCP-003, RCP-010)."""
    if RECIPE_ID.fullmatch(recipe_id) is None:
        raise RecipeError(f"{source}: {recipe_id!r} is not a recipe id (letters, digits, _ and - only)")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise RecipeError(f"{source}:{error.lineno}:{error.colno}: invalid JSON: {error.msg}") from None
    recipe = repr(recipe_id)
    if isinstance(raw, dict) and "goals" in raw and "mashing_process" not in raw:  # recipe format before Q-31
        raise RecipeError(f"{source}: recipe {recipe}: old recipe format: move goals into mashing_process")
    version = _format_version(raw, recipe, source, recipe_format.latest)
    try:
        recipe_format.validate(raw, version)
    except fastjsonschema.JsonSchemaValueException as error:
        raise RecipeError(f"{source}: {_describe(raw, recipe, error.message, error.name)}") from None
    migrated = None
    if version < recipe_format.latest:
        try:
            migrated = recipe_format.migrate(raw, version)
            recipe_format.validate(migrated, recipe_format.latest)
        except MigrationError as error:
            raise RecipeError(f"{source}: recipe {recipe}: {error}") from None
        except fastjsonschema.JsonSchemaValueException as error:
            where = _describe(migrated, recipe, error.message, error.name)
            step = f"migration from format version {version} to {recipe_format.latest}"
            raise RecipeError(f"{source}: {step} gave an invalid recipe: {where}") from None
        raw = migrated

    goals = []
    for goal_index, raw_goal in enumerate(raw["mashing_process"]["goals"]):
        # The schema's dependentRequired (draft 2019-09) is not evaluated by fastjsonschema.
        if raw_goal["type"] == "Rest" and ("name" in raw_goal) != ("description" in raw_goal):
            where = _goal_location(recipe, goal_index, raw_goal)
            raise RecipeError(f"{source}: {where}: a Rest needs both name and description, or neither")
        goals.append(GoalDefinition(**raw_goal))
    return ParsedRecipe(Recipe(id=recipe_id, name=raw["name"], goals=tuple(goals)), version, migrated)


def _format_version(raw: Any, recipe: str, source: str, latest: int) -> int:
    """The file's format version, 1 without `version` (RCP-008); invalid or newer: `RecipeError` (RCP-009)."""
    if not isinstance(raw, dict) or "version" not in raw:
        return 1
    version = raw["version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        found = json.dumps(version)
        raise RecipeError(f"{source}: recipe {recipe}: version must be an integer ≥ 1, not {found}")
    if version > latest:
        raise RecipeError(
            f"{source}: recipe {recipe}: format version {version} is newer than this application supports "
            f"(latest {latest}): update the application"
        )
    return version


def _goal_location(recipe: str, goal_index: int, raw_goal: Any) -> str:
    """`recipe 'BENIWEISS', goal 5 (index 4, Rest)`: goals numbered from 1 as in spec 7.4, plus the JSON
    index."""
    goal_type = raw_goal.get("type") if isinstance(raw_goal, dict) else None
    kind = f", {goal_type}" if isinstance(goal_type, str) else ""
    return f"recipe {recipe}, goal {goal_index + 1} (index {goal_index}{kind})"


_GOAL_PATH = re.compile(r"^data\.mashing_process\.goals\[(\d+)\]")


def _describe(raw: Any, recipe: str, message: str, name: str) -> str:
    """Turn a schema error into a message with recipe id and goal number instead of a JSON path."""
    problem = message.removeprefix(name).strip()
    match = _GOAL_PATH.match(name)
    if match is None:
        field = name.removeprefix("data").removeprefix(".")
        return f"recipe {recipe}: {field} {problem}" if field else f"recipe {recipe}: {problem}"
    goal_index = int(match.group(1))
    rest = name[match.end() :].removeprefix(".")
    field = f"{rest} " if rest else ""
    raw_goal = raw["mashing_process"]["goals"][goal_index]
    return f"{_goal_location(recipe, goal_index, raw_goal)}: {field}{problem}"


_BUILT_IN_DIRECTORY = files("brewberrypi").joinpath("data/recipes")
