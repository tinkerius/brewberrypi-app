"""Client/server interface: REST API and server-sent events (spec 08; AC-070..074, AC-005 interface part)."""

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from brewberrypi.chart import TemperatureChart
from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.engine import Engine
from brewberrypi.hal.mock import MockHal
from brewberrypi.recipes import RecipeLibrary, load_recipes
from brewberrypi.web.app import create_app
from brewberrypi.web.events import MAX_BACKLOG, EventHub, Subscriber, frame
from brewberrypi.web.messages import (
    hardware_status,
    process_state,
    recipe_detail,
    recipe_list,
    run_command,
    validator,
)

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
CATALOGUE = load_recipes(CONFIG.recipes)
HARDWARE_STATUS = validator("HardwareStatus")
PROCESS_STATE = validator("ProcessState")
RECIPE_LIST = validator("RecipeList")
RECIPE_DETAIL = validator("RecipeDetail")
CHART_DATA = validator("ChartData")
CHART_UPDATE = validator("ChartUpdate")
SNAPSHOT = validator("Snapshot")
COMMAND_REPLY = validator("CommandReply")
ERROR_REPLY = validator("ErrorReply")
UI_SETTINGS = validator("UiSettings")
JSON = {"Content-Type": "application/json"}


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def hal(clock: ManualClock) -> MockHal:
    return MockHal(clock, temperature=20.0)


@pytest.fixture
async def engine(hal: MockHal, clock: ManualClock) -> AsyncIterator[Engine]:
    engine = Engine(hal, clock, CATALOGUE, CONFIG)
    yield engine
    await engine.shutdown()


# --- payloads (spec 8.2, IF-004) ---


def test_AC_071_temperature_is_a_json_number_with_one_decimal(hal: MockHal) -> None:
    hal.set_temperature(62.5)
    hal.switch_on_pump()
    hal.set_heating(0.5)
    status = hardware_status(hal)
    HARDWARE_STATUS(status)
    assert json.dumps(status) == '{"temperature": 62.5, "pumpstatus": true, "heatingstatus": 0.5}'


def test_AC_005_invalid_temperature_is_null(hal: MockHal) -> None:
    hal.set_temperature(None)
    status = hardware_status(hal)
    HARDWARE_STATUS(status)
    assert '"temperature": null' in json.dumps(status)


def test_AC_072_no_process(engine: Engine) -> None:
    assert process_state(engine) == {"state": "NoProcess"}
    PROCESS_STATE(process_state(engine))


async def test_IF_020_process_state_with_goals_and_active_request(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    state = process_state(engine)
    PROCESS_STATE(state)
    assert state == {
        "processid": "TEST",
        "processname": "Test Process",
        "state": "Loaded",
        "goals": [
            {
                "name": "Iodine Test",
                "description": "Now it's your job to do the iodine test.",
                "status": "Waiting",
                "statuspercent": 0,
                "statusmessage": "0%",
            }
        ],
    }
    engine.start()
    await clock.advance(0)
    goal = process_state(engine)["goals"][0]
    request = engine.process.goals[0].active_request  # type: ignore[union-attr]
    assert request is not None
    assert goal["activeRequest"] == {
        "id": request.id,
        "message": "Iodine test successfull?",
        "options": ["Yes", "No"],
    }
    assert goal["statusmessage"] == "Waiting for answer"
    PROCESS_STATE(process_state(engine))


def test_IF_030_recipe_list_in_catalogue_order() -> None:
    recipes = recipe_list(CATALOGUE)
    RECIPE_LIST(recipes)
    assert [r["id"] for r in recipes] == [
        "BENIWEISS",
        "Dummy",
        "HANCOCK",
        "HIPA",
        "TEST",
        "WIT",
        "WaterBoil",
        "ZORA",
    ]


def test_IF_031_recipe_detail_has_the_goals_as_in_the_recipe_file() -> None:
    recipe = CATALOGUE.get("WaterBoil")
    assert recipe is not None
    detail = recipe_detail(recipe)
    RECIPE_DETAIL(detail)
    assert detail == {
        "id": "WaterBoil",
        "name": "Water Boiling",
        "goals": [
            {"type": "HeatUpForHopsCooking"},
            {
                "type": "CookWater",
                "minutes": 20,
                "name": "Cooking Water",
                "description": "Cooking water for 20min",
            },
        ],
    }


# --- commands (spec 8.3, IF-050..052) ---


@pytest.mark.parametrize(
    ("command", "body", "status", "message"),
    [
        ("load", {"recipe": "ZORA"}, 200, "Process München 12.6 - SUD1:ZORA (ZORA) loaded."),
        ("load", {}, 422, "parameter 'recipe' not set."),
        ("load", None, 422, "parameter 'recipe' not set."),
        ("load", {"recipe": "NOPE"}, 404, "Unknown recipe 'NOPE'."),
        ("load", {"recipe": 7}, 422, "Invalid load command: recipe must be string"),
        ("load", "ZORA", 422, "Invalid load command: must be object"),
        ("start", {}, 409, "No process loaded."),
        ("start", {"now": True}, 422, "Invalid start command: must not contain {'now'} properties"),
        ("unload", None, 409, "No process loaded."),
        ("pause", {}, 409, "No process loaded."),
        ("resume", {}, 409, "No process loaded."),
        ("stop", {}, 409, "No process loaded."),
        ("respond", {"response": "Yes"}, 409, "There is no active request to respond to."),
        ("respond", {}, 422, "Invalid respond command: must contain ['response'] properties"),
    ],
)
def test_AC_078_every_command_gets_a_reply_and_an_outcome(
    engine: Engine, command: str, body: Any, status: int, message: str
) -> None:
    code, reply = run_command(engine, command, body)
    COMMAND_REPLY(reply)
    assert (code, reply) == (status, {"command": command, "success": status == 200, "message": message})


async def test_AC_078_an_answer_that_is_no_option_is_invalid(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    assert run_command(engine, "respond", {"response": "confirm"}) == (
        422,
        {"command": "respond", "success": False, "message": "Response option 'confirm' is not valid."},
    )


# --- event hub (spec 8.4, IF-070..076) ---


def events(frames: list[str]) -> list[tuple[str, Any]]:
    """(event, data) of each frame."""
    result = []
    for text in frames:
        name, data = text.removesuffix("\n\n").split("\n")
        result.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
    return result


class Sources:
    """Controllable state for an EventHub."""

    def __init__(self) -> None:
        self.hardware: Any = {"temperature": 20.0}
        self.process: Any = {"state": "NoProcess"}
        self.chart = TemperatureChart(3)
        self.temperature: float | None = 20.0
        self.time = 0.0

    def hub(self, clock: ManualClock) -> EventHub:
        def sample() -> Any:
            self.time += 1
            return self.chart.sample(self.time, self.temperature)

        return EventHub(
            clock,
            {"hardware": lambda: self.hardware, "process": lambda: self.process, "recipes": lambda: []},
            self.chart.data,
            sample,
            check_seconds=0.25,
            chart_seconds=1.0,
            heartbeat_seconds=5.0,
        )


async def drain(subscriber: Subscriber) -> list[tuple[str, Any]]:
    """The events pending right now."""
    task = asyncio.create_task(subscriber.next())
    await asyncio.sleep(0)
    if not task.done():
        task.cancel()
        return []
    return events(task.result())


async def test_AC_070_a_new_stream_starts_with_a_snapshot_of_everything(clock: ManualClock) -> None:
    sources = Sources()
    hub = sources.hub(clock)
    subscriber = hub.subscribe()
    [(name, snapshot)] = await drain(subscriber)
    assert name == "snapshot"
    assert snapshot == {
        "hardware": {"temperature": 20.0},
        "process": {"state": "NoProcess"},
        "recipes": [],
        "chart": [{"key": "Kettle Temperature", "values": []}],
        "heartbeatMilliseconds": 5000,
    }


async def test_AC_079_state_is_pushed_only_when_it_changes(clock: ManualClock) -> None:
    sources = Sources()
    sources.temperature = None  # no chart points in this test
    hub = sources.hub(clock)
    subscriber = hub.subscribe()
    await drain(subscriber)
    loops = asyncio.create_task(hub.run())
    await clock.advance(0.75)  # three checks, nothing changed
    assert await drain(subscriber) == []
    sources.hardware = {"temperature": 20.5}
    await clock.advance(0.25)
    assert await drain(subscriber) == [("hardware", {"temperature": 20.5})]
    sources.process = {"state": "Loaded"}
    hub.check()  # what a command does, without waiting for the next check
    assert await drain(subscriber) == [("process", {"state": "Loaded"})]
    loops.cancel()


async def test_IF_073_chart_updates_carry_the_new_point_and_the_thinned_ones(clock: ManualClock) -> None:
    sources = Sources()
    hub = sources.hub(clock)
    subscriber = hub.subscribe()
    await drain(subscriber)
    loops = asyncio.create_task(hub.run())
    await clock.advance(1)
    [(name, update)] = await drain(subscriber)
    assert name == "chart"
    CHART_UPDATE(update)
    assert update == {"add": {"x": 1000, "y": 20.0}, "remove": []}
    await clock.advance(3)  # max 3 points: the 4th sample thins one
    assert [e for e in await drain(subscriber) if e[0] == "chart"][-1][1] == {
        "add": {"x": 4000, "y": 20.0},
        "remove": [2000],
    }
    sources.temperature = None  # AC-074: no point, no update
    await clock.advance(1)
    assert [e for e in await drain(subscriber) if e[0] == "chart"] == []
    loops.cancel()


async def test_IF_074_heartbeat(clock: ManualClock) -> None:
    hub = Sources().hub(clock)
    subscriber = hub.subscribe()
    await drain(subscriber)
    loops = asyncio.create_task(hub.run())
    await clock.advance(5)
    assert ("ping", {}) in await drain(subscriber)
    loops.cancel()


async def test_a_failing_source_is_logged_and_the_hub_continues(
    clock: ManualClock, caplog: pytest.LogCaptureFixture
) -> None:
    sources = Sources()
    hub = sources.hub(clock)
    subscriber = hub.subscribe()
    await drain(subscriber)
    loops = asyncio.create_task(hub.run())

    def broken() -> Any:
        raise RuntimeError("boom")

    hub._state["hardware"] = broken
    with caplog.at_level(logging.ERROR):
        await clock.advance(0.25)
    assert "building hardware failed" in caplog.text
    hub._state["hardware"] = lambda: {"temperature": 30.0}
    await clock.advance(0.25)
    assert await drain(subscriber) == [("hardware", {"temperature": 30.0})]
    loops.cancel()


async def test_a_slow_stream_gets_only_the_latest_state_and_is_ended_when_far_behind() -> None:
    subscriber = Subscriber()
    for value in (1, 2, 3):
        subscriber.offer("hardware", frame("hardware", value))
    subscriber.offer("chart", frame("chart", "a"))
    subscriber.offer("hardware", frame("hardware", 4))
    assert events(await subscriber.next()) == [("hardware", 4), ("chart", "a")]  # coalesced, order kept
    for _ in range(MAX_BACKLOG + 1):
        subscriber.offer("chart", frame("chart", "x"))
    assert subscriber.ended
    assert await subscriber.next() == []  # the stream closes; the browser reconnects with a snapshot


async def test_close_ends_every_stream(clock: ManualClock) -> None:
    hub = Sources().hub(clock)
    subscriber = hub.subscribe()
    hub.close()
    assert await subscriber.next() == []
    assert hub.subscribers == 0


# --- the application: REST (spec 8.2, 8.3) ---


def test_rest_reads(hal: MockHal, clock: ManualClock) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock)) as http:
        HARDWARE_STATUS(http.get("/api/hardware").json())
        assert http.get("/api/process").json() == {"state": "NoProcess"}
        RECIPE_LIST(http.get("/api/recipes").json())
        RECIPE_DETAIL(http.get("/api/recipes/ZORA").json())
        missing = http.get("/api/recipes/NOPE")
        assert missing.status_code == 404
        ERROR_REPLY(missing.json())
        CHART_DATA(http.get("/api/chart").json())
        assert http.get("/api/chart?since=x").status_code == 422


def test_AC_093_ui_settings_name_the_device_link(hal: MockHal, clock: ManualClock) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock)) as http:
        settings = http.get("/api/ui").json()
    UI_SETTINGS(settings)
    assert settings == {"deviceLink": "http://{host}:8081/"}
    no_link = dataclasses.replace(CONFIG, ui=dataclasses.replace(CONFIG.ui, device_link=None))
    with TestClient(create_app(no_link, CATALOGUE, hal, clock)) as http:
        assert http.get("/api/ui").json() == {"deviceLink": None}


def test_IF_042_chart_since_returns_only_newer_points(hal: MockHal, clock: ManualClock) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock)) as http:
        chart: TemperatureChart = http.app.state.chart  # type: ignore[attr-defined]
        for second in (1, 2, 3):
            chart.sample(second, 20.0)
        assert http.get("/api/chart?since=2000").json()[0]["values"] == [{"x": 3000, "y": 20.0}]


def test_IF_053_commands_over_rest(hal: MockHal, clock: ManualClock) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock)) as http:
        loaded = http.put("/api/process", json={"recipe": "Dummy"})
        assert (loaded.status_code, loaded.json()) == (
            200,
            {"command": "load", "success": True, "message": "Process My Dummy Process (Dummy) loaded."},
        )
        assert http.post("/api/process/start", json={}).json()["message"] == "Process started."
        twice = http.post("/api/process/start", json={})
        assert (twice.status_code, twice.json()["message"]) == (409, "Process is already running.")
        assert http.get("/api/process").json()["state"] == "Running"
        assert http.post("/api/process/pause", json={}).status_code == 200
        assert http.post("/api/process/resume", json={}).json()["message"] == "Process resumed."
        assert http.post("/api/process/stop", json={}).status_code == 200
        assert http.delete("/api/process").json() == {
            "command": "unload",
            "success": True,
            "message": "Process unloaded.",
        }
        unknown = http.post("/api/process/boil", json={})
        assert unknown.status_code == 404
        ERROR_REPLY(unknown.json())
        not_json = http.put("/api/process", content=b"{oops", headers=JSON)
        assert (not_json.status_code, not_json.json()["message"]) == (
            422,
            "Invalid load command: body is not JSON",
        )
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)


async def test_AC_058_an_answer_names_its_request(hal: MockHal, clock: ManualClock) -> None:
    hal.set_temperature(25.0)
    app = create_app(CONFIG, CATALOGUE, hal, clock)
    async with app.router.lifespan_context(app):
        engine: Engine = app.state.engine
        engine.load("TEST")
        engine.start()
        await clock.advance(0)
        request_id = process_state(engine)["goals"][0]["activeRequest"]["id"]
        status, body = await call(app, "POST", "/api/process/requests/0", {"response": "Yes"})
        assert (status, body["message"]) == (409, "This request is no longer open.")
        status, body = await call(app, "POST", f"/api/process/requests/{request_id}", {"response": "yes"})
        assert (status, body["message"]) == (422, "Response option 'yes' is not valid.")
        status, body = await call(app, "POST", f"/api/process/requests/{request_id}", {"response": "Yes"})
        assert (status, body["message"]) == (200, "Response 'Yes' accepted.")


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        ("POST", "/api/process/start", {"Origin": "http://evil.example", **JSON}, 403),
        ("PUT", "/api/process", {"Origin": "null", **JSON}, 403),
        ("GET", "/api/process", {"Origin": "http://evil.example"}, 403),
        ("POST", "/api/process/start", {"Content-Type": "text/plain"}, 415),
        ("POST", "/api/process/start", {}, 415),
        ("POST", "/api/process/start", {"Origin": "http://testserver", **JSON}, 409),  # own UI: passes
        ("POST", "/api/process/start", JSON, 409),  # no Origin (curl, scripts): passes
    ],
)
def test_AC_060_only_same_origin_and_json_commands(
    hal: MockHal, method: str, path: str, headers: dict[str, str], status: int
) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, ManualClock())) as http:
        response = http.request(method, path, headers=headers, content=b"{}")
        assert response.status_code == status
        if status in (403, 415):
            ERROR_REPLY(response.json())
            assert hal.get_pump_status() is False


# --- the application: event stream (spec 8.4) ---


async def call(app: Starlette, method: str, path: str, body: Any = None) -> tuple[int, Any]:
    """One request straight through ASGI (keeps the test on the manual clocks)."""
    raw = b"" if body is None else json.dumps(body).encode()
    scope = http_scope(method, path, [(b"content-type", b"application/json")])
    messages: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            await asyncio.Event().wait()
        received = True
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        messages.append(dict(message))

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(content)


def http_scope(method: str, path: str, headers: list[tuple[bytes, bytes]]) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }


class Stream:
    """An open `GET /api/events` straight through ASGI; `events()` returns what arrived so far."""

    def __init__(self, app: Starlette) -> None:
        self.headers: dict[bytes, bytes] = {}
        self._body = b""
        self._disconnect = asyncio.Event()
        self.done = asyncio.Event()

        async def receive() -> dict[str, Any]:
            await self._disconnect.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.headers = dict(message["headers"])
            else:
                self._body += message.get("body", b"")

        async def run() -> None:
            await app(http_scope("GET", "/api/events", []), receive, send)
            self.done.set()

        self._task = asyncio.create_task(run())

    def events(self) -> list[tuple[str, Any]]:
        frames = [f for f in self._body.decode().split("\n\n") if f.startswith("event: ")]
        return events([f + "\n\n" for f in frames])

    async def close(self) -> None:
        self._disconnect.set()
        await asyncio.wait_for(self.done.wait(), 1)


async def test_AC_070_event_stream_snapshot_then_the_effect_of_a_command(
    hal: MockHal, clock: ManualClock
) -> None:
    event_clock = ManualClock()
    app = create_app(CONFIG, CATALOGUE, hal, clock, event_clock=event_clock)
    async with app.router.lifespan_context(app):
        stream = Stream(app)
        await event_clock.advance(0)
        assert stream.headers[b"content-type"].startswith(b"text/event-stream")
        assert stream.headers[b"cache-control"] == b"no-cache"
        [(name, snapshot)] = stream.events()
        assert name == "snapshot"
        SNAPSHOT(snapshot)

        status, _ = await call(app, "PUT", "/api/process", {"recipe": "Dummy"})
        assert status == 200
        await event_clock.advance(0)  # pushed with the command, before the next check
        process = [data for name, data in stream.events() if name == "process"]
        assert process[-1]["processid"] == "Dummy"
        PROCESS_STATE(process[-1])

        hal.set_temperature(21.0)
        await event_clock.advance(CONFIG.events.check_milliseconds / 1000)
        assert (
            "hardware",
            {"temperature": 21.0, "pumpstatus": False, "heatingstatus": 0.0},
        ) in stream.events()
        await event_clock.advance(CONFIG.chart.sample_milliseconds / 1000)
        assert any(name == "chart" for name, _ in stream.events())
        await stream.close()
        assert app.state.hub.subscribers == 0


async def test_AC_085_a_new_recipe_file_reaches_every_client_and_a_loaded_process_keeps_its_recipe(
    hal: MockHal, clock: ManualClock, tmp_path: Path
) -> None:
    directory = tmp_path / "recipes"
    directory.mkdir()
    recipe = {"name": "Hold 30 °C", "mashing_process": {"goals": [{"type": "HeatUp", "temperature": 30}]}}
    (directory / "HOLD30.json").write_text(json.dumps(recipe))
    library = RecipeLibrary.open(dataclasses.replace(CONFIG.recipes, directory=directory))
    event_clock = ManualClock()
    app = create_app(CONFIG, library, hal, clock, event_clock=event_clock)
    async with app.router.lifespan_context(app):
        streams = [Stream(app), Stream(app)]
        await event_clock.advance(0)
        assert (await call(app, "PUT", "/api/process", {"recipe": "HOLD30"}))[0] == 200

        (directory / "WATER.json").write_text(
            json.dumps({"name": "Water", "mashing_process": {"goals": [{"type": "MashIn"}]}})
        )
        (directory / "HOLD30.json").write_text(json.dumps({**recipe, "name": "Hold at 30 °C, renamed"}))
        await event_clock.advance(CONFIG.recipes.check_milliseconds / 1000)
        expected = [{"id": "HOLD30", "name": "Hold at 30 °C, renamed"}, {"id": "WATER", "name": "Water"}]
        for stream in streams:
            [pushed] = [data for name, data in stream.events() if name == "recipes"]
            assert pushed == expected
            RECIPE_LIST(pushed)
        assert (await call(app, "GET", "/api/recipes"))[1] == expected
        assert app.state.engine.process.process_name == "Hold 30 °C"  # the loaded process is unchanged

        (directory / "WATER.json").unlink()
        await event_clock.advance(CONFIG.recipes.check_milliseconds / 1000)
        assert [data for name, data in streams[0].events() if name == "recipes"][-1] == expected[:1]
        assert (await call(app, "GET", "/api/recipes/WATER"))[0] == 404
        for stream in streams:
            await stream.close()


async def test_shutdown_ends_open_streams(hal: MockHal, clock: ManualClock) -> None:
    event_clock = ManualClock()
    app = create_app(CONFIG, CATALOGUE, hal, clock, event_clock=event_clock)
    async with app.router.lifespan_context(app):
        stream = Stream(app)
        await event_clock.advance(0)
        app.state.close_streams()  # what the server's signal handler calls
        await asyncio.wait_for(stream.done.wait(), 1)


async def test_chart_samples_on_its_period_with_the_app_clock(hal: MockHal) -> None:
    """The chart timestamps come from the application clock (simulated time in simulation)."""
    app_clock = ManualClock(wall_start=1_000.0)
    event_clock = ManualClock()
    app = create_app(CONFIG, CATALOGUE, hal, app_clock, event_clock=event_clock)
    async with app.router.lifespan_context(app):
        await app_clock.advance(60)
        await event_clock.advance(1)
        hal.set_temperature(None)
        await event_clock.advance(1)
        assert app.state.chart.data()[0]["values"] == [{"x": 1_060_000, "y": 20.0}]


def test_static_front_end_is_served(hal: MockHal, clock: ManualClock, tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<title>Brewberry Pi</title>")
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock, static_dir=tmp_path)) as http:
        response = http.get("/")
    assert response.status_code == 200
    assert "Brewberry Pi" in response.text


def test_missing_front_end_is_explained(hal: MockHal, clock: ManualClock, tmp_path: Path) -> None:
    with TestClient(create_app(CONFIG, CATALOGUE, hal, clock, static_dir=tmp_path)) as http:
        response = http.get("/")
    assert response.status_code == 503
    assert "make build" in response.text
