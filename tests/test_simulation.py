"""M6: simulation HAL, beeps and time scale (HW-060..064, AC-090 accelerated)."""

import asyncio
import io
import itertools
import logging
import threading
import wave
from pathlib import Path

import pytest

from brewberrypi.clock import ManualClock, ScaledClock
from brewberrypi.config import SimulationConfig, load_config
from brewberrypi.engine import Engine
from brewberrypi.hal import LONG_BEEP, SHORT_BEEP, round_temperature, sound
from brewberrypi.hal.simulation import SimulationHal
from recipe_fixtures import with_test_recipes

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
SIMULATION = CONFIG.simulation


def kettle(clock: ManualClock, **changes: float) -> SimulationHal:
    config = SimulationConfig(**{**SIMULATION.__dict__, **changes})
    return SimulationHal(clock, config)


# --- physics (HW-060, HW-061) ---


def test_HW_060_starts_at_20_degrees_with_everything_off() -> None:
    hal = kettle(ManualClock())
    assert hal.get_temperature() == 20.0
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)


async def test_HW_060_heats_by_0_085_per_second_times_power() -> None:
    clock = ManualClock()
    hal = kettle(clock)
    hal.set_heating(1.0)
    await clock.advance(100)
    assert hal.get_temperature() == 28.5
    hal.set_heating(0.5)
    await clock.advance(100)
    assert hal.get_temperature() == 32.8  # 28.5 + 4.25, rounded half-up


async def test_HW_060_updates_in_whole_seconds() -> None:
    clock = ManualClock()
    hal = kettle(clock)
    hal.set_heating(1.0)
    await clock.advance(0.9)
    assert hal.get_temperature() == 20.0
    await clock.advance(0.1)
    assert hal.get_temperature() == 20.1  # 20.085


async def test_HW_060_cools_by_0_005_per_second_down_to_ambient() -> None:
    clock = ManualClock()
    hal = kettle(clock, start_temperature=21.0)
    await clock.advance(100)
    assert hal.get_temperature() == 20.5
    await clock.advance(1000)
    assert hal.get_temperature() == 20.0


async def test_HW_060_no_heating_above_100_degrees() -> None:
    clock = ManualClock()
    hal = kettle(clock, start_temperature=99.95)
    hal.set_heating(1.0)
    await clock.advance(1)  # 99.95 <= 100: heats to 100.035
    await clock.advance(10)
    assert hal.get_temperature() == 100.0


async def test_power_set_during_a_second_counts_from_that_second() -> None:
    clock = ManualClock()
    hal = kettle(clock)
    await clock.advance(10)  # 10 s off at ambient
    hal.set_heating(1.0)
    await clock.advance(10)
    assert hal.get_temperature() == 20.9  # 20 + 10 · 0.085 = 20.85


def test_HW_061_pump_and_heating_in_memory_with_clamping() -> None:
    hal = kettle(ManualClock())
    hal.switch_on_pump()
    hal.set_heating(1.5)
    assert (hal.get_pump_status(), hal.get_heating_status()) == (True, 1.0)
    hal.switch_everything_off()
    assert (hal.get_pump_status(), hal.get_heating_status()) == (False, 0.0)


@pytest.mark.parametrize(("value", "rounded"), [(23.125, 23.1), (23.15, 23.2), (-1.25, -1.3), (32.75, 32.8)])
def test_HW_060_temperature_is_rounded_like_the_sensor(value: float, rounded: float) -> None:
    assert round_temperature(value) == rounded


def test_HW_063_state_changes_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    hal = kettle(ManualClock())
    with caplog.at_level(logging.INFO, logger="brewberrypi.hal.simulation"):
        caplog.clear()
        hal.switch_on_pump()
        hal.switch_on_pump()
        hal.set_heating(0.5)
        hal.set_heating(0.5)
        hal.switch_everything_off()
    assert caplog.messages == [
        "change pump status to on",
        "change heating status to 0.5",
        "change heating status to 0.0",
        "change pump status to off",
    ]


# --- beeps (HW-062) ---


def test_HW_062_beeps_are_played_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    played: list[bytes] = []
    hal = SimulationHal(ManualClock(), SIMULATION, played.append)
    with caplog.at_level(logging.INFO, logger="brewberrypi.hal.simulation"):
        caplog.clear()
        hal.short_beep()
        hal.long_beep()
    assert caplog.messages == ["beep: short", "beep: long"]
    assert played == [sound.pattern_wav(SHORT_BEEP), sound.pattern_wav(LONG_BEEP)]


def test_HW_062_without_audio_beeps_are_only_logged(caplog: pytest.LogCaptureFixture) -> None:
    hal = SimulationHal(ManualClock(), SIMULATION, None)
    with caplog.at_level(logging.INFO, logger="brewberrypi.hal.simulation"):
        hal.long_beep()
    assert "beep: long" in caplog.messages


@pytest.mark.parametrize(("pattern", "seconds"), [(SHORT_BEEP, 0.5), (LONG_BEEP, 5.8)])
def test_HW_062_tone_has_the_pattern_length(pattern: tuple[tuple[bool, int], ...], seconds: float) -> None:
    with wave.open(io.BytesIO(sound.pattern_wav(pattern))) as wav:
        assert wav.getnframes() / wav.getframerate() == seconds
        frames = wav.readframes(wav.getnframes())
    assert max(frames) > 200  # a tone, not silence


def test_HW_062_long_beep_uses_the_HW_041_timing() -> None:
    assert [ms for _, ms in LONG_BEEP] == [2000, 300, 200, 300, 200, 300, 200, 300, 2000]
    assert [on for on, _ in LONG_BEEP] == [True, False] * 4 + [True]


def test_find_player_without_any_player(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("brewberrypi.hal.sound.sys.platform", "linux")
    monkeypatch.setattr("brewberrypi.hal.sound.shutil.which", lambda name: None)
    assert sound.find_player() is None


def test_find_player_prefers_paplay_and_plays_in_the_background(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("brewberrypi.hal.sound.sys.platform", "linux")
    monkeypatch.setattr(
        "brewberrypi.hal.sound.shutil.which",
        lambda name: f"/usr/bin/{name}" if name in ("aplay", "paplay") else None,
    )
    calls: list[list[str]] = []
    finished = threading.Event()

    def run(command: list[str], **kwargs: object) -> None:
        calls.append(command)
        finished.set()

    monkeypatch.setattr("brewberrypi.hal.sound.subprocess.run", run)
    player = sound.find_player()
    assert player is not None
    player(b"wav")
    assert finished.wait(5)
    assert calls == [["/usr/bin/paplay"]]


def test_a_failing_player_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(wav: bytes) -> None:
        raise OSError("no audio device")

    sound._guarded(broken, b"wav")  # logged at debug level only


# --- time scale (HW-064) ---


async def test_HW_064_scaled_clock_accelerates_time_and_sleep() -> None:
    base = ManualClock(start=100, wall_start=1000)
    clock = ScaledClock(base, 60)
    assert clock.monotonic() == 100
    await base.advance(1)
    assert clock.monotonic() == 160
    assert clock.wall_time() == 1060
    task = asyncio.create_task(clock.sleep(600))  # 10 simulated minutes = 10 real seconds
    await base.advance(9.9)
    assert not task.done()
    await base.advance(0.1)
    assert task.done()


def test_HW_064_time_scale_below_1_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        ScaledClock(ManualClock(), 0.5)


async def test_HW_064_simulation_physics_follows_the_scaled_clock() -> None:
    base = ManualClock()
    clock = ScaledClock(base, 10)
    hal = SimulationHal(clock, SIMULATION)
    hal.set_heating(1.0)
    await base.advance(10)  # 100 simulated seconds
    assert hal.get_temperature() == 28.5


# --- end to end in simulation (AC-090) ---


async def test_AC_090_demo_recipe_runs_to_finished_in_simulation() -> None:
    """The test recipe DEMO with simulated physics and accelerated (manual) time.

    The chart (M7) will plot the temperature broadcast every second; this test samples the same values.
    """
    clock = ManualClock()
    hal = SimulationHal(clock, SIMULATION)
    engine = Engine(hal, clock, with_test_recipes(CONFIG), CONFIG)
    assert engine.load("DEMO").success
    assert engine.start().success
    assert engine.process is not None
    goals = engine.process.goals

    trace: list[tuple[float, float, str]] = []  # (time, temperature, active goal)
    lautering_since: float | None = None
    while engine.state != "Finished" and clock.monotonic() < 4 * 3600:
        await clock.advance(1)
        goal = engine.process.active_goal()
        temperature = hal.get_temperature()
        assert temperature is not None
        trace.append((clock.monotonic(), temperature, "" if goal is None else goal.name))
        request = None if goal is None else goal.active_request
        if request is not None and not request.answered:
            if goal is not None and goal.name == "Lautering":
                lautering_since = lautering_since or clock.monotonic()
                if clock.monotonic() - lautering_since < 300:  # let it cool for 5 minutes
                    continue
            assert engine.respond("Yes" if "Yes" in request.options else "Confirm").success

    assert engine.state == "Finished"
    assert [goal.status for goal in goals] == ["Done"] * 6
    assert (hal.get_heating_status(), hal.get_pump_status()) == (0.0, False)

    def phase(name: str) -> list[float]:
        return [t for _, t, g in trace if g == name]

    assert trace[-1][1] >= 97.0  # heat-up to 97 ends the process
    rest = phase("Rest")
    assert 10 * 60 <= len(rest) <= 10 * 60 + 2  # a hold for 10 simulated minutes
    assert all(61.8 <= t <= 62.2 for t in rest)  # at 62
    first_rest = next(i for i, (_, _, g) in enumerate(trace) if g == "Rest")
    assert max(t for _, t, _ in trace[: first_rest + 1]) >= 62.0  # heat-up to 62 before the rest
    first_lautering = next(i for i, (_, _, g) in enumerate(trace) if g == "Lautering")
    assert max(t for _, t, _ in trace[first_rest : first_lautering + 1]) >= 78.0  # heat-up to 78
    lautering = phase("Lautering")
    assert lautering[-1] < lautering[0] - 1.0  # cool-down during lautering
    assert all(b <= a for a, b in itertools.pairwise(lautering))
    await engine.shutdown()
