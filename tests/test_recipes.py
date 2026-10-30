"""M1: recipe catalogue, one file per recipe (RCP-001..006, AC-080, AC-081, AC-084, AC-085, Q-27, Q-29)."""

import json
import logging
import re
import stat
from pathlib import Path
from typing import Any

import pytest

from brewberrypi.config import RecipesConfig
from brewberrypi.recipes import (
    GoalDefinition,
    RecipeDirectoryError,
    RecipeError,
    RecipeLibrary,
    load_recipes,
    parse_recipe,
)

SPEC_DATA = Path(__file__).parents[1] / "docs" / "specification" / "data"
PACKAGE_DATA = Path(__file__).parents[1] / "src" / "brewberrypi" / "data"
SORTED_IDS = ["BENIWEISS", "Dummy", "HANCOCK", "HIPA", "TEST", "WIT", "WaterBoil", "ZORA"]
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
    hipa = catalogue.get("HIPA")
    assert hipa is not None
    assert hipa.name == "München 12.6 - SUD4:HIPA"
    assert hipa.goals[3] == GoalDefinition(
        type="Rest",
        temperature=68,
        minutes=60,
        name="Verzuckerungsrast",
        description="Verzuckuerungsrast @68 for 60min",  # legacy typo kept (Q-07)
    )
    zora = catalogue.get("ZORA")
    assert zora is not None
    assert len(zora.goals) == 11
    assert zora.goals[9] == GoalDefinition(type="IodineTest", temperature=68, minutes=10)  # Q-05


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
    (directory / "ZORA.json").unlink()
    (directory / "Dummy.json").unlink()
    catalogue = load_recipes(configured(directory))
    assert [recipe.id for recipe in catalogue] == ["BENIWEISS", "HANCOCK", "HIPA", "TEST", "WIT", "WaterBoil"]
    assert not (directory / "ZORA.json").exists()
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
    (staging / "ZORA.json").write_text("half written")
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
