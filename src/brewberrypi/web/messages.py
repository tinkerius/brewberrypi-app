"""Message payloads and command handling of the client/server interface (spec 08).

Payload shapes follow `data/messages.schema.json` (a copy of `docs/specification/data/messages.schema.json`,
IF-004). State payloads are built synchronously on the event loop, so each one is a consistent snapshot
(IF-020, ENG-040). Command bodies are validated; a malformed one is rejected with 422, never a crash (IF-051).
"""

import json
from collections.abc import Callable
from dataclasses import asdict
from importlib.resources import files
from typing import Any

import fastjsonschema

from brewberrypi.config import Config
from brewberrypi.engine import Engine, ErrorKind, Reply
from brewberrypi.goals import Goal
from brewberrypi.hal import Hal
from brewberrypi.recipes import Recipe, RecipeCatalogue

_SCHEMA: dict[str, Any] = json.loads(
    files("brewberrypi").joinpath("data/messages.schema.json").read_text("utf-8")
)


def validator(definition: str) -> Callable[[Any], Any]:
    """A validator for one payload definition of the messages schema, e.g. `ProcessState`."""
    return fastjsonschema.compile({"$ref": f"#/$defs/{definition}", "$defs": _SCHEMA["$defs"]})  # type: ignore[no-any-return]


# --- state (spec 8.2) ---


def hardware_status(hal: Hal) -> dict[str, Any]:
    """IF-010: temperature as a JSON number with one decimal or `null` if INVALID (FIX-02, FIX-16)."""
    temperature = hal.get_temperature()
    return {
        "temperature": None if temperature is None else round(float(temperature), 1),
        "pumpstatus": hal.get_pump_status(),
        "heatingstatus": hal.get_heating_status(),
    }


def process_state(engine: Engine) -> dict[str, Any]:
    """IF-020: `{"state": "NoProcess"}` (FIX-18) or the loaded process with all goals."""
    process = engine.process
    if process is None:
        return {"state": "NoProcess"}
    return {
        "processid": process.process_id,
        "processname": process.process_name,
        "state": process.state,
        "goals": [_goal(goal) for goal in process.goals],
    }


def _goal(goal: Goal) -> dict[str, Any]:
    progress = goal.progress
    payload: dict[str, Any] = {
        "name": goal.name,
        "description": goal.description,
        "status": goal.status,
        "statuspercent": progress.percent,
        "statusmessage": progress.message,
    }
    request = goal.active_request
    if request is not None:  # omitted without a request (legacy)
        payload["activeRequest"] = {
            "id": request.id,
            "message": request.message,
            "options": list(request.options),
        }
    return payload


def recipe_list(recipes: RecipeCatalogue) -> list[dict[str, str]]:
    """IF-030, in catalogue order."""
    return recipes.summaries()


def ui_settings(config: Config) -> dict[str, Any]:
    """UiSettings (IF-080): the device link as configured; the UI puts in its own host (UI-002)."""
    return {"deviceLink": config.ui.device_link}


def recipe_detail(recipe: Recipe) -> dict[str, Any]:
    """IF-031: one recipe with its goals as written in its recipe file's `mashing_process` (spec 7.2, 7.3)."""
    goals = [
        {key: value for key, value in asdict(goal).items() if value is not None} for goal in recipe.goals
    ]
    return {"id": recipe.id, "name": recipe.name, "goals": goals}


# --- commands (spec 8.3) ---

COMMANDS: dict[str, str] = {
    "load": "LoadCommand",
    "unload": "EmptyCommand",
    "start": "EmptyCommand",
    "pause": "EmptyCommand",
    "resume": "EmptyCommand",
    "stop": "EmptyCommand",
    "respond": "RespondCommand",
}
"""Command name -> schema definition of its request body."""
_VALIDATORS = {command: validator(definition) for command, definition in COMMANDS.items()}
_STATUS: dict[ErrorKind | None, int] = {None: 200, "invalid": 422, "not_found": 404, "conflict": 409}


def run_command(
    engine: Engine, command: str, body: Any, request_id: str | None = None
) -> tuple[int, dict[str, Any]]:
    """Run a command; return the HTTP status (IF-052) and the CommandReply (IF-050).

    `body` is the decoded JSON request body (`None` without one). Never raises for bad input (IF-051).
    """
    body = {} if body is None else body
    if command == "load" and isinstance(body, dict) and "recipe" not in body:
        reply = engine.load(None)  # legacy text "parameter 'recipe' not set."
    else:
        try:
            _VALIDATORS[command](body)
        except fastjsonschema.JsonSchemaValueException as error:
            message: str = error.message
            problem = message.removeprefix("data ").removeprefix("data.")
            reply = Reply(False, f"Invalid {command} command: {problem}", "invalid")
        else:
            reply = _run(engine, command, body, request_id)
    return _STATUS[reply.error], {"command": command, "success": reply.success, "message": reply.message}


def _run(engine: Engine, command: str, body: dict[str, Any], request_id: str | None) -> Reply:
    if command == "load":
        return engine.load(body["recipe"])
    if command == "respond":
        return engine.respond(body["response"], request_id)
    actions: dict[str, Callable[[], Reply]] = {
        "unload": engine.unload,
        "start": engine.start,
        "pause": engine.pause,
        "resume": engine.resume,
        "stop": engine.stop,
    }
    return actions[command]()
