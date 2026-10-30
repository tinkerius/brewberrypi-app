"""M5: engine lifecycle and execution loop (spec 03; AC-030..039, engine parts of AC-050..057)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.engine import Engine, EngineState, Reply
from brewberrypi.goals import Goal
from brewberrypi.hal.mock import MockHal
from brewberrypi.recipes import RecipeCatalogue, load_recipes, parse_recipe

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
CATALOGUE = load_recipes(CONFIG.recipes)
TICK = 0.5


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


def engine_with(hal: MockHal, clock: ManualClock, goals: str) -> Engine:
    """An engine whose only recipe `R` has the given goals (JSON list)."""
    recipe = parse_recipe("R", f'{{"name": "Recipe", "mashing_process": {{"goals": {goals}}}}}', "test")
    catalogue = RecipeCatalogue(recipes=(recipe,), source="test")
    return Engine(hal, clock, catalogue, CONFIG)


def state(engine: Engine) -> EngineState:
    """Read through a function, so mypy does not narrow the state across commands."""
    return engine.state


def statuses(engine: Engine) -> list[str]:
    assert engine.process is not None
    return [goal.status for goal in engine.process.goals]


def active(engine: Engine) -> Goal:
    assert engine.process is not None
    goal = engine.process.active_goal()
    assert goal is not None
    return goal


def respond_when_asked(engine: Engine, option: str) -> Reply:
    assert active(engine).active_request is not None
    return engine.respond(option)


# --- load / unload (ENG-010, ENG-011) ---


def test_AC_030_load_creates_a_loaded_process(engine: Engine) -> None:
    assert state(engine) == "NoProcess"
    reply = engine.load("ZORA")
    assert reply == Reply(True, "Process München 12.6 - SUD1:ZORA (ZORA) loaded.")
    assert state(engine) == "Loaded"
    assert statuses(engine) == ["Waiting"] * 11
    assert engine.process is not None
    assert (engine.process.process_id, engine.process.process_name) == ("ZORA", "München 12.6 - SUD1:ZORA")


def test_AC_031_unknown_recipe_or_missing_parameter_is_rejected(engine: Engine) -> None:
    assert engine.load("NOPE") == Reply(False, "Unknown recipe 'NOPE'.")
    assert state(engine) == "NoProcess"
    assert engine.load(None) == Reply(False, "parameter 'recipe' not set.")
    assert engine.load("") == Reply(False, "parameter 'recipe' not set.")
    engine.load("HIPA")
    assert engine.load("NOPE").success is False
    assert engine.process is not None
    assert engine.process.process_id == "HIPA"  # state unchanged


def test_load_replaces_a_loaded_process(engine: Engine) -> None:
    engine.load("ZORA")
    assert engine.load("TEST").success
    assert engine.process is not None
    assert engine.process.process_id == "TEST"


async def test_unload_switches_everything_off(engine: Engine, hal: MockHal) -> None:
    assert engine.unload() == Reply(False, "No process loaded.")
    engine.load("TEST")
    hal.switch_on_pump()
    assert engine.unload() == Reply(True, "Process unloaded.")
    assert state(engine) == "NoProcess"
    assert hal.get_pump_status() is False


# --- start (ENG-012) ---


def test_AC_032_start_without_a_process_is_rejected(engine: Engine) -> None:
    assert engine.start() == Reply(False, "No process loaded.")
    assert state(engine) == "NoProcess"


async def test_AC_033_start_twice_is_rejected_and_one_loop_runs(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(hal, clock, '[{"type": "HeatUp", "temperature": 62}]')
    engine.load("R")
    assert engine.start() == Reply(True, "Process started.")
    assert engine.start() == Reply(False, "Process is already running.")
    await clock.advance(5)
    ticks = [
        e for e in hal.events if e.operation == "pump"
    ]  # holdTemperature switches the pump on every tick
    assert len(ticks) == 11  # t = 0, 0.5, ..., 5.0: one pursue per tick
    await engine.shutdown()


async def test_ENG_022_first_pursue_happens_immediately(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(hal, clock, '[{"type": "HeatUp", "temperature": 62}]')
    engine.load("R")
    engine.start()
    await clock.advance(0)
    assert hal.get_heating_status() == 1.0
    assert statuses(engine) == ["Active"]
    await engine.shutdown()


# --- stop (ENG-014) ---


def test_AC_034_stop_without_running_is_rejected(engine: Engine) -> None:
    assert engine.stop() == Reply(False, "No process loaded.")
    engine.load("TEST")
    assert engine.stop() == Reply(False, "Process is not running.")
    assert state(engine) == "Loaded"


async def test_AC_035_stop_switches_everything_off_at_once(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    engine.load("Dummy")
    engine.start()
    await clock.advance(2)
    assert (hal.get_heating_status(), hal.get_pump_status()) == (1.0, True)
    beeps = len(hal.beeps())
    assert engine.stop() == Reply(True, "Process stopped.")
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)
    assert state(engine) == "Stopped"
    assert statuses(engine)[0] == "Active"  # kept for display
    events = len(hal.events)
    await clock.advance(60)
    assert len(hal.events) == events  # the loop no longer touches the hardware
    assert len(hal.beeps()) == beeps
    assert engine.start() == Reply(False, "Load the process again.")


# --- pause / resume (ENG-013, ENG-030..032) ---


async def test_AC_036_pause_freezes_the_rest_and_resume_continues(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(hal, clock, '[{"type": "Rest", "temperature": 62, "minutes": 10}]')
    hal.set_temperature(55.0)
    engine.load("R")
    engine.start()
    await clock.advance(3 * 60)
    rest = active(engine)
    assert rest.status_message == "07:00 left"
    assert hal.get_heating_status() == 1.0

    assert engine.pause() == Reply(True, "Process paused.")
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)
    assert state(engine) == "Paused"
    await clock.advance(5 * 60)
    assert rest.status_message == "07:00 left"
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)  # no pursue while paused

    assert engine.resume() == Reply(True, "Process resumed.")
    assert state(engine) == "Running"
    await clock.advance(0)
    assert rest.status_message == "07:00 left"
    assert hal.get_heating_status() == 1.0
    await clock.advance(7 * 60)
    assert state(engine) == "Finished"
    await engine.shutdown()


async def test_pause_rejections(engine: Engine) -> None:
    assert engine.pause() == Reply(False, "No process loaded.")
    engine.load("TEST")
    assert engine.pause() == Reply(False, "Process is not running.")
    engine.start()
    engine.pause()
    assert engine.pause() == Reply(False, "Process is already paused.")


async def test_ENG_016_resume_only_from_paused_and_start_does_not_resume(engine: Engine) -> None:
    assert engine.resume() == Reply(False, "No process loaded.")
    engine.load("TEST")
    assert engine.resume() == Reply(False, "Process is not paused.")
    engine.start()
    assert engine.resume() == Reply(False, "Process is not paused.")
    engine.pause()
    reply = engine.start()  # AC-036: start while paused is rejected
    assert reply == Reply(False, "Process is paused. Resume it.")
    assert reply.error == "conflict"
    assert state(engine) == "Paused"
    assert engine.resume() == Reply(True, "Process resumed.")
    await engine.shutdown()


def test_IF_051_rejections_carry_their_error_kind(engine: Engine) -> None:
    assert engine.load(None).error == "invalid"
    assert engine.load("NOPE").error == "not_found"
    assert engine.unload().error == "conflict"
    assert engine.load("TEST").error is None


async def test_ENG_032_start_temperature_survives_pause(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(hal, clock, '[{"type": "HeatUp", "temperature": 62}]')
    engine.load("R")
    engine.start()
    await clock.advance(1)
    engine.pause()
    hal.set_temperature(41.0)
    await clock.advance(10)
    engine.resume()
    await clock.advance(0)
    assert active(engine).status_message == "50%"  # still measured from 20.0
    await engine.shutdown()


async def test_stop_while_paused(engine: Engine, hal: MockHal, clock: ManualClock) -> None:
    engine.load("Dummy")
    engine.start()
    await clock.advance(1)
    engine.pause()
    assert engine.stop() == Reply(True, "Process stopped.")
    assert state(engine) == "Stopped"
    await clock.advance(10)
    assert hal.get_heating_status() == 0.0


# --- reload, running commands, finish (AC-037..039) ---


async def test_AC_037_reload_creates_fresh_goals(engine: Engine, hal: MockHal, clock: ManualClock) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    respond_when_asked(engine, "Yes")
    await clock.advance(TICK)
    assert state(engine) == "Finished"

    engine.load("TEST")
    assert statuses(engine) == ["Waiting"]
    iodine = engine.process.goals[0] if engine.process else None
    assert iodine is not None
    assert iodine.progress.message == "0%"  # timer not started, no request
    assert iodine.active_request is None
    engine.start()
    await clock.advance(0)
    assert active(engine).active_request is not None  # asks the question again


async def test_AC_038_load_or_unload_while_running_is_rejected(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    engine.load("Dummy")
    engine.start()
    await clock.advance(1)
    for reply in (engine.load("ZORA"), engine.unload()):
        assert reply == Reply(False, "Stop the process first.")
    assert state(engine) == "Running"
    assert engine.process is not None
    assert engine.process.process_id == "Dummy"
    engine.pause()
    assert engine.load("ZORA") == Reply(False, "Stop the process first.")
    assert engine.unload() == Reply(False, "Stop the process first.")
    engine.resume()
    await clock.advance(1)
    assert hal.get_heating_status() == 1.0  # running process unaffected


async def test_AC_039_process_end_switches_everything_off(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(
        hal, clock, '[{"type": "HeatUp", "temperature": 30}, {"type": "HeatUp", "temperature": 40}]'
    )
    engine.load("R")
    engine.start()
    await clock.advance(1)
    hal.set_temperature(30.0)
    await clock.advance(TICK)
    assert statuses(engine) == ["Done", "Active"]
    assert hal.beeps() == ["short_beep"]  # ENG-024
    hal.set_temperature(40.0)
    await clock.advance(TICK)
    assert state(engine) == "Finished"
    assert statuses(engine) == ["Done", "Done"]
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)
    assert hal.beeps() == ["short_beep", "short_beep"]
    assert hal.events[-1].operation == "switch_everything_off"
    assert hal.events[-2].operation == "short_beep"  # beep after the last goal, then all off


# --- respond and reminders: engine parts of AC-050..057 ---


async def test_AC_050_confirm_completes_the_goal_with_a_short_beep(hal: MockHal, clock: ManualClock) -> None:
    engine = engine_with(hal, clock, '[{"type": "PourInBrewingWater"}, {"type": "MashIn"}]')
    engine.load("R")
    engine.start()
    await clock.advance(0)
    request = active(engine).active_request
    assert request is not None
    assert request.message == "Pour in brewing water"
    assert hal.beeps() == ["long_beep"]  # immediately
    await clock.advance(20)
    assert hal.beeps() == ["long_beep"] * 3  # every 10 s

    assert engine.respond("Confirm") == Reply(True, "Response 'Confirm' accepted.")
    await clock.advance(TICK)  # done within one tick
    assert statuses(engine) == ["Done", "Active"]
    assert hal.beeps() == ["long_beep"] * 3 + ["short_beep", "long_beep"]  # short beep, then MashIn asks
    await engine.shutdown()


async def test_AC_051_invalid_answer_is_rejected_and_the_request_stays(
    engine: Engine, clock: ManualClock
) -> None:
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    assert engine.respond("yes") == Reply(False, "Response option 'yes' is not valid.")
    request = active(engine).active_request
    assert request is not None
    assert not request.answered


async def test_AC_058_an_answer_naming_an_earlier_request_is_rejected(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    first = active(engine).active_request
    assert first is not None
    assert engine.respond("No", first.id).success  # iodine: rest 1 min, then ask again
    await clock.advance(TICK + 60 + 2 * TICK)
    second = active(engine).active_request
    assert second is not None
    assert second.id != first.id
    late = engine.respond("Yes", first.id)  # a late click on the earlier question
    assert late == Reply(False, "This request is no longer open.")
    assert late.error == "conflict"
    assert not second.answered
    assert engine.respond("Yes", second.id).success
    await engine.shutdown()


async def test_AC_052_respond_without_an_active_request_is_rejected(
    engine: Engine, clock: ManualClock
) -> None:
    assert engine.respond("Confirm") == Reply(False, "There is no active request to respond to.")
    engine.load("Dummy")
    assert engine.respond("Confirm") == Reply(False, "There is no active request to respond to.")
    engine.start()
    await clock.advance(0)
    assert engine.respond("Confirm") == Reply(False, "There is no active request to respond to.")  # HeatUp


async def test_AC_053_test_recipe_iodine_no_then_yes_finishes(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    assert respond_when_asked(engine, "No").success
    await clock.advance(TICK)
    iodine = active(engine)
    assert iodine.active_request is None
    assert iodine.description.endswith("\nRest at 25.0 degrees (C) for 1min.")
    beeps = len(hal.beeps())
    await clock.advance(60)  # the rest started one tick after the No (GOAL-005)
    assert len(hal.beeps()) == beeps  # no reminders during the additional rest
    await clock.advance(2 * TICK)  # rest over, then a new question on the next tick
    assert iodine.active_request is not None  # asked again
    assert hal.beeps()[-1] == "long_beep"  # with new reminders
    respond_when_asked(engine, "Yes")
    await clock.advance(TICK)
    assert state(engine) == "Finished"


async def test_AC_057_no_reminders_after_stop_pause_or_unload(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    engine.load("TEST")
    engine.start()
    await clock.advance(15)
    assert hal.beeps() == ["long_beep"] * 2

    engine.pause()
    await clock.advance(60)
    assert hal.beeps() == ["long_beep"] * 2  # silenced while paused
    assert active(engine).active_request is not None  # but still visible
    engine.resume()
    await clock.advance(0)
    assert hal.beeps() == ["long_beep"] * 3  # back on resume

    engine.stop()
    assert active(engine).active_request is None  # the request is removed
    await clock.advance(60)
    assert hal.beeps() == ["long_beep"] * 3
    engine.unload()
    await clock.advance(60)
    assert hal.beeps() == ["long_beep"] * 3
    assert clock.sleepers == 0


async def test_respond_while_paused_is_accepted_without_reminders(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    hal.set_temperature(25.0)
    engine.load("TEST")
    engine.start()
    await clock.advance(0)
    engine.pause()
    assert engine.respond("Yes").success
    await clock.advance(30)
    assert state(engine) == "Paused"
    engine.resume()
    await clock.advance(0)
    assert state(engine) == "Finished"


# --- fail-safe (ENG-023, FIX-22) ---


async def test_ENG_023_failing_goal_switches_the_heater_off_and_is_retried(
    hal: MockHal, clock: ManualClock, caplog: pytest.LogCaptureFixture
) -> None:
    engine = engine_with(hal, clock, '[{"type": "HeatUp", "temperature": 62}]')
    engine.load("R")
    engine.start()
    await clock.advance(1)
    assert hal.get_heating_status() == 1.0
    goal = active(engine)
    calls = 0

    def broken() -> bool:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    goal.pursue = broken  # type: ignore[method-assign]
    await clock.advance(TICK)
    assert hal.get_heating_status() == 0.0
    await clock.advance(2)
    assert calls == 5  # retried every tick
    assert state(engine) == "Running"
    assert "goal Heat Up failed" in caplog.text
    await engine.shutdown()


async def test_shutdown_ends_the_loop_and_switches_everything_off(
    engine: Engine, hal: MockHal, clock: ManualClock
) -> None:
    engine.load("Dummy")
    engine.start()
    await clock.advance(1)
    await engine.shutdown()
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)
    events = len(hal.events)
    await clock.advance(10)
    assert len(hal.events) == events
