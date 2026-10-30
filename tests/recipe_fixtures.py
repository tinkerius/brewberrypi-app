"""Recipes for the tests: the shipped catalogue plus the test recipes in `tests/recipes/` (not shipped).

`DEMO` runs every kind of operator request in a few simulated minutes (AC-090); `IODINE` is a single
one-minute iodine test (AC-037).
"""

from pathlib import Path

from brewberrypi.config import Config
from brewberrypi.recipes import RecipeCatalogue, load_recipes, read_catalogue

TEST_RECIPES = Path(__file__).parent / "recipes"


def with_test_recipes(config: Config) -> RecipeCatalogue:
    """The shipped recipes of `config` and the test recipes, sorted by id like the catalogue (RCP-002)."""
    shipped = load_recipes(config.recipes)
    extra = read_catalogue(TEST_RECIPES, str(TEST_RECIPES))
    recipes = sorted(shipped.recipes + extra.recipes, key=lambda recipe: recipe.id)
    return RecipeCatalogue(recipes=tuple(recipes), source=shipped.source)
