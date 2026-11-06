"""The web application: UI files, REST API and event stream on one port (spec 08, ADR-0002).

- REST (`/api/...`) for everything the client asks or orders: reads of the current state and the commands,
  each answered with an HTTP status and a CommandReply (IF-050..054).
- Server-sent events (`GET /api/events`) for everything the server tells all clients: a snapshot, then only
  changes (IF-070..076, `events.py`).
- `/api/...` accepts only same-origin requests, and commands only as `application/json` (IF-061, Q-17).
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from brewberrypi import __version__
from brewberrypi.chart import TemperatureChart
from brewberrypi.clock import Clock, SystemClock
from brewberrypi.cloud_sync import CloudSync, create_cloud_sync
from brewberrypi.config import Config
from brewberrypi.engine import Engine
from brewberrypi.hal import Hal
from brewberrypi.notify import Notifier
from brewberrypi.recipes import RecipeCatalogue, RecipeLibrary
from brewberrypi.web.events import RETRY_MILLISECONDS, EventHub
from brewberrypi.web.messages import (
    CLOUD_SYNC_COMMANDS,
    cloud_sync_status,
    hardware_status,
    process_state,
    recipe_detail,
    recipe_list,
    run_cloud_sync_command,
    run_command,
    ui_settings,
)

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(  # noqa: PLR0913  (clocks and UI folder are injectable for tests)
    config: Config,
    recipes: RecipeLibrary | RecipeCatalogue,
    hal: Hal,
    clock: Clock,
    *,
    event_clock: Clock | None = None,
    static_dir: Path = STATIC_DIR,
    notifier: Notifier | None = None,
    cloud_sync: CloudSync | None = None,
) -> Starlette:
    """The application. `clock` drives the engine and the chart timestamps (scaled in simulation, HW-064);
    `event_clock` paces the event stream, the recipe directory checks and Cloud Sync in real time. A plain
    `RecipeCatalogue` is never read again. `cloud_sync` replaces the one built from the configuration."""
    event_clock = SystemClock() if event_clock is None else event_clock
    library = recipes if isinstance(recipes, RecipeLibrary) else RecipeLibrary(recipes)
    notifier = Notifier() if notifier is None else notifier

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        await hal.start()  # sensor loop, first reading (HW-030, HW-034); outputs are already off (HW-020)
        engine = Engine(hal, clock, library, config)
        chart = TemperatureChart(config.chart.max_points)
        sync = cloud_sync or create_cloud_sync(config.cloud_sync, config.recipes.directory, event_clock)
        hub = _event_hub(
            config,
            recipes=library,
            hal=hal,
            clock=clock,
            engine=engine,
            chart=chart,
            event_clock=event_clock,
            cloud_sync=sync,
        )

        def recipes_synced() -> None:
            """Cloud Sync changed recipe files: show the new recipe list at once (RCP-006, SYNC-011)."""
            if library.refresh():
                hub.check()

        sync.on_recipes_changed = recipes_synced
        app.state.engine, app.state.hub, app.state.chart, app.state.cloud_sync = engine, hub, chart, sync
        app.state.hal, app.state.recipes, app.state.config = hal, library, config
        loop = asyncio.get_running_loop()
        app.state.close_streams = lambda: loop.call_soon_threadsafe(hub.close)  # for serve()'s signal handler
        events = asyncio.create_task(hub.run())
        watch = _watch_recipes(library, hub, event_clock, config.recipes.check_milliseconds / 1000)
        # Cloud Sync starts after the outputs are off (HAL created and started), without delaying startup.
        background = [events, asyncio.create_task(watch), asyncio.create_task(sync.run())]
        if notifier.watchdog_seconds is not None:

            def alive() -> bool:
                return engine.alive() and not events.done()

            watchdog = _watchdog(notifier, event_clock, notifier.watchdog_seconds, alive)
            background.append(asyncio.create_task(watchdog))

        def ready() -> None:
            """Startup step 6 of NFR-021, once the server is listening."""
            log.info("BrewberryPI ready for brewing :-)")
            hal.short_beep()
            notifier.notify("READY=1")

        app.state.ready = ready
        try:
            yield
        finally:
            log.info("Shutting down BrewberryPi.")  # NFR-022
            notifier.notify("STOPPING=1")
            hub.close()
            for task in background:
                task.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await engine.shutdown()  # outputs off on every shutdown path (HW-021, ADR-0001)
            await hal.close()  # buzzer off, GPIO released

    routes: list[Route | Mount] = [
        Route("/api/hardware", _get_hardware, methods=["GET"]),
        Route("/api/process", _get_process, methods=["GET"]),
        Route("/api/process", _load, methods=["PUT"]),
        Route("/api/process", _unload, methods=["DELETE"]),
        Route("/api/process/{command:str}", _command, methods=["POST"]),
        Route("/api/process/requests/{request_id:str}", _respond, methods=["POST"]),
        Route("/api/recipes", _get_recipes, methods=["GET"]),
        Route("/api/recipes/{recipe_id:str}", _get_recipe, methods=["GET"]),
        Route("/api/chart", _get_chart, methods=["GET"]),
        Route("/api/ui", _get_ui, methods=["GET"]),
        Route("/api/cloud-sync", _get_cloud_sync, methods=["GET"]),
        Route("/api/cloud-sync/{command:str}", _cloud_sync_command, methods=["POST"]),
        Route("/api/events", _events, methods=["GET"]),
        Route("/healthz", _healthz),
    ]
    if (static_dir / "index.html").is_file():
        routes.append(Mount("/", StaticFiles(directory=static_dir, html=True)))
    else:
        routes.append(Route("/", _front_end_missing))
    app = Starlette(routes=routes, lifespan=lifespan)
    app.add_middleware(SameOriginApi)
    return app


# --- reads (spec 8.2) ---


async def _get_hardware(request: Request) -> Response:
    return JSONResponse(hardware_status(request.app.state.hal))


async def _get_process(request: Request) -> Response:
    return JSONResponse(process_state(request.app.state.engine))


async def _get_recipes(request: Request) -> Response:
    return JSONResponse(recipe_list(request.app.state.recipes.catalogue))


async def _get_recipe(request: Request) -> Response:
    recipe_id = request.path_params["recipe_id"]
    recipe = request.app.state.recipes.get(recipe_id)
    if recipe is None:
        return _error(f"Unknown recipe '{recipe_id}'.", 404)
    return JSONResponse(recipe_detail(recipe))


async def _get_ui(request: Request) -> Response:
    return JSONResponse(ui_settings(request.app.state.config))


async def _get_chart(request: Request) -> Response:
    """IF-040; `?since=<epoch ms>` returns only newer points."""
    since = request.query_params.get("since")
    try:
        after = None if since is None else int(since)
    except ValueError:
        return _error("since must be epoch milliseconds.", 422)
    chart: TemperatureChart = request.app.state.chart
    return JSONResponse(chart.data(after))


async def _get_cloud_sync(request: Request) -> Response:
    return JSONResponse(cloud_sync_status(request.app.state.cloud_sync))


# --- commands (spec 8.3) ---

_POST_COMMANDS = ("start", "pause", "resume", "stop")


async def _load(request: Request) -> Response:
    return await _run(request, "load")


async def _unload(request: Request) -> Response:
    return await _run(request, "unload")


async def _command(request: Request) -> Response:
    command = request.path_params["command"]
    if command not in _POST_COMMANDS:
        return _error(f"Unknown command '{command}'.", 404)
    return await _run(request, command)


async def _respond(request: Request) -> Response:
    return await _run(request, "respond", request.path_params["request_id"])


async def _run(request: Request, command: str, request_id: str | None = None) -> Response:
    """Run a command, push its effect to every event stream, then answer the caller (IF-050, IF-072)."""
    raw = await request.body()
    try:
        body: Any = json.loads(raw) if raw.strip() else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        message = f"Invalid {command} command: body is not JSON"
        return JSONResponse({"command": command, "success": False, "message": message}, status_code=422)
    status, reply = run_command(request.app.state.engine, command, body, request_id)
    hub: EventHub = request.app.state.hub
    hub.check()
    return JSONResponse(reply, status_code=status)


async def _cloud_sync_command(request: Request) -> Response:
    """IF-091: `POST /api/cloud-sync/connect|disconnect|sync`."""
    command = CLOUD_SYNC_COMMANDS.get(request.path_params["command"])
    if command is None:
        return _error(f"Unknown command '{request.path_params['command']}'.", 404)
    raw = await request.body()
    try:
        body: Any = json.loads(raw) if raw.strip() else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        message = f"Invalid {command} command: body is not JSON"
        return JSONResponse({"command": command, "success": False, "message": message}, status_code=422)
    status, reply = run_cloud_sync_command(request.app.state.cloud_sync, command, body)
    hub: EventHub = request.app.state.hub
    hub.check()
    return JSONResponse(reply, status_code=status)


# --- event stream (spec 8.4) ---


async def _events(request: Request) -> Response:
    hub: EventHub = request.app.state.hub
    subscriber = hub.subscribe()

    async def stream() -> AsyncIterator[str]:
        try:
            yield f"retry: {RETRY_MILLISECONDS}\n\n"
            while True:
                frames = await subscriber.next()
                if not frames:
                    return  # fell behind or the server shuts down (IF-075)
                for text in frames:
                    yield text
        finally:
            hub.unsubscribe(subscriber)

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(stream(), media_type="text/event-stream", headers=headers)


def _error(message: str, status: int) -> Response:
    """An error that is not a command's own reply (IF-052)."""
    return JSONResponse({"success": False, "message": message}, status_code=status)


async def _healthz(request: Request) -> Response:
    """Runtime contract §5."""
    engine: Engine = request.app.state.engine
    return JSONResponse({"status": "ok", "version": __version__, "engine": engine.state})


class SameOriginApi:
    """IF-061 (Q-17): `/api/...` only for the server's own web UI and for tools without an `Origin` header.

    A request whose `Origin` is not the server itself gets 403; a POST or PUT that is not `application/json`
    gets 415. Browsers send such a cross-site request only after a CORS preflight, which this server never
    allows, so other web pages in the brewer's browser cannot send commands.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None and urlsplit(origin).netloc != headers.get("host"):
                await _error("Requests from other web pages are not allowed.", 403)(scope, receive, send)
                return
            content_type = headers.get("content-type", "").split(";")[0].strip().lower()
            if scope["method"] in ("POST", "PUT") and content_type != "application/json":
                await _error("Commands must be sent as application/json.", 415)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _event_hub(  # noqa: PLR0913
    config: Config,
    *,
    recipes: RecipeLibrary,
    hal: Hal,
    clock: Clock,
    engine: Engine,
    chart: TemperatureChart,
    event_clock: Clock,
    cloud_sync: CloudSync,
) -> EventHub:
    """The event stream's sources (spec 8.4); chart timestamps come from the application clock."""
    return EventHub(
        event_clock,
        {
            "hardware": lambda: hardware_status(hal),
            "process": lambda: process_state(engine),
            "recipes": lambda: recipe_list(recipes.catalogue),
            "cloudSync": lambda: cloud_sync_status(cloud_sync),
        },
        chart.data,
        lambda: chart.sample(clock.wall_time(), hal.get_temperature()),
        check_seconds=config.events.check_milliseconds / 1000,
        chart_seconds=config.chart.sample_milliseconds / 1000,
        heartbeat_seconds=config.events.heartbeat_milliseconds / 1000,
    )


async def _watch_recipes(library: RecipeLibrary, hub: EventHub, clock: Clock, interval: float) -> None:
    """Read the recipe directory again when its files change and push the new recipe list (RCP-006)."""
    while True:
        await clock.sleep(interval)
        try:
            changed = library.refresh()
        except Exception:
            log.exception("checking the recipe directory failed")
            continue
        if changed:
            hub.check()


async def _watchdog(notifier: Notifier, clock: Clock, interval: float, alive: Callable[[], bool]) -> None:
    """WATCHDOG=1 only while the engine and the event hub are alive, so a hang stops the pings (§4)."""
    healthy = True
    while True:
        if alive():
            notifier.notify("WATCHDOG=1")
            healthy = True
        elif healthy:
            log.error("engine or event hub is not alive; no more watchdog pings")
            healthy = False
        await clock.sleep(interval)


async def _front_end_missing(request: Request) -> Response:
    return PlainTextResponse(
        "The front end is not built. Run `make build`, or use `make dev`.", status_code=503
    )
