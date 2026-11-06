"""Configuration file: loading, validation and defaults (NFR-020, NFR-030, NFR-031, NFR-032, HW-010..013).

The file format is `data/config.schema.json`, a copy of `docs/specification/data/config.schema.json`. All
keys are optional; the defaults are the schema's `default` values, which reproduce the legacy behaviour.
A missing configuration file is written with every key at its default (Q-29). Unknown keys only produce a
warning (NFR-030); invalid values raise `ConfigError` (startup exit code 2).
"""

import copy
import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal

import fastjsonschema

from brewberrypi.files import write_file

log = logging.getLogger(__name__)

DEFAULT_CONFIG_FILE = "config.json"


class ConfigError(Exception):
    """The configuration file cannot be read or is invalid; the application refuses to start."""


@dataclass(frozen=True)
class ServerConfig:
    port: int


@dataclass(frozen=True)
class RecipesConfig:
    directory: Path
    """Recipe directory, one file per recipe, resolved against the configuration file's directory (Q-19)."""
    check_milliseconds: int
    """How often the recipe directory is checked for changed files (RCP-006)."""


@dataclass(frozen=True)
class OutputConfig:
    pin: int
    """BCM GPIO number."""
    active_low: bool


@dataclass(frozen=True)
class HeatingConfig(OutputConfig):
    mode: Literal["dutycycle", "onoff"]
    min_phase_milliseconds: int


@dataclass(frozen=True)
class SensorConfig:
    sensor_id: str
    """`"auto"` or a DS18B20 id such as `28-0000056291e6` (HW-013)."""
    devices_path: Path
    poll_pause_milliseconds: int
    stale_after_seconds: float
    startup_wait_milliseconds: int


@dataclass(frozen=True)
class HardwareConfig:
    heating: HeatingConfig
    pump: OutputConfig
    buzzer: OutputConfig
    sensor: SensorConfig


@dataclass(frozen=True)
class ControlConfig:
    hysteresis_upper_offset: float
    hysteresis_lower_offset: float
    near_target_band: float
    near_target_power: float


@dataclass(frozen=True)
class EngineConfig:
    tick_milliseconds: int


@dataclass(frozen=True)
class UiConfig:
    request_reminder_interval_seconds: float
    device_link: str | None
    """Link to the device web interface with `{host}` still in it (UI-002); `None`: no link."""


@dataclass(frozen=True)
class EventsConfig:
    check_milliseconds: int
    """How often the server looks for changed state to push (IF-072)."""
    heartbeat_milliseconds: int
    """Period of the `ping` event (IF-074)."""


@dataclass(frozen=True)
class ChartConfig:
    max_points: int
    sample_milliseconds: int


@dataclass(frozen=True)
class SimulationConfig:
    time_scale: float
    start_temperature: float
    ambient_temperature: float
    heating_rate_per_second: float
    cooling_rate_per_second: float
    max_temperature: float


@dataclass(frozen=True)
class CloudSyncConfig:
    """Cloud Sync (spec 14)."""

    enabled: bool
    interval_seconds: int
    """Time between syncs (SYNC-020)."""
    google_drive_client_id: str
    """Empty: the client built into the application (SYNC-001)."""
    google_drive_client_secret: str
    state_directory: Path
    """Where the sign-in is stored: the configuration file's directory (SYNC-004)."""


@dataclass(frozen=True)
class Config:
    source: Path
    """The file the configuration was read from."""
    server: ServerConfig
    recipes: RecipesConfig
    hardware: HardwareConfig
    control: ControlConfig
    engine: EngineConfig
    ui: UiConfig
    events: EventsConfig
    chart: ChartConfig
    simulation: SimulationConfig
    cloud_sync: CloudSyncConfig


def load_config(path: Path | None = None, *, cwd: Path | None = None) -> Config:
    """Read the configuration (NFR-020).

    `path` is the `--config` argument, by default `config.json` in the working directory. A missing file is
    first written with every key at its default (NFR-032, Q-29); if that fails, `ConfigError`.
    """
    cwd = Path.cwd() if cwd is None else cwd
    path = cwd / (DEFAULT_CONFIG_FILE if path is None else path)
    if not path.exists():
        try:
            write_file(path, default_config_text())
        except OSError as error:
            raise ConfigError(f"{path}: cannot create configuration file: {error.strerror}") from None
        log.info("configuration file %s created with the default values", path)
    raw = _read_json(path)
    log.info("configuration read from %s", path)
    return _build(raw, source=path, base_dir=path.parent)


def default_config_text() -> str:
    """The configuration file written when there is none: every key with its default (= config.example.json
    without `$schema`)."""
    return json.dumps(_DEFAULTS, indent=2, ensure_ascii=False) + "\n"


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path}: configuration file not found") from None
    except OSError as error:
        raise ConfigError(f"{path}: cannot read configuration file: {error.strerror}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path}:{error.lineno}:{error.colno}: invalid JSON: {error.msg}") from None


def _build(raw: Any, *, source: Path, base_dir: Path) -> Config:
    name = str(source)
    if not isinstance(raw, dict):
        raise ConfigError(f"{name}: the configuration must be a JSON object")
    known = _strip_unknown_keys(raw, _SCHEMA, "", name)
    try:
        _validate(known)
    except fastjsonschema.JsonSchemaValueException as error:
        raise ConfigError(f"{name}: {_describe(error)}") from None
    values = _merge(_DEFAULTS, known)

    def section(key: str) -> dict[str, Any]:
        result: dict[str, Any] = values[key]
        return result

    hardware = section("hardware")
    heating, pump, buzzer, sensor = (hardware[k] for k in ("heating", "pump", "buzzer", "sensor"))
    control, events, simulation = section("control"), section("events"), section("simulation")
    cloud_sync = section("cloudSync")
    return Config(
        source=source,
        server=ServerConfig(port=section("server")["port"]),
        recipes=RecipesConfig(
            directory=base_dir / section("recipes")["directory"],
            check_milliseconds=section("recipes")["checkMilliseconds"],
        ),
        hardware=HardwareConfig(
            heating=HeatingConfig(
                pin=heating["pin"],
                active_low=heating["activeLow"],
                mode=heating["mode"],
                min_phase_milliseconds=heating["minPhaseMilliseconds"],
            ),
            pump=OutputConfig(pin=pump["pin"], active_low=pump["activeLow"]),
            buzzer=OutputConfig(pin=buzzer["pin"], active_low=buzzer["activeLow"]),
            sensor=SensorConfig(
                sensor_id=sensor["sensorId"],
                devices_path=Path(sensor["devicesPath"]),
                poll_pause_milliseconds=sensor["pollPauseMilliseconds"],
                stale_after_seconds=sensor["staleAfterSeconds"],
                startup_wait_milliseconds=sensor["startupWaitMilliseconds"],
            ),
        ),
        control=ControlConfig(
            hysteresis_upper_offset=control["hysteresisUpperOffset"],
            hysteresis_lower_offset=control["hysteresisLowerOffset"],
            near_target_band=control["nearTargetBand"],
            near_target_power=control["nearTargetPower"],
        ),
        engine=EngineConfig(tick_milliseconds=section("engine")["tickMilliseconds"]),
        ui=UiConfig(
            request_reminder_interval_seconds=section("ui")["requestReminderIntervalSeconds"],
            device_link=section("ui")["deviceLink"] or None,
        ),
        events=EventsConfig(
            check_milliseconds=events["checkMilliseconds"],
            heartbeat_milliseconds=events["heartbeatMilliseconds"],
        ),
        chart=ChartConfig(
            max_points=section("chart")["maxPoints"],
            sample_milliseconds=section("chart")["sampleMilliseconds"],
        ),
        simulation=SimulationConfig(
            time_scale=simulation["timeScale"],
            start_temperature=simulation["startTemperature"],
            ambient_temperature=simulation["ambientTemperature"],
            heating_rate_per_second=simulation["heatingRatePerSecond"],
            cooling_rate_per_second=simulation["coolingRatePerSecond"],
            max_temperature=simulation["maxTemperature"],
        ),
        cloud_sync=CloudSyncConfig(
            enabled=cloud_sync["enabled"],
            interval_seconds=cloud_sync["intervalSeconds"],
            google_drive_client_id=cloud_sync["googleDrive"]["clientId"],
            google_drive_client_secret=cloud_sync["googleDrive"]["clientSecret"],
            state_directory=base_dir,
        ),
    )


def _strip_unknown_keys(
    value: dict[str, Any], schema: dict[str, Any], path: str, name: str
) -> dict[str, Any]:
    """Copy of `value` without keys the schema does not know; each one is logged as a warning (NFR-030)."""
    properties: dict[str, Any] = schema.get("properties", {})
    result: dict[str, Any] = {}
    for key, item in value.items():
        if key not in properties:
            log.warning("%s: unknown configuration key %s%s is ignored", name, path, key)
        elif isinstance(item, dict) and "properties" in properties[key]:
            result[key] = _strip_unknown_keys(item, properties[key], f"{path}{key}.", name)
        else:
            result[key] = item
    return result


def _schema_defaults(schema: dict[str, Any]) -> dict[str, Any]:
    """The schema's `default` values as a nested object (= `config.example.json` without `$schema`)."""
    result: dict[str, Any] = {}
    for key, prop in schema.get("properties", {}).items():
        if "properties" in prop:
            result[key] = _schema_defaults(prop)
        elif "default" in prop:
            result[key] = prop["default"]
    return result


def _merge(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _describe(error: fastjsonschema.JsonSchemaValueException) -> str:
    """`data.hardware.heating.pin must be ...` → `hardware.heating.pin must be ...`."""
    message: str = error.message
    return message.removeprefix("data.").removeprefix("data ")


_SCHEMA: dict[str, Any] = json.loads(
    files("brewberrypi").joinpath("data/config.schema.json").read_text("utf-8")
)
_DEFAULTS = _schema_defaults(_SCHEMA)
_validate = fastjsonschema.compile(_SCHEMA)
