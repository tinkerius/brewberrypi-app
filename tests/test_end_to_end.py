"""M12: end to end in simulation (11 §11.8). AC-090 (DEMO) is in test_simulation.py; this is AC-091
(BENIWEISS).

The engine runs a whole recipe against the simulation HAL with simulated physics; the test plays the brewer
and answers every request. Time is the manual clock, so 110 minutes of rests take a few seconds.
"""

from pathlib import Path

from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.engine import Engine
from brewberrypi.hal.simulation import SimulationHal
from recipe_fixtures import with_test_recipes

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
CATALOGUE = with_test_recipes(CONFIG)
LIMIT_SECONDS = 6 * 3600


async def brew(recipe_id: str) -> tuple[Engine, SimulationHal, list[tuple[float, float, str]]]:
    """Run a recipe to its end, answering Confirm and Yes at once; returns the (time, °C, goal) trace."""
    clock = ManualClock()
    hal = SimulationHal(clock, CONFIG.simulation)
    engine = Engine(hal, clock, CATALOGUE, CONFIG)
    assert engine.load(recipe_id).success
    assert engine.start().success
    assert engine.process is not None
    trace: list[tuple[float, float, str]] = []
    while engine.state == "Running" and clock.monotonic() < LIMIT_SECONDS:
        await clock.advance(1)
        goal = engine.process.active_goal()
        temperature = hal.get_temperature()
        assert temperature is not None  # the simulation has no sensor errors
        trace.append((clock.monotonic(), temperature, "" if goal is None else goal.name))
        request = None if goal is None else goal.active_request
        if request is not None and not request.answered:
            assert engine.respond("Yes" if "Yes" in request.options else "Confirm").success
    await engine.shutdown()
    return engine, hal, trace


def rest_samples(trace: list[tuple[float, float, str]], name: str) -> list[float]:
    return [temperature for _, temperature, goal in trace if goal == name]


async def test_AC_091_full_beniweiss_run_in_simulation() -> None:
    engine, hal, trace = await brew("BENIWEISS")
    assert engine.state == "Finished"
    assert engine.process is not None
    assert [goal.status for goal in engine.process.goals] == ["Done"] * 11
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)

    # The rests were held at their temperatures for their durations (GOAL-004, CTL-011).
    rests = (("Eiweißrast", 52, 30), ("Maltoserastr", 65, 30), ("Verzuckerungsrast", 73, 40))
    for name, target, minutes in rests:
        samples = rest_samples(trace, name)
        assert minutes * 60 <= len(samples) <= minutes * 60 + 2, name
        assert all(target - 0.3 <= t <= target + 0.3 for t in samples), name
    assert trace[-1][1] >= 78.0  # the last HeatUp reached 78 °C
    assert trace[-1][0] < 3 * 3600  # in under three simulated hours
