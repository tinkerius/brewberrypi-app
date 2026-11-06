"""The mirror rule of Cloud Sync (SYNC-011, SYNC-015): make the recipe files equal the cloud recipes.

`plan()` is a pure function from the recipe files on disk and the wanted recipes to a list of actions;
`apply()` carries them out. Contents are compared as bytes, so a file is only written when it really differs.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from brewberrypi.files import make_directory, sync_directory, write_bytes
from brewberrypi.recipes import BACKUP_DIRECTORY

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Write:
    """Write `<recipe_id>.json`; first copy the file on disk to `.backup/<id>.local.json` if `keep_local`."""

    recipe_id: str
    content: bytes
    keep_local: bool


@dataclass(frozen=True)
class Remove:
    """Move `<recipe_id>.json` to `.backup/<id>.removed.json`."""

    recipe_id: str


Action = Write | Remove


def local_recipes(directory: Path) -> dict[str, bytes | None]:
    """The recipe files (`*.json`, not hidden, RCP-001): id -> content, `None` if unreadable."""
    result: dict[str, bytes | None] = {}
    for entry in os.scandir(directory):
        name = entry.name
        if not name.endswith(".json") or name.startswith(".") or not entry.is_file():
            continue
        try:
            result[name.removesuffix(".json")] = Path(entry.path).read_bytes()
        except OSError:
            result[name.removesuffix(".json")] = None
    return result


def plan(
    local: Mapping[str, bytes | None], wanted: Mapping[str, bytes], written: Mapping[str, bytes]
) -> list[Action]:
    """What makes the recipe files equal `wanted` (SYNC-011).

    `written` is what this application last wrote for each id: a file that still has that content was not
    changed on the device, so it needs no `.local` copy when the cloud changed it."""
    actions: list[Action] = []
    for recipe_id in sorted(wanted):
        content = wanted[recipe_id]
        if recipe_id not in local:
            actions.append(Write(recipe_id, content, keep_local=False))
            continue
        on_disk = local[recipe_id]
        if on_disk != content:
            changed_here = on_disk is not None and on_disk != written.get(recipe_id)
            actions.append(Write(recipe_id, content, keep_local=changed_here))
    actions.extend(Remove(recipe_id) for recipe_id in sorted(local) if recipe_id not in wanted)
    return actions


def apply(directory: Path, actions: list[Action], local: Mapping[str, bytes | None]) -> list[Action]:
    """Carry out `actions`; return those that succeeded. A failure is logged and only skips that recipe
    (SYNC-015)."""
    done: list[Action] = []
    backup = directory / BACKUP_DIRECTORY
    for action in actions:
        file = directory / f"{action.recipe_id}.json"
        try:
            if isinstance(action, Write):
                on_disk = local.get(action.recipe_id)
                if action.keep_local and on_disk is not None:
                    _ensure(backup)
                    write_bytes(backup / f"{action.recipe_id}.local.json", on_disk)
                write_bytes(file, action.content)
                log.info("Cloud Sync: recipe %r written", action.recipe_id)
            else:
                _ensure(backup)
                os.replace(file, backup / f"{action.recipe_id}.removed.json")
                sync_directory(directory)
                sync_directory(backup)
                log.info("Cloud Sync: recipe %r moved to %s", action.recipe_id, BACKUP_DIRECTORY)
            done.append(action)
        except OSError as error:
            log.warning(
                "Cloud Sync: recipe %r could not be updated, retried at the next sync: %s",
                action.recipe_id,
                error,
            )
    return done


def _ensure(directory: Path) -> None:
    if not directory.is_dir():
        make_directory(directory)
