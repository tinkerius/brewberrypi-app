"""M1: recipe catalogue, one file per recipe (RCP-001..012, AC-080, AC-081, AC-084, AC-085, AC-094..101,
AC-118, AC-119, Q-27, Q-29, Q-33, Q-34, Q-37)."""

import json
import logging
import re
import stat
from pathlib import Path
from typing import Any

import fastjsonschema
import pytest

from brewberrypi import recipes
from brewberrypi.config import RecipesConfig
from brewberrypi.recipe_migrations import LATEST_VERSION, MIGRATORS, RecipeFormat, with_version
from brewberrypi.recipes import (
    BACKUP_DIRECTORY,
    GoalDefinition,
    RecipeDirectoryError,
    RecipeError,
    RecipeLibrary,
    load_recipes,
    parse_recipe,
    read_catalogue,
    read_recipe,
)
from recipe_fixtures import TEST_RECIPES

SPEC_DATA = Path(__file__).parents[1] / "docs" / "specification" / "data"
PACKAGE_DATA = Path(__file__).parents[1] / "src" / "brewberrypi" / "data"
SORTED_IDS = ["BENIWEISS", "WaterBoil"]
MASH_IN = {"goals": [{"type": "MashIn"}]}
"""A recipe file's `mashing_process` with a single goal."""


def write_recipe(directory: Path, goals: list[dict[str, Any]], recipe_id: str = "MyRecipe") -> Path:
    directory.mkdir(exist_ok=True)
    file = directory / f"{recipe_id}.json"
    file.write_text(json.dumps({"name": f"Recipe {recipe_id}", "mashing_process": {"goals": goals}}))
    return file


def configured(directory: Path) -> RecipesConfig:
    return RecipesConfig(directory=directory, check_milliseconds=1000)


def test_AC_080_shipped_recipe_files_equal_the_spec_catalogue() -> None:
    spec_files = sorted(file.name for file in (SPEC_DATA / "recipes").iterdir())
    assert spec_files == [f"{recipe_id}.json" for recipe_id in SORTED_IDS]
    for name in spec_files:
        assert (PACKAGE_DATA / "recipes" / name).read_bytes() == (SPEC_DATA / "recipes" / name).read_bytes()
    assert sorted(file.name for file in (PACKAGE_DATA / "recipes").iterdir()) == spec_files
    catalogue = load_recipes(configured(SPEC_DATA / "recipes"))
    assert [recipe.id for recipe in catalogue] == SORTED_IDS
    assert [summary["id"] for summary in catalogue.summaries()] == SORTED_IDS


@pytest.mark.parametrize("name", ["recipe.schema.json", "config.schema.json", "messages.schema.json"])
def test_shipped_schemas_equal_the_spec(name: str) -> None:
    assert (PACKAGE_DATA / name).read_bytes() == (SPEC_DATA / name).read_bytes()


def test_AC_081_rest_without_minutes_is_skipped_naming_file_and_goal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    bad = write_recipe(
        directory, [{"type": "HeatUp", "temperature": 62}, {"type": "Rest", "temperature": 62}]
    )
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="Good")
    with caplog.at_level(logging.ERROR):
        catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["Good"]  # the other recipes are loaded
    [record] = caplog.records
    message = record.getMessage()
    assert message.startswith("invalid recipe file, recipe skipped: ")
    assert str(bad) in message
    assert "recipe 'MyRecipe', goal 2 (index 1, Rest)" in message
    assert "minutes" in message


def test_AC_094_old_recipe_format_is_skipped_naming_file_and_new_section(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="Good")
    old = directory / "Old.json"
    old.write_text(json.dumps({"name": "Old", "goals": [{"type": "MashIn"}]}))
    with caplog.at_level(logging.ERROR):
        catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["Good"]  # the other recipes are loaded
    [record] = caplog.records
    message = record.getMessage()
    assert message.startswith("invalid recipe file, recipe skipped: ")
    assert f"{old}: recipe 'Old': old recipe format: move goals into mashing_process" in message


def test_spec_recipe_files_are_read_with_verbatim_texts() -> None:
    catalogue = load_recipes(configured(SPEC_DATA / "recipes"))
    assert catalogue.source == str(SPEC_DATA / "recipes")
    assert [recipe.id for recipe in catalogue] == SORTED_IDS
    beniweiss = catalogue.get("BENIWEISS")
    assert beniweiss is not None
    assert beniweiss.name == "München 12.6 - SUD2:BENIWEISS"
    assert len(beniweiss.goals) == 11
    assert beniweiss.goals[6] == GoalDefinition(
        type="Rest",
        temperature=65,
        minutes=30,
        name="Maltoserastr",  # legacy typo kept (Q-07)
        description="Maltoserastr @65 for 30min",
    )
    assert beniweiss.goals[9] == GoalDefinition(type="IodineTest", temperature=73, minutes=10)
    water_boil = catalogue.get("WaterBoil")
    assert water_boil is not None
    assert water_boil.goals[1] == GoalDefinition(
        type="CookWater", minutes=20, name="Cooking Water", description="Cooking water for 20min"
    )


def test_Q27_file_name_is_the_id_and_the_list_is_sorted_by_it(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    for recipe_id in ["b", "A", "a-2", "B_1"]:
        write_recipe(directory, [{"type": "MashIn"}], recipe_id=recipe_id)
    catalogue = load_recipes(configured(directory))
    assert catalogue.summaries() == [
        {"id": "A", "name": "Recipe A"},
        {"id": "B_1", "name": "Recipe B_1"},
        {"id": "a-2", "name": "Recipe a-2"},
        {"id": "b", "name": "Recipe b"},
    ]


def test_Q27_other_files_and_subdirectories_are_ignored(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}])
    (directory / "README.md").write_text("notes")
    (directory / "MyRecipe.json~").write_text("editor backup")
    (directory / "old.json").mkdir()
    write_recipe(directory / "archive", [{"type": "MashIn"}], recipe_id="Archived")
    with caplog.at_level(logging.ERROR):
        catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["MyRecipe"]
    assert caplog.records == []


def test_Q27_a_file_name_that_is_no_recipe_id_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="my recipe")
    with caplog.at_level(logging.ERROR):
        assert len(load_recipes(configured(directory))) == 0
    assert "'my recipe' is not a recipe id" in caplog.text


def test_an_id_inside_the_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(RecipeError, match=re.escape("must not contain {'id'}")):
        parse_recipe("R", json.dumps({"id": "R", "name": "r", "mashing_process": MASH_IN}), "R.json")


def test_AC_084_a_missing_recipe_directory_is_created_with_the_shipped_recipes(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    catalogue = load_recipes(configured(directory))
    assert catalogue.source == str(directory)
    assert [recipe.id for recipe in catalogue] == SORTED_IDS
    assert sorted(file.name for file in directory.iterdir()) == [f"{id}.json" for id in SORTED_IDS]
    for file in directory.iterdir():
        assert file.read_bytes() == (SPEC_DATA / "recipes" / file.name).read_bytes()
        assert stat.S_IMODE(file.stat().st_mode) == 0o664  # the brewer edits them as another user
    assert stat.S_IMODE(directory.stat().st_mode) & 0o777 == 0o775
    assert sorted(path.name for path in tmp_path.iterdir()) == ["recipes"]  # no staging left


def test_AC_084_deleted_recipes_are_not_written_again(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    load_recipes(configured(directory))
    (directory / "BENIWEISS.json").unlink()
    catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["WaterBoil"]
    assert not (directory / "BENIWEISS.json").exists()
    for file in list(directory.iterdir()):
        file.unlink()
    assert len(load_recipes(configured(directory))) == 0  # an empty directory stays empty


def test_AC_084_set_group_id_of_the_parent_is_kept(tmp_path: Path) -> None:
    tmp_path.chmod(0o2775)
    load_recipes(configured(tmp_path / "recipes"))
    assert stat.S_IMODE((tmp_path / "recipes").stat().st_mode) == 0o2775


def test_a_staging_directory_left_over_from_an_interrupted_start_is_replaced(tmp_path: Path) -> None:
    staging = tmp_path / ".recipes.new"
    staging.mkdir()
    (staging / "BENIWEISS.json").write_text("half written")
    catalogue = load_recipes(configured(tmp_path / "recipes"))
    assert [recipe.id for recipe in catalogue] == SORTED_IDS
    assert not staging.exists()


def test_an_existing_recipe_directory_is_read_as_it_is(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}])
    catalogue = load_recipes(configured(directory))
    assert catalogue.source == str(directory)
    assert [recipe.id for recipe in catalogue] == ["MyRecipe"]


def test_an_empty_recipe_directory_gives_no_recipes(tmp_path: Path) -> None:
    (tmp_path / "recipes").mkdir()
    assert len(load_recipes(configured(tmp_path / "recipes"))) == 0


def test_hidden_files_are_no_recipes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}])
    (directory / "._MyRecipe.json").write_bytes(b"\x00\x05\x16\x07")  # macOS metadata over SMB
    (directory / ".MyRecipe.json.tmp").write_text("{")
    with caplog.at_level(logging.ERROR):
        assert [recipe.id for recipe in load_recipes(configured(directory))] == ["MyRecipe"]
    assert caplog.records == []


def test_a_recipe_directory_that_cannot_be_created_refuses_to_start(tmp_path: Path) -> None:
    (tmp_path / "file").write_text("")
    with pytest.raises(RecipeDirectoryError, match="cannot create recipe directory"):
        load_recipes(configured(tmp_path / "file" / "recipes"))


def test_a_file_instead_of_a_directory_refuses_to_start(tmp_path: Path) -> None:
    (tmp_path / "recipes.json").write_text("{}")
    with pytest.raises(RecipeDirectoryError, match="recipe directory not found"):
        load_recipes(configured(tmp_path / "recipes.json"))


# --- RCP-006: the recipe directory is read again when it changes ---


def open_library(directory: Path) -> RecipeLibrary:
    directory.mkdir(exist_ok=True)
    return RecipeLibrary.open(configured(directory))


def ids(library: RecipeLibrary) -> list[str]:
    return [recipe.id for recipe in library.catalogue]


def test_AC_085_added_changed_and_removed_recipe_files_are_read_again(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    library = open_library(directory)
    assert not library.refresh()
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="HOLD30")
    assert library.refresh()
    assert ids(library) == ["HOLD30"]
    assert not library.refresh()  # nothing changed since
    (directory / "HOLD30.json").write_text(json.dumps({"name": "Hold 30 °C", "mashing_process": MASH_IN}))
    assert library.refresh()
    recipe = library.get("HOLD30")
    assert recipe is not None
    assert recipe.name == "Hold 30 °C"
    (directory / "HOLD30.json").unlink()
    assert library.refresh()
    assert ids(library) == []


def test_AC_085_an_invalid_new_file_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "recipes"
    library = open_library(directory)
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="Good")
    (directory / "Bad.json").write_text("{")
    with caplog.at_level(logging.ERROR):
        assert library.refresh()
    assert ids(library) == ["Good"]
    assert "Bad.json:1:2: invalid JSON" in caplog.text


def test_a_file_saved_again_without_change_is_no_change(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    file = write_recipe(directory, [{"type": "MashIn"}])
    library = open_library(directory)
    file.write_text(file.read_text())
    assert not library.refresh()
    assert ids(library) == ["MyRecipe"]


def test_other_files_do_not_count_as_a_change(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    library = open_library(directory)
    (directory / "notes.txt").write_text("notes")
    (directory / "._X.json").write_text("metadata")
    assert not library.refresh()


def test_a_removed_recipe_directory_is_created_again(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    library = open_library(directory)
    directory.rmdir()
    assert library.refresh()
    assert ids(library) == SORTED_IDS


def test_an_unreadable_directory_keeps_the_recipe_list_and_logs_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}])
    library = open_library(directory)

    def unreadable(directory: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("brewberrypi.recipes._signature", unreadable)
    with caplog.at_level(logging.ERROR):
        assert not library.refresh()
        assert not library.refresh()
    assert ids(library) == ["MyRecipe"]
    assert len([record for record in caplog.records if record.levelno >= logging.ERROR]) == 1
    assert "the recipe list stays as it is" in caplog.text
    monkeypatch.undo()
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="New")
    assert library.refresh()
    assert ids(library) == ["MyRecipe", "New"]


def test_a_library_without_directory_never_changes() -> None:
    catalogue = load_recipes(configured(SPEC_DATA / "recipes"))
    library = RecipeLibrary(catalogue)
    assert not library.refresh()
    assert library.catalogue is catalogue


def test_unreadable_recipe_file_is_skipped(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "Latin1.json").write_bytes('{"name": "Eiweißrast"}'.encode("latin-1"))
    with caplog.at_level(logging.ERROR):
        assert len(load_recipes(configured(directory))) == 0
    assert "Latin1.json: cannot read recipe file" in caplog.text


def test_invalid_json_names_file_line_and_column() -> None:
    with pytest.raises(RecipeError, match=r"^my.json:2:\d+: invalid JSON"):
        parse_recipe("my", '{"name": "x",\n  oops }', "my.json")


@pytest.mark.parametrize(
    ("goal", "expected"),
    [
        ({"type": "Boil"}, "goal 1 (index 0, Boil): type must be one of"),
        ({"type": "HeatUp"}, "goal 1 (index 0, HeatUp): must contain ['temperature']"),
        ({"type": "HeatUp", "temperature": 111}, "temperature must be smaller than or equal to 110"),
        ({"type": "Rest", "temperature": 62, "minutes": -1}, "minutes must be bigger than or equal to 0"),
        ({"type": "Rest", "temperature": 62, "minutes": 1.5}, "minutes must be integer"),
        ({"type": "MashIn", "temperature": 62}, "must not contain {'temperature'}"),
        ({"type": "CookWater", "minutes": 20}, "goal 1 (index 0, CookWater): must contain ["),
        ({"type": "Rest", "temperature": 62, "minutes": 1, "name": "Rast"}, "both name and description"),
    ],
)
def test_invalid_goal_is_reported_naming_recipe_and_goal(goal: dict[str, Any], expected: str) -> None:
    with pytest.raises(RecipeError) as error:
        parse_recipe(
            "MyRecipe", json.dumps({"name": "x", "mashing_process": {"goals": [goal]}}), "MyRecipe.json"
        )
    assert str(error.value).startswith("MyRecipe.json: recipe 'MyRecipe', goal 1")
    assert expected in str(error.value)


def test_rest_with_default_texts_is_valid() -> None:
    recipe = parse_recipe(
        "R",
        json.dumps(
            {"name": "x", "mashing_process": {"goals": [{"type": "Rest", "temperature": 62, "minutes": 10}]}}
        ),
        "R.json",
    )
    assert recipe.goals[0] == GoalDefinition(type="Rest", temperature=62, minutes=10)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({}, "recipe 'R': must contain ['mashing_process', 'name']"),
        ([], "recipe 'R': must be object"),
        ({"name": "", "mashing_process": MASH_IN}, "recipe 'R': name must be longer than or equal to 1"),
        ({"name": "x", "mashing_process": {}}, "recipe 'R': mashing_process must contain ['goals']"),
        (
            {"name": "x", "mashing_process": {"goals": []}},
            "recipe 'R': mashing_process.goals must contain at least 1",
        ),
        (
            {"name": "x", "mashing_process": {**MASH_IN, "x": 1}},
            "recipe 'R': mashing_process must not contain {'x'}",
        ),
        ({"name": "x", "mashing_process": MASH_IN, "goals": []}, "recipe 'R': must not contain {'goals'}"),
    ],
)
def test_invalid_recipe_structure_is_reported(content: Any, expected: str) -> None:
    with pytest.raises(RecipeError) as error:
        parse_recipe("R", json.dumps(content), "R.json")
    assert expected in str(error.value)


# Recipe file format version and migration (RCP-008..011, Q-33, spec 7.2.1)


def recipe_file(directory: Path, content: Any, recipe_id: str = "MyRecipe") -> Path:
    directory.mkdir(exist_ok=True)
    file = directory / f"{recipe_id}.json"
    file.write_text(json.dumps(content))
    return file


def _schema_of_version(version: int) -> Any:
    """A test format: version 2 renames `name` to `title`, 3 adds `notes`, 4 renames `title` back."""
    schema = json.loads((SPEC_DATA / "recipe.schema.json").read_text())
    schema["properties"]["version"] = {"const": version}
    if version in (2, 3):
        schema["properties"]["title"] = schema["properties"].pop("name")
        schema["required"] = ["title", "mashing_process"]
    if version >= 3:
        schema["properties"]["notes"] = {"type": "array", "items": {"type": "string"}}
        schema["required"] = [*schema["required"], "notes"]
    return schema


def four_versions(calls: list[str], broken: int | None = None) -> RecipeFormat:
    """A recipe format with the latest version 4 and migrators 1 → 2 → 3 → 4 that record their calls."""

    def rename(old: str, new: str, step: str) -> Any:
        def migrator(raw: dict[str, Any]) -> dict[str, Any]:
            calls.append(step)
            raw[new] = raw.pop(old)
            return raw

        return migrator

    def add_notes(raw: dict[str, Any]) -> dict[str, Any]:
        calls.append("2→3")
        raw["notes"] = ["migrated"]
        return raw

    def fail(raw: dict[str, Any]) -> dict[str, Any]:
        raise KeyError("boom")

    migrators: dict[int, Any] = {
        1: rename("name", "title", "1→2"),
        2: add_notes,
        3: rename("title", "name", "3→4"),
    }
    if broken is not None:
        migrators[broken] = fail
    validators = {version: fastjsonschema.compile(_schema_of_version(version)) for version in range(1, 5)}
    return RecipeFormat(latest=4, migrators=migrators, validator=validators.__getitem__)


def test_AC_095_a_file_without_version_is_version_1(tmp_path: Path) -> None:
    assert read_recipe("R", json.dumps({"name": "x", "mashing_process": MASH_IN}), "R.json").version == 1
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}])
    load_recipes(configured(directory))
    assert (directory / BACKUP_DIRECTORY / "MyRecipe.v1.json").exists()  # migrated like version 1 (AC-101)


def test_AC_095_a_file_in_the_latest_version_is_not_written(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    content = {"version": LATEST_VERSION, "name": "x", "mashing_process": MASH_IN}
    file = recipe_file(directory, content)
    before = (file.read_bytes(), file.stat().st_mtime_ns)
    catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["MyRecipe"]
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before
    assert not (directory / BACKUP_DIRECTORY).exists()


@pytest.mark.parametrize("with_version_1", [True, False])
def test_AC_101_a_version_1_file_is_written_back_in_the_latest_version(
    tmp_path: Path, with_version_1: bool
) -> None:
    directory = tmp_path / "recipes"
    original = {
        "$schema": "../recipe.schema.json",
        **({"version": 1} if with_version_1 else {}),
        "name": "Old",
        "mashing_process": {"goals": [{"type": "HeatUp", "temperature": 62}, {"type": "MashIn"}]},
    }
    file = recipe_file(directory, original, "Old")
    original_text = file.read_text()
    [recipe] = load_recipes(configured(directory))
    assert (recipe.name, [goal.type for goal in recipe.goals]) == ("Old", ["HeatUp", "MashIn"])
    written = json.loads(file.read_text())
    assert list(written)[:2] == ["$schema", "version"]
    assert written == {**original, "version": 3}
    assert (directory / BACKUP_DIRECTORY / "Old.v1.json").read_text() == original_text


def test_AC_118_a_version_2_file_is_written_back_as_version_3(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    original = {
        "$schema": "../recipe.schema.json",
        "version": 2,
        "name": "Old",
        "mashing_process": {"goals": [{"type": "HeatUp", "temperature": 62}, {"type": "MashIn"}]},
    }
    file = recipe_file(directory, original, "Old")
    original_text = file.read_text()
    [recipe] = load_recipes(configured(directory))
    assert (recipe.name, [goal.type for goal in recipe.goals]) == ("Old", ["HeatUp", "MashIn"])
    written = json.loads(file.read_text())
    assert list(written)[:2] == ["$schema", "version"]
    assert written == {**original, "version": 3}
    assert (directory / BACKUP_DIRECTORY / "Old.v2.json").read_text() == original_text


MASHING = {"MaltoseRest": {"temperature": 63, "minutes": 45}, "MashOut": {"temperature": 78, "minutes": 10}}
"""A valid mashing plan (RCP-012)."""


def test_AC_119_a_valid_mashing_plan_is_accepted_and_not_used(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    goals = {"goals": [{"type": "HeatUp", "temperature": 62}, {"type": "MashIn"}]}
    file = recipe_file(
        directory, {"version": 3, "name": "Plan", "mashing_process": goals, "mashing": MASHING}, "Plan"
    )
    recipe_file(directory, {"version": 3, "name": "Plan", "mashing_process": goals}, "NoPlan")
    before = (file.read_bytes(), file.stat().st_mtime_ns)
    catalogue = {recipe.id: recipe for recipe in load_recipes(configured(directory))}
    assert (catalogue["Plan"].name, catalogue["Plan"].goals) == (
        catalogue["NoPlan"].name,
        catalogue["NoPlan"].goals,
    )
    assert (file.read_bytes(), file.stat().st_mtime_ns) == before
    assert not (directory / BACKUP_DIRECTORY).exists()


@pytest.mark.parametrize(
    "mashing",
    [
        [],
        "MaltoseRest",
        {"MainRest": {"temperature": 63, "minutes": 45}},
        {"MaltoseRest": {"minutes": 45}},
        {"MaltoseRest": {"temperature": 63}},
        {"MaltoseRest": {"temperature": 111, "minutes": 45}},
        {"MaltoseRest": {"temperature": "63", "minutes": 45}},
        {"MaltoseRest": {"temperature": 63, "minutes": 4.5}},
        {"MaltoseRest": {"temperature": 63, "minutes": -1}},
        {"MaltoseRest": {"temperature": 63, "minutes": 45, "name": "Maltoserast"}},
    ],
)
def test_AC_119_an_invalid_mashing_plan_skips_the_recipe(tmp_path: Path, mashing: Any) -> None:
    directory = tmp_path / "recipes"
    recipe_file(
        directory, {"version": 3, "name": "Bad", "mashing_process": MASH_IN, "mashing": mashing}, "Bad"
    )
    recipe_file(
        directory, {"version": 3, "name": "Good", "mashing_process": MASH_IN, "mashing": MASHING}, "Good"
    )
    assert [recipe.id for recipe in load_recipes(configured(directory))] == ["Good"]


def test_AC_100_a_recipe_without_goals_is_listed(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    recipe_file(
        directory, {"version": LATEST_VERSION, "name": "Draft", "mashing_process": {"goals": []}}, "Draft"
    )
    recipe_file(directory, {"version": LATEST_VERSION, "name": "No section"}, "NoSection")
    write_recipe(directory, [], recipe_id="OldEmpty")  # version 1 needs a goal: skipped before migrating
    catalogue = load_recipes(configured(directory))
    assert [(recipe.id, recipe.goals) for recipe in catalogue] == [("Draft", ())]


def test_RCP_008_the_shipped_recipes_are_in_the_latest_version() -> None:
    assert LATEST_VERSION == 3
    for file in (SPEC_DATA / "recipes").iterdir():
        assert json.loads(file.read_text())["version"] == LATEST_VERSION
    for file in TEST_RECIPES.glob("*.json"):  # else the tests would migrate these files in place
        assert json.loads(file.read_text())["version"] == LATEST_VERSION
    assert (
        read_recipe("R", json.dumps({"version": 3, "name": "x", "mashing_process": MASH_IN}), "R").migrated
        is None
    )


def test_AC_096_a_newer_version_is_skipped_naming_both_versions(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    write_recipe(directory, [{"type": "MashIn"}], recipe_id="Good")
    newer_version = LATEST_VERSION + 1
    content = {"version": newer_version, "name": "New", "mashing_process": MASH_IN}
    newer = recipe_file(directory, content, "New")
    before = newer.read_bytes()
    with caplog.at_level(logging.ERROR):
        catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["Good"]
    [record] = caplog.records
    assert record.getMessage() == (
        f"invalid recipe file, recipe skipped: {newer}: recipe 'New': format version {newer_version} is "
        f"newer than this application supports (latest {LATEST_VERSION}): update the application"
    )
    assert newer.read_bytes() == before


def test_AC_097_an_older_version_is_migrated_step_by_step_and_written_back(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    original = {"$schema": "../recipe.schema.json", "name": "Old", "mashing_process": MASH_IN}
    file = recipe_file(directory, original, "Old")
    original_text = file.read_text()
    calls: list[str] = []
    recipe_format = four_versions(calls)
    with caplog.at_level(logging.INFO):
        catalogue = read_catalogue(directory, str(directory), recipe_format)
    assert calls == ["1→2", "2→3", "3→4"]
    assert [(recipe.id, recipe.name) for recipe in catalogue] == [("Old", "Old")]
    assert json.loads(file.read_text()) == {
        "$schema": "../recipe.schema.json",
        "version": 4,
        "mashing_process": MASH_IN,
        "notes": ["migrated"],
        "name": "Old",
    }
    assert list(json.loads(file.read_text()))[:2] == ["$schema", "version"]  # RCP-008: version after $schema
    backup = directory / BACKUP_DIRECTORY / "Old.v1.json"
    assert backup.read_text() == original_text
    assert stat.S_IMODE(file.stat().st_mode) == 0o664  # NFR-023
    assert stat.S_IMODE(backup.stat().st_mode) == 0o664
    assert stat.S_IMODE(backup.parent.stat().st_mode) == 0o775
    assert any("migrated from format version 1 to 4" in record.getMessage() for record in caplog.records)

    calls.clear()
    migrated_text = file.read_text()
    again = read_catalogue(directory, str(directory), recipe_format)
    assert calls == []  # read again without migration
    assert file.read_text() == migrated_text
    assert again.recipes == catalogue.recipes


def test_AC_097_a_file_in_a_middle_version_runs_only_the_later_migrators(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    file = recipe_file(
        directory, {"version": 3, "title": "Mid", "notes": [], "mashing_process": MASH_IN}, "Mid"
    )
    calls: list[str] = []
    catalogue = read_catalogue(directory, str(directory), four_versions(calls))
    assert calls == ["3→4"]
    assert [recipe.name for recipe in catalogue] == ["Mid"]
    assert json.loads(file.read_text())["version"] == 4
    assert (directory / BACKUP_DIRECTORY / "Mid.v3.json").exists()


def test_AC_097_the_library_reads_the_rewritten_file_without_a_change(tmp_path: Path) -> None:
    directory = tmp_path / "recipes"
    recipe_file(directory, {"version": LATEST_VERSION, "name": "x", "mashing_process": MASH_IN})
    library = RecipeLibrary.open(configured(directory))
    assert library.refresh() is False  # nothing was migrated, nothing changed
    hidden = directory / BACKUP_DIRECTORY
    hidden.mkdir()
    (hidden / "Other.v1.json").write_text("{}")
    assert library.refresh() is False  # .backup is no recipe and no change (RCP-001)
    assert [recipe.id for recipe in library.catalogue] == ["MyRecipe"]


@pytest.mark.parametrize("broken", [1, 2, 3])
def test_AC_098_a_failing_migrator_skips_the_recipe_and_leaves_the_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, broken: int
) -> None:
    directory = tmp_path / "recipes"
    file = recipe_file(directory, {"name": "Old", "mashing_process": MASH_IN}, "Old")
    before = file.read_bytes()
    with caplog.at_level(logging.ERROR):
        catalogue = read_catalogue(directory, str(directory), four_versions([], broken=broken))
    assert len(catalogue) == 0
    [record] = caplog.records
    assert record.getMessage() == (
        f"invalid recipe file, recipe skipped: {file}: recipe 'Old': migration from format version {broken} "
        f"to {broken + 1} failed: KeyError('boom')"
    )
    assert file.read_bytes() == before
    assert not (directory / BACKUP_DIRECTORY).exists()


def test_AC_098_an_invalid_migrated_recipe_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    file = recipe_file(directory, {"name": "Old", "mashing_process": MASH_IN}, "Old")
    before = file.read_bytes()
    calls: list[str] = []
    recipe_format = four_versions(calls)
    bad = dict(recipe_format.migrators)
    bad[3] = lambda raw: {"name": raw["title"], "mashing_process": raw["mashing_process"]}  # forgets notes
    with caplog.at_level(logging.ERROR):
        catalogue = read_catalogue(directory, str(directory), RecipeFormat(4, bad, recipe_format.validator))
    assert len(catalogue) == 0
    [record] = caplog.records
    message = record.getMessage()
    assert f"{file}: migration from format version 1 to 4 gave an invalid recipe: recipe 'Old': " in message
    assert "notes" in message
    assert file.read_bytes() == before
    assert not (directory / BACKUP_DIRECTORY).exists()


def test_AC_098_a_file_invalid_in_its_own_version_is_skipped_before_migrating(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = tmp_path / "recipes"
    recipe_file(directory, {"name": "Old", "mashing_process": {"goals": [{"type": "Rest"}]}}, "Old")
    calls: list[str] = []
    with caplog.at_level(logging.ERROR):
        catalogue = read_catalogue(directory, str(directory), four_versions(calls))
    assert len(catalogue) == 0
    assert calls == []
    [record] = caplog.records
    assert "recipe 'Old', goal 1 (index 0, Rest)" in record.getMessage()


def test_AC_098_a_migrated_file_that_cannot_be_written_is_used_all_the_same(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "recipes"
    file = recipe_file(directory, {"name": "Old", "mashing_process": MASH_IN}, "Old")
    before = file.read_bytes()

    def refuse(path: Path, text: str) -> None:
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(recipes, "write_file", refuse)
    with caplog.at_level(logging.WARNING):
        catalogue = read_catalogue(directory, str(directory), four_versions([]))
    assert [recipe.name for recipe in catalogue] == ["Old"]
    assert file.read_bytes() == before
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert (
        "recipe 'Old' migrated from format version 1, but the file cannot be written" in record.getMessage()
    )


@pytest.mark.parametrize("version", [0, -1, 1.5, "1", True, None])
def test_AC_099_an_invalid_version_is_rejected(version: Any) -> None:
    content = {"version": version, "name": "x", "mashing_process": MASH_IN}
    with pytest.raises(RecipeError) as error:
        parse_recipe("R", json.dumps(content), "R.json")
    assert (
        str(error.value) == f"R.json: recipe 'R': version must be an integer ≥ 1, not {json.dumps(version)}"
    )


def test_RCP_011_every_version_below_the_latest_needs_a_migrator() -> None:
    assert sorted(MIGRATORS) == list(range(1, LATEST_VERSION))
    with pytest.raises(ValueError, match="migrators missing"):
        RecipeFormat(latest=3, migrators={1: lambda raw: raw})
    with pytest.raises(ValueError, match="or extra"):
        RecipeFormat(latest=1, migrators={1: lambda raw: raw})
    for version in range(1, LATEST_VERSION):  # the frozen schema of every older version is shipped
        assert (SPEC_DATA / f"recipe.v{version}.schema.json").read_bytes() == (
            PACKAGE_DATA / f"recipe.v{version}.schema.json"
        ).read_bytes()


def test_with_version_puts_version_after_schema() -> None:
    assert list(with_version({"name": "x", "version": 1, "$schema": "s"}, 2)) == [
        "$schema",
        "version",
        "name",
    ]
    assert list(with_version({"name": "x"}, 2)) == ["version", "name"]
