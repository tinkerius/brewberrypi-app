"""Raspberry Pi HAL: relays and buzzer on GPIO, DS18B20 sensor (spec 06, CTL-040..046).

Background work runs as asyncio tasks on the application clock: the heating duty cycle, buzzer patterns and
the sensor loop (whose blocking file read runs in a worker thread). The HW-001 operations are called from the
event loop (engine, command handlers, event stream, shutdown), which serialises them (HW-003, ENG-040).
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from decimal import ROUND_HALF_UP, Decimal

from brewberrypi.clock import Clock
from brewberrypi.config import HardwareConfig
from brewberrypi.hal import ERROR_BEEP, LONG_BEEP, SHORT_BEEP, BeepPattern, Hal, Temperature, clamp_power
from brewberrypi.hal.ds18b20 import ImplausibleResetValue, SensorError, check_reset_value, read_sensor
from brewberrypi.hal.gpio import LibgpiodOutputs, Output, Outputs

log = logging.getLogger(__name__)

BlockingRunner = Callable[[Callable[[], float]], Awaitable[float]]


def duty_phases(power: float, min_phase_ms: int) -> tuple[int, int]:
    """(on, off) milliseconds of the time-proportional duty cycle for 0 < power < 1 (CTL-042)."""
    if power < 0.5:  # noqa: PLR2004
        return min_phase_ms, _round(min_phase_ms / power - min_phase_ms)
    return _round(min_phase_ms / (1 - power) - min_phase_ms), min_phase_ms


def _round(value: float) -> int:
    return int(Decimal(repr(value)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class HeatingRelay:
    """Turns a heating power into relay switching (CTL-040..046)."""

    def __init__(self, outputs: Outputs, pin: int, mode: str, min_phase_ms: int, clock: Clock) -> None:
        self._outputs = outputs
        self._pin = pin
        self._mode = mode
        self._min_phase_ms = min_phase_ms
        self._clock = clock
        self.power = 0.0
        """The last requested power (CTL-045); 1/0 in on/off mode (CTL-046)."""
        self._cycle: asyncio.Task[None] | None = None

    def set_power(self, requested: float) -> None:
        """Apply a power atomically: when this returns, the relay matches it (CTL-044, FIX-03)."""
        power = clamp_power(requested)
        if self._mode == "onoff":
            power = 1.0 if power > 0 else 0.0
        if 0 < power < 1:
            if power == self.power and self._cycle is not None:
                return  # the same power does not restart the cycle (CTL-043)
            self._stop_cycle()
            self.power = power
            on_ms, off_ms = duty_phases(power, self._min_phase_ms)
            self._relay(True)  # a new cycle starts with an on-phase
            self._cycle = asyncio.get_running_loop().create_task(self._run(on_ms, off_ms))
        else:
            self._stop_cycle()
            self.power = power
            self._relay(power >= 1)  # CTL-040, CTL-041

    def stop(self) -> None:
        """End the cycle without touching the relay (shutdown)."""
        self._stop_cycle()

    def _stop_cycle(self) -> None:
        if self._cycle is not None:
            # A cancelled cycle never writes the relay again: it is cancelled at its sleep.
            self._cycle.cancel()
            self._cycle = None

    async def _run(self, on_ms: int, off_ms: int) -> None:
        while True:
            await self._clock.sleep(on_ms / 1000)
            self._relay(False)
            await self._clock.sleep(off_ms / 1000)
            self._relay(True)

    def _relay(self, on: bool) -> None:
        self._outputs.set(self._pin, on)


class Buzzer:
    """Plays beep patterns (HW-040..043). A new pattern replaces a running one; the buzzer ends off."""

    def __init__(self, outputs: Outputs, pin: int, clock: Clock) -> None:
        self._outputs = outputs
        self._pin = pin
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    def play(self, pattern: BeepPattern) -> asyncio.Task[None]:
        if self._task is not None:
            self._task.cancel()
        task = asyncio.get_running_loop().create_task(self._run(pattern))
        self._task = task
        return task

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self, pattern: BeepPattern) -> None:
        me = asyncio.current_task()
        try:
            for on, milliseconds in pattern:
                self._outputs.set(self._pin, on)
                await self._clock.sleep(milliseconds / 1000)
        finally:
            if self._task is me:  # a replaced pattern leaves the buzzer to its successor
                self._outputs.set(self._pin, False)
                self._task = None


class RaspberryPiHal(Hal):
    def __init__(
        self,
        clock: Clock,
        hardware: HardwareConfig,
        outputs: Outputs | None = None,
        read: Callable[[], float] | None = None,
        run_blocking: BlockingRunner = asyncio.to_thread,
    ) -> None:
        heating, pump, buzzer, sensor = hardware.heating, hardware.pump, hardware.buzzer, hardware.sensor
        # First of all: every output requested in its off state (HW-020, NFR-002).
        self._outputs = (
            outputs
            if outputs is not None
            else LibgpiodOutputs([Output(o.pin, o.active_low) for o in (heating, pump, buzzer)])
        )
        self._clock = clock
        self._heating = HeatingRelay(
            self._outputs, heating.pin, heating.mode, heating.min_phase_milliseconds, clock
        )
        self._buzzer = Buzzer(self._outputs, buzzer.pin, clock)
        self._buzzer_pin = buzzer.pin
        self._pump_pin = pump.pin
        self._pump = False
        self._read = read if read is not None else lambda: read_sensor(sensor.devices_path, sensor.sensor_id)
        self._run_blocking = run_blocking
        self._poll_pause = sensor.poll_pause_milliseconds / 1000
        self._stale_after = sensor.stale_after_seconds
        self._startup_wait = sensor.startup_wait_milliseconds / 1000
        self._reading: tuple[float, float] | None = None
        """(temperature, monotonic time) of the last successful read; `None` after a failed read."""
        self._last_valid: float | None = None
        """The last successful reading, kept across failed reads (power-on reset value check, HW-031)."""
        self._first_reading = asyncio.Event()
        self._sensor_task: asyncio.Task[None] | None = None

    # --- HW-001 operations ---

    def switch_on_pump(self) -> None:
        self._set_pump(True)

    def switch_off_pump(self) -> None:
        self._set_pump(False)

    def get_pump_status(self) -> bool:
        return self._pump

    def set_heating(self, power: float) -> None:
        before = self._heating.power
        self._heating.set_power(power)
        if self._heating.power != before:
            log.info("change heating status to %s", self._heating.power)  # NFR-041

    def get_heating_status(self) -> float:
        return self._heating.power

    def get_temperature(self) -> Temperature:
        """The cached reading; INVALID after a failed read or when older than staleAfterSeconds (HW-032)."""
        if self._reading is None:
            return None
        temperature, read_at = self._reading
        if self._clock.monotonic() - read_at > self._stale_after:
            return None
        return temperature

    def short_beep(self) -> None:
        self._buzzer.play(SHORT_BEEP)

    def long_beep(self) -> None:
        self._buzzer.play(LONG_BEEP)

    def switch_everything_off(self) -> None:
        self.set_heating(0)
        self._set_pump(False)

    # --- lifecycle ---

    async def start(self) -> None:
        """Start the sensor loop; wait up to startupWaitMilliseconds for a first reading (HW-030, HW-034)."""
        self._sensor_task = asyncio.get_running_loop().create_task(self._sensor_loop())
        first = asyncio.ensure_future(self._first_reading.wait())
        timeout = asyncio.ensure_future(self._clock.sleep(self._startup_wait))
        await asyncio.wait({first, timeout}, return_when=asyncio.FIRST_COMPLETED)
        for waiter in (first, timeout):
            waiter.cancel()
        if self._reading is None:
            log.warning("no temperature reading within %.1f s after start", self._startup_wait)

    async def close(self) -> None:
        """Heating, pump and buzzer off, background work ended, GPIO released (HW-021, NFR-022)."""
        tasks = [task for task in (self._sensor_task,) if task is not None]
        for task in tasks:
            task.cancel()
        self._heating.stop()
        self._buzzer.stop()
        self.switch_everything_off()
        self._outputs.set(self._buzzer_pin, False)
        await asyncio.gather(*tasks, return_exceptions=True)
        self._outputs.close()

    # --- internals ---

    def _set_pump(self, on: bool) -> None:
        if on != self._pump:
            log.info("change pump status to %s", "on" if on else "off")
        self._pump = on
        self._outputs.set(self._pump_pin, on)

    async def _sensor_loop(self) -> None:
        """Read continuously and cache; a failed read is logged, cached as INVALID and beeped (HW-033).

        An implausible 85.0 °C (the power-on reset value) is logged and skipped: the cached reading stays as
        it is while it is fresh, and the reset value counts as a failed read once it is not (HW-031, FIX-27).
        """
        while True:
            try:
                temperature = await self._run_blocking(self._read)
                check_reset_value(temperature, self._last_valid)  # FIX-27
            except SensorError as error:
                if isinstance(error, ImplausibleResetValue) and self.get_temperature() is not None:
                    log.warning("reading DS18b20 sensor skipped: %s", error)  # the cached reading stays
                else:
                    log.warning("reading DS18b20 sensor failed: %s", error)  # NFR-041
                    self._reading = None
                    # Synchronous: the error beep throttles retries (HW-042).
                    await asyncio.wait({self._buzzer.play(ERROR_BEEP)})
            else:
                self._last_valid = temperature
                self._reading = (temperature, self._clock.monotonic())
                self._first_reading.set()
            await self._clock.sleep(self._poll_pause)
