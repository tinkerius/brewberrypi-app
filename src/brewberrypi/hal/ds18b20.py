"""DS18B20 temperature sensor via the Linux 1-Wire sysfs interface (HW-013, HW-031).

A `w1_slave` file looks like this:

    72 01 4b 46 7f ff 0e 10 57 : crc=57 YES
    72 01 4b 46 7f ff 0e 10 57 t=23125

The first line must end in `YES`; the temperature is the integer after `t=` in milli-°C, rounded to one
decimal half-up on the exact decimal value (FIX-01: legacy took the last 6 characters and read 100.000 °C as
0.0 °C).

85.0 °C is also the sensor's power-on reset value, reported with a valid CRC after a brief loss of power (a
moving plug); it is accepted only next to the last successful reading, otherwise skipped (FIX-27, Q-06).
"""

import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

AUTO = "auto"
FAMILY_PREFIX = "28-"
_TEMPERATURE = re.compile(r"t=(-?\d+)\s*$")
RESET_VALUE = 85.0
"""°C the DS18B20 holds after power-up (HW-031)."""
RESET_VALUE_BAND = 2.0
"""K: 85.0 °C is plausible only this close to the last successful reading (HW-031, FIX-27)."""


class SensorError(Exception):
    """No valid reading; the message is the cause (logged with the warning, HW-033)."""


class ImplausibleResetValue(SensorError):
    """85.0 °C away from the last successful reading: skipped while that reading is fresh (HW-031, FIX-27)."""


def parse_w1_slave(content: str) -> float:
    """Temperature in °C with one decimal (HW-031)."""
    lines = content.strip().splitlines()
    if len(lines) < 2:  # noqa: PLR2004  (CRC line and data line)
        raise SensorError("incomplete sensor data")
    if not lines[0].rstrip().endswith("YES"):
        raise SensorError(f"CRC check failed: {lines[0].strip()!r}")
    match = _TEMPERATURE.search(lines[1])
    if match is None:
        raise SensorError(f"no temperature in {lines[1].strip()!r}")
    milli = Decimal(int(match.group(1)))
    return float((milli / 1000).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def check_reset_value(temperature: float, last_valid: float | None) -> None:
    """Reject 85.0 °C unless the last successful reading was within RESET_VALUE_BAND (HW-031, FIX-27)."""
    if temperature != RESET_VALUE:
        return
    if last_valid is None:
        raise ImplausibleResetValue("85.0 °C power-on reset value without a previous reading")
    if abs(last_valid - RESET_VALUE) > RESET_VALUE_BAND:
        raise ImplausibleResetValue(f"85.0 °C power-on reset value after {last_valid} °C")


def find_sensor_file(devices_path: Path, sensor_id: str) -> Path:
    """`<devices>/<id>/w1_slave`; with `auto` the first device whose name starts with `28-` (HW-013)."""
    if sensor_id != AUTO:
        return devices_path / sensor_id / "w1_slave"
    try:
        candidates = sorted(p.name for p in devices_path.iterdir() if p.name.startswith(FAMILY_PREFIX))
    except OSError as error:
        raise SensorError(f"cannot list {devices_path}: {error.strerror}") from None
    if not candidates:
        raise SensorError(f"no DS18B20 sensor (28-*) in {devices_path}")
    return devices_path / candidates[0] / "w1_slave"


def read_sensor(devices_path: Path, sensor_id: str) -> float:
    """One blocking read (about 750 ms on the device); run it in a worker thread."""
    file = find_sensor_file(devices_path, sensor_id)
    try:
        content = file.read_text(encoding="ascii", errors="replace")
    except OSError as error:
        raise SensorError(f"cannot read {file}: {error.strerror}") from None
    return parse_w1_slave(content)
