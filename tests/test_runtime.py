"""M10: runtime contract, application side (NFR-021, NFR-022, runtime contract §4, §5), as unit tests."""

import asyncio
import logging
import socket
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from brewberrypi import __version__
from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.engine import Engine
from brewberrypi.hal.mock import MockHal
from brewberrypi.notify import Notifier
from brewberrypi.web.app import create_app
from recipe_fixtures import with_test_recipes

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
CATALOGUE = with_test_recipes(CONFIG)


class RecordingNotifier(Notifier):
    def __init__(self, watchdog_seconds: float | None = None) -> None:
        super().__init__({"NOTIFY_SOCKET": "/run/test.sock"})
        self.watchdog_seconds = watchdog_seconds
        self.messages: list[str] = []

    def notify(self, message: str) -> None:
        self.messages.append(message)


# --- sd_notify ---


def test_notifier_is_off_without_notify_socket() -> None:
    notifier = Notifier({"BREWBERRYPI_WATCHDOG_MS": "5000"})
    assert not notifier.enabled
    assert notifier.watchdog_seconds is None
    notifier.notify("READY=1")  # no-op


@pytest.mark.parametrize(
    ("value", "seconds"), [("5000", 5.0), ("250", 0.25), ("", None), ("0", None), ("x", None)]
)
def test_watchdog_interval_from_the_environment(value: str, seconds: float | None) -> None:
    notifier = Notifier({"NOTIFY_SOCKET": "/run/notify", "BREWBERRYPI_WATCHDOG_MS": value})
    assert notifier.watchdog_seconds == seconds


def test_notifications_reach_the_socket(tmp_path: Path) -> None:
    path = tmp_path / "notify.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind(str(path))
        Notifier({"NOTIFY_SOCKET": str(path)}).notify("READY=1")
        assert receiver.recv(100) == b"READY=1"


def test_abstract_socket_address() -> None:
    name = f"brewberrypi-test-{id(object())}"
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as receiver:
        receiver.bind("\0" + name)
        Notifier({"NOTIFY_SOCKET": "@" + name}).notify("WATCHDOG=1")
        assert receiver.recv(100) == b"WATCHDOG=1"


def test_a_failing_notification_is_only_logged(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    with caplog.at_level(logging.WARNING):
        Notifier({"NOTIFY_SOCKET": str(tmp_path / "missing.sock")}).notify("READY=1")
    assert "sd_notify READY=1 failed" in caplog.text


# --- /healthz, ready, shutdown ---


def test_healthz_reports_version_and_engine_state() -> None:
    hal = MockHal(temperature=20.0)
    with TestClient(create_app(CONFIG, CATALOGUE, hal, ManualClock(), notifier=RecordingNotifier())) as http:
        response = http.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "version": __version__, "engine": "NoProcess"}
        http.app.state.engine.load("IODINE")  # type: ignore[attr-defined]
        assert http.get("/healthz").json()["engine"] == "Loaded"


def test_NFR_021_step_6_ready_logs_beeps_and_notifies(caplog: pytest.LogCaptureFixture) -> None:
    hal = MockHal(temperature=20.0)
    notifier = RecordingNotifier()
    with (
        caplog.at_level(logging.INFO),
        TestClient(create_app(CONFIG, CATALOGUE, hal, ManualClock(), notifier=notifier)) as http,
    ):
        assert notifier.messages == []  # not before the server listens
        http.app.state.ready()  # type: ignore[attr-defined]  # called by the server once it listens
        assert notifier.messages == ["READY=1"]
        assert hal.beeps() == ["short_beep"]
        assert "BrewberryPI ready for brewing :-)" in caplog.messages
    assert notifier.messages == ["READY=1", "STOPPING=1"]
    assert "Shutting down BrewberryPi." in caplog.messages  # NFR-022


def test_NFR_022_shutdown_switches_everything_off() -> None:
    hal = MockHal(temperature=20.0)
    app = create_app(CONFIG, CATALOGUE, hal, ManualClock(), notifier=RecordingNotifier())
    with TestClient(app) as http:
        assert http.put("/api/process", json={"recipe": "DEMO"}).status_code == 200
        assert http.post("/api/process/start", json={}).status_code == 200
        for _ in range(100):  # the execution loop is its own task: wait for its first tick (ENG-022)
            if http.get("/api/hardware").json()["heatingstatus"] == 1.0:
                break
        assert (hal.get_heating_status(), hal.get_pump_status()) == (1.0, True)  # heating up
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)


# --- watchdog (runtime contract §4) ---


async def test_watchdog_pings_while_alive_and_stops_when_the_engine_hangs() -> None:
    clock = ManualClock()
    event_clock = ManualClock()
    hal = MockHal(clock, temperature=20.0)
    notifier = RecordingNotifier(watchdog_seconds=1.0)
    app = create_app(CONFIG, CATALOGUE, hal, clock, event_clock=event_clock, notifier=notifier)
    async with app.router.lifespan_context(app):
        await event_clock.advance(3)
        assert notifier.messages.count("WATCHDOG=1") == 4  # at 0, 1, 2, 3 s
        engine: Engine = app.state.engine
        engine.load("DEMO")
        engine.start()
        await clock.advance(1)
        await event_clock.advance(1)
        assert notifier.messages.count("WATCHDOG=1") == 5  # running and ticking

        engine._last_tick -= 60  # the execution loop has not ticked for a minute: hung
        await event_clock.advance(5)
        assert notifier.messages.count("WATCHDOG=1") == 5  # no more pings: the host restarts the app


async def test_no_watchdog_without_an_interval() -> None:
    event_clock = ManualClock()
    notifier = RecordingNotifier(watchdog_seconds=None)
    app = create_app(CONFIG, CATALOGUE, MockHal(), ManualClock(), event_clock=event_clock, notifier=notifier)
    async with app.router.lifespan_context(app):
        await event_clock.advance(10)
    assert "WATCHDOG=1" not in notifier.messages


async def test_engine_is_alive_unless_a_running_loop_is_gone_or_stalled() -> None:
    clock = ManualClock()
    engine = Engine(MockHal(clock, temperature=20.0), clock, CATALOGUE, CONFIG)
    assert engine.alive()  # no process
    engine.load("DEMO")
    engine.start()
    await clock.advance(30)
    assert engine.alive()  # ticking
    engine.pause()
    await clock.advance(3600)
    assert engine.alive()  # paused: no ticks expected
    engine.resume()
    await clock.advance(0)
    assert engine.alive()
    assert engine._loop_task is not None
    engine._loop_task.cancel()  # the loop died while the process is still Running
    await asyncio.sleep(0)
    assert not engine.alive()
    await engine.shutdown()
