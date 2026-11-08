"""M9: Raspberry Pi HAL with simulated GPIO and sensor (spec 06, CTL-040..046; AC-010..012, AC-020..023,
AC-117).

These tests drive the HAL logic with a recording GPIO fake, a fake libgpiod module and a manual clock. The
acceptance criteria also have to be confirmed once on real hardware (implementation plan, M9).
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from brewberrypi.clock import ManualClock
from brewberrypi.config import load_config
from brewberrypi.hal.ds18b20 import (
    SensorError,
    check_reset_value,
    find_sensor_file,
    parse_w1_slave,
    read_sensor,
)
from brewberrypi.hal.gpio import GpioError, LibgpiodOutputs, Output, find_chip
from brewberrypi.hal.raspberry_pi import RaspberryPiHal, duty_phases

CONFIG = load_config(Path(__file__).parents[1] / "docs" / "specification" / "data" / "config.example.json")
HARDWARE = CONFIG.hardware
HEATING, PUMP, BUZZER = HARDWARE.heating.pin, HARDWARE.pump.pin, HARDWARE.buzzer.pin
W1 = "72 01 4b 46 7f ff 0e 10 57 : crc=57 YES\n72 01 4b 46 7f ff 0e 10 57 t={}\n"


class RecordingOutputs:
    """Logical output states over time ("on" means the active level)."""

    def __init__(self, clock: ManualClock) -> None:
        self.clock = clock
        self.state: dict[int, bool] = {}
        self.changes: list[tuple[float, int, bool]] = []
        self.closed = False

    def set(self, pin: int, on: bool) -> None:
        if self.state.get(pin) != on:
            self.changes.append((round(self.clock.monotonic(), 3), pin, on))
        self.state[pin] = on

    def close(self) -> None:
        self.closed = True

    def timeline(self, pin: int) -> list[tuple[float, bool]]:
        return [(t, on) for t, p, on in self.changes if p == pin]


async def inline(read: Callable[[], float]) -> float:
    """Runs the sensor read on the event loop, so the manual clock fully controls the test."""
    return read()


class Sensor:
    def __init__(self) -> None:
        self.value: float | Exception = 20.0

    def __call__(self) -> float:
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def outputs(clock: ManualClock) -> RecordingOutputs:
    return RecordingOutputs(clock)


@pytest.fixture
def sensor() -> Sensor:
    return Sensor()


@pytest.fixture
async def hal(clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor) -> AsyncIterator[RaspberryPiHal]:
    hal = RaspberryPiHal(clock, HARDWARE, outputs, sensor, inline)
    yield hal
    await hal.close()


# --- duty cycle (CTL-040..046) ---


@pytest.mark.parametrize(
    ("power", "on_ms", "off_ms"),
    [
        (0.10, 10000, 90000),
        (0.25, 10000, 30000),
        (0.50, 10000, 10000),
        (0.75, 30000, 10000),
        (0.90, 90000, 10000),
    ],
)
def test_CTL_042_duty_cycle_phases(power: float, on_ms: int, off_ms: int) -> None:
    assert HARDWARE.heating.min_phase_milliseconds == 10000  # FIX-26 (legacy: 500)
    assert duty_phases(power, 10000) == (on_ms, off_ms)


async def test_AC_010_quarter_and_three_quarter_power(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.set_heating(0.25)
    await clock.advance(120)
    assert outputs.timeline(HEATING) == [
        (0, True),
        (10, False),
        (40, True),
        (50, False),
        (80, True),
        (90, False),
        (120, True),
    ]
    outputs.changes.clear()
    hal.set_heating(0.75)  # at 120 s: a new cycle, starting with a 30 s on-phase
    await clock.advance(80)
    assert outputs.timeline(HEATING) == [(150, False), (160, True), (190, False), (200, True)]


async def test_AC_011_same_power_again_does_not_restart_the_cycle(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    for _ in range(80):  # every 500 ms (one engine tick) for 40 s
        hal.set_heating(0.5)
        await clock.advance(0.5)
    assert outputs.timeline(HEATING) == [(i * 10, i % 2 == 0) for i in range(5)]  # 0 s .. 40 s


@pytest.mark.parametrize("phase_offset", [0.0, 4.0, 10.0, 14.0, 19.999])
async def test_AC_012_full_power_during_a_cycle_is_applied_at_once_and_stays(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs, phase_offset: float
) -> None:
    hal.set_heating(0.5)
    await clock.advance(phase_offset)
    hal.set_heating(1.0)
    assert outputs.state[HEATING] is True  # at once, not within a later tick (FIX-03)
    assert hal.get_heating_status() == 1.0
    changes = len(outputs.changes)
    await clock.advance(25)  # longer than one 0.5 period
    assert outputs.state[HEATING] is True
    assert len(outputs.changes) == changes  # the old cycle never touched the relay again
    hal.set_heating(0)
    assert outputs.state[HEATING] is False
    await clock.advance(25)
    assert outputs.state[HEATING] is False
    assert hal.get_heating_status() == 0.0


async def test_CTL_043_a_different_power_restarts_with_an_on_phase(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.set_heating(0.5)
    await clock.advance(14)  # in the off-phase
    hal.set_heating(0.25)
    assert outputs.state[HEATING] is True
    await clock.advance(10)
    assert outputs.state[HEATING] is False
    await clock.advance(30)
    assert outputs.state[HEATING] is True


async def test_CTL_045_status_is_the_requested_power_not_the_relay(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.set_heating(0.5)
    await clock.advance(14)
    assert outputs.state[HEATING] is False
    assert hal.get_heating_status() == 0.5
    hal.set_heating(1.7)
    assert hal.get_heating_status() == 1.0  # clamped (HW-001)


async def test_CTL_046_on_off_mode(clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor) -> None:
    hardware = replace(HARDWARE, heating=replace(HARDWARE.heating, mode="onoff"))
    hal = RaspberryPiHal(clock, hardware, outputs, sensor, inline)
    hal.set_heating(0.5)
    assert (outputs.state[HEATING], hal.get_heating_status()) == (True, 1.0)
    await clock.advance(5)
    assert outputs.timeline(HEATING) == [(0, True)]
    hal.set_heating(0)
    assert (outputs.state[HEATING], hal.get_heating_status()) == (False, 0.0)
    await hal.close()


# --- pump, buzzer, safety (HW-020, HW-021, HW-040..043) ---


async def test_pump_and_switch_everything_off(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.switch_on_pump()
    hal.set_heating(0.5)
    assert (outputs.state[PUMP], hal.get_pump_status()) == (True, True)
    hal.switch_everything_off()
    await clock.advance(3)
    assert (outputs.state[PUMP], outputs.state[HEATING]) == (False, False)
    assert (hal.get_pump_status(), hal.get_heating_status()) == (False, 0.0)


async def test_HW_040_HW_041_beep_patterns(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.short_beep()
    await clock.advance(1)
    assert outputs.timeline(BUZZER) == [(0, True), (0.5, False)]
    outputs.changes.clear()
    hal.long_beep()
    await clock.advance(7)
    assert [t - 1 for t, _ in outputs.timeline(BUZZER)] == pytest.approx(
        [0, 2.0, 2.3, 2.5, 2.8, 3.0, 3.3, 3.5, 3.8, 5.8]
    )
    assert outputs.state[BUZZER] is False


async def test_HW_043_overlapping_patterns_leave_the_buzzer_off(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs
) -> None:
    hal.long_beep()
    await clock.advance(1)
    hal.short_beep()  # replaces the long beep
    await clock.advance(0.2)
    assert outputs.state[BUZZER] is True
    await clock.advance(10)
    assert outputs.state[BUZZER] is False


async def test_HW_021_close_switches_everything_off_and_releases_the_lines(
    clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor
) -> None:
    hal = RaspberryPiHal(clock, HARDWARE, outputs, sensor, inline)
    await hal.start()
    hal.switch_on_pump()
    hal.set_heating(0.5)
    hal.long_beep()
    await clock.advance(0.1)
    await hal.close()
    assert outputs.state == {HEATING: False, PUMP: False, BUZZER: False}
    assert outputs.closed
    changes = len(outputs.changes)
    await clock.advance(30)
    assert len(outputs.changes) == changes  # no background task is left
    assert clock.sleepers == 0


# --- sensor (HW-030..034, AC-020..023) ---


@pytest.mark.parametrize(
    ("milli", "celsius"),
    [
        ("23125", 23.1),
        ("9875", 9.9),
        ("-1250", -1.3),
        ("100000", 100.0),
        ("23150", 23.2),
        ("85000", 85.0),
        ("0", 0.0),
    ],
)
def test_AC_020_w1_slave_parsing(milli: str, celsius: float) -> None:
    assert parse_w1_slave(W1.format(milli)) == celsius


@pytest.mark.parametrize(
    ("content", "cause"),
    [
        ("72 01 : crc=57 NO\n72 01 t=23125\n", "CRC check failed"),
        ("72 01 : crc=57 YES\n", "incomplete"),
        ("72 01 : crc=57 YES\n72 01 t=abc\n", "no temperature"),
        ("", "incomplete"),
    ],
)
def test_HW_031_invalid_sensor_data(content: str, cause: str) -> None:
    with pytest.raises(SensorError, match=cause):
        parse_w1_slave(content)


def test_AC_023_auto_uses_the_first_28_device(tmp_path: Path) -> None:
    for name in ("w1_bus_master1", "28-0000056291e6"):
        (tmp_path / name).mkdir()
    assert find_sensor_file(tmp_path, "auto") == tmp_path / "28-0000056291e6" / "w1_slave"
    assert find_sensor_file(tmp_path, "28-0000000000aa") == tmp_path / "28-0000000000aa" / "w1_slave"


def test_HW_013_no_sensor_device(tmp_path: Path) -> None:
    (tmp_path / "w1_bus_master1").mkdir()
    with pytest.raises(SensorError, match="no DS18B20"):
        find_sensor_file(tmp_path, "auto")
    with pytest.raises(SensorError, match="cannot list"):
        find_sensor_file(tmp_path / "missing", "auto")


def test_read_sensor_from_sysfs(tmp_path: Path) -> None:
    device = tmp_path / "28-0000056291e6"
    device.mkdir()
    (device / "w1_slave").write_text(W1.format("62500"))
    assert read_sensor(tmp_path, "auto") == 62.5
    with pytest.raises(SensorError, match="cannot read"):
        read_sensor(tmp_path, "28-000000000bad")


async def test_HW_030_sensor_loop_caches_the_reading(
    hal: RaspberryPiHal, clock: ManualClock, sensor: Sensor
) -> None:
    assert hal.get_temperature() is None  # nothing read yet
    sensor.value = 61.9
    await hal.start()
    assert hal.get_temperature() == 61.9  # first reading before start returns (HW-034)
    sensor.value = 62.0
    await clock.advance(0.1)  # pollPauseMilliseconds
    assert hal.get_temperature() == 62.0


async def test_AC_021_failed_read_is_invalid_logged_and_beeped(
    hal: RaspberryPiHal,
    clock: ManualClock,
    outputs: RecordingOutputs,
    sensor: Sensor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await hal.start()
    assert hal.get_temperature() == 20.0
    sensor.value = SensorError("CRC check failed: '... NO'")
    with caplog.at_level(logging.WARNING):
        await clock.advance(0.1)
    assert hal.get_temperature() is None
    assert "reading DS18b20 sensor failed: CRC check failed" in caplog.text
    assert outputs.state[BUZZER] is True  # error beep (HW-042)
    await clock.advance(0.5)
    assert outputs.state[BUZZER] is False
    sensor.value = 21.0
    await clock.advance(0.2)  # retried after the beep and the poll pause (HW-033)
    assert hal.get_temperature() == 21.0


async def test_AC_117_implausible_reset_value_is_skipped(
    hal: RaspberryPiHal,
    clock: ManualClock,
    outputs: RecordingOutputs,
    sensor: Sensor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensor.value = 65.0
    await hal.start()
    assert hal.get_temperature() == 65.0
    sensor.value = 85.0  # the plug moved: the sensor restarted and reports its reset value (FIX-27)
    with caplog.at_level(logging.WARNING):
        await clock.advance(0.1)
    assert hal.get_temperature() == 65.0  # skipped: the last reading stays
    assert "reading DS18b20 sensor skipped: 85.0 °C power-on reset value after 65.0 °C" in caplog.text
    assert outputs.state.get(BUZZER, False) is False  # no error beep
    sensor.value = 65.1
    await clock.advance(0.1)
    assert hal.get_temperature() == 65.1


async def test_AC_117_reset_value_for_more_than_5_s_is_a_failed_read(
    hal: RaspberryPiHal,
    clock: ManualClock,
    outputs: RecordingOutputs,
    sensor: Sensor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensor.value = 65.0
    await hal.start()
    sensor.value = 85.0
    await clock.advance(4.9)
    assert hal.get_temperature() == 65.0  # still within staleAfterSeconds
    assert outputs.state.get(BUZZER, False) is False
    with caplog.at_level(logging.WARNING):
        await clock.advance(0.3)
    assert hal.get_temperature() is None  # HW-032: heater off, Sensor error
    assert "reading DS18b20 sensor failed: 85.0 °C power-on reset value after 65.0 °C" in caplog.text
    assert outputs.state[BUZZER] is True  # error beep (HW-042)
    sensor.value = 65.2
    await clock.advance(0.6)  # after the error beep and the poll pause
    assert hal.get_temperature() == 65.2


async def test_AC_117_reset_value_next_to_the_last_reading_is_valid(
    hal: RaspberryPiHal, clock: ManualClock, sensor: Sensor
) -> None:
    sensor.value = 84.0
    await hal.start()
    sensor.value = SensorError("CRC check failed")  # a failed read in between keeps the last successful one
    await clock.advance(0.1)
    assert hal.get_temperature() is None
    sensor.value = 85.0
    await clock.advance(0.6)
    assert hal.get_temperature() == 85.0  # a kettle really heating through 85 °C


async def test_AC_117_reset_value_without_a_previous_reading_is_a_failed_read(
    hal: RaspberryPiHal, clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor
) -> None:
    sensor.value = 85.0
    start = asyncio.create_task(hal.start())
    await clock.advance(0.1)
    assert hal.get_temperature() is None  # nothing to keep: not a first reading (HW-034 keeps waiting)
    assert outputs.state[BUZZER] is True
    sensor.value = 20.0
    await clock.advance(0.5)  # after the error beep and the poll pause
    assert hal.get_temperature() == 20.0
    assert start.done()


@pytest.mark.parametrize(
    ("temperature", "last_valid", "valid"),
    [
        (85.0, 83.0, True),
        (85.0, 87.0, True),
        (85.0, 82.9, False),
        (85.0, 87.1, False),
        (85.0, None, False),
        (85.1, None, True),  # only the reset value itself is checked
        (84.9, 20.0, True),
    ],
)
def test_HW_031_reset_value_band(temperature: float, last_valid: float | None, valid: bool) -> None:
    if valid:
        check_reset_value(temperature, last_valid)
    else:
        with pytest.raises(SensorError, match="power-on reset value"):
            check_reset_value(temperature, last_valid)


async def test_AC_022_reading_older_than_5_s_is_invalid(
    clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor
) -> None:
    hung = asyncio.Event()

    async def hanging(read: Callable[[], float]) -> float:
        if hung.is_set():
            await asyncio.Event().wait()  # a read that never returns
        return read()

    hal = RaspberryPiHal(clock, HARDWARE, outputs, sensor, hanging)
    await hal.start()
    hung.set()
    await clock.advance(5.0)
    assert hal.get_temperature() == 20.0
    await clock.advance(0.2)
    assert hal.get_temperature() is None
    await hal.close()


async def test_HW_034_start_waits_at_most_the_startup_wait(
    clock: ManualClock, outputs: RecordingOutputs, sensor: Sensor
) -> None:
    sensor.value = SensorError("no DS18B20 sensor (28-*) in /sys/bus/w1/devices")
    hal = RaspberryPiHal(clock, HARDWARE, outputs, sensor, inline)
    start = asyncio.create_task(hal.start())
    await clock.advance(1.9)
    assert not start.done()
    await clock.advance(0.1)
    assert start.done()
    assert hal.get_temperature() is None
    await hal.close()


# --- libgpiod adapter (ADR-0001, HW-020) ---


class FakeGpiod:
    """The parts of the libgpiod v2 Python API the adapter uses."""

    def __init__(self, labels: dict[str, str]) -> None:
        self.labels = labels
        self.requests: list[dict[str, Any]] = []
        self.values: list[tuple[int, str]] = []
        self.released = False
        fake = self
        self.line = SimpleNamespace(
            Direction=SimpleNamespace(OUTPUT="output"),
            Value=SimpleNamespace(ACTIVE="active", INACTIVE="inactive"),
            Bias=SimpleNamespace(PULL_UP="pull-up", PULL_DOWN="pull-down"),
        )

        class Request:
            def set_value(self, pin: int, value: str) -> None:
                fake.values.append((pin, value))

            def release(self) -> None:
                fake.released = True

        class Chip:
            def __init__(self, path: str) -> None:
                self.path = path

            def __enter__(self) -> "Chip":
                return self

            def __exit__(self, *args: object) -> None:
                pass

            def get_info(self) -> SimpleNamespace:
                return SimpleNamespace(label=fake.labels[self.path])

            def request_lines(self, consumer: str, config: dict[int, Any]) -> Request:
                fake.requests.append({"path": self.path, "consumer": consumer, "config": config})
                return Request()

        self.Chip = Chip
        self.LineSettings = lambda **settings: settings


def gpiochips(dev: Path, labels: dict[str, str]) -> FakeGpiod:
    dev.mkdir(exist_ok=True)
    for name in labels:
        (dev / name).touch()
    return FakeGpiod({str(dev / name): label for name, label in labels.items()})


def test_chip_is_found_by_label(tmp_path: Path) -> None:
    pi5 = tmp_path / "pi5"
    gpiod = gpiochips(pi5, {"gpiochip0": "pinctrl-rp1", "gpiochip1": "raspberrypi-exp-gpio"})
    assert find_chip(gpiod, pi5) == pi5 / "gpiochip0"  # type: ignore[arg-type]
    pi4 = tmp_path / "pi4"
    gpiod = gpiochips(pi4, {"gpiochip0": "raspberrypi-exp-gpio", "gpiochip4": "pinctrl-bcm2711"})
    assert find_chip(gpiod, pi4) == pi4 / "gpiochip4"  # type: ignore[arg-type]


def test_no_header_chip_is_a_hardware_error(tmp_path: Path) -> None:
    gpiod = gpiochips(tmp_path, {"gpiochip0": "something-else"})
    with pytest.raises(GpioError, match="no GPIO chip"):
        find_chip(gpiod, tmp_path)  # type: ignore[arg-type]


def test_HW_020_all_outputs_are_requested_off_with_their_polarity(tmp_path: Path) -> None:
    gpiod = gpiochips(tmp_path, {"gpiochip0": "pinctrl-bcm2835"})
    outputs = LibgpiodOutputs(
        [Output(23, active_low=True), Output(22, active_low=True), Output(10, active_low=False)],
        gpiod,  # type: ignore[arg-type]
        tmp_path,
    )
    [request] = gpiod.requests
    assert request["consumer"] == "brewberrypi"
    assert request["config"] == {
        23: {"direction": "output", "active_low": True, "output_value": "inactive", "bias": "pull-up"},
        22: {"direction": "output", "active_low": True, "output_value": "inactive", "bias": "pull-up"},
        10: {"direction": "output", "active_low": False, "output_value": "inactive", "bias": "pull-down"},
    }
    outputs.set(23, True)
    outputs.set(23, False)
    assert gpiod.values == [(23, "active"), (23, "inactive")]
    outputs.close()
    assert gpiod.released


def test_missing_libgpiod_bindings_are_a_hardware_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(__import__("sys").modules, "gpiod", None)
    with pytest.raises(GpioError, match="python3-libgpiod"):
        LibgpiodOutputs([Output(23, active_low=True)])
