"""Startup (NFR-021) with the exit codes of the runtime contract."""

import json
from pathlib import Path

import pytest
from starlette.applications import Starlette

from brewberrypi.__main__ import (
    EXIT_ERROR,
    EXIT_HARDWARE,
    EXIT_INVALID_CONFIG,
    EXIT_INVALID_RECIPES,
    EXIT_OK,
    main,
)
from brewberrypi.__main__ import serve as real_serve


@pytest.fixture(autouse=True)
def empty_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def served(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Records the port instead of running the web server."""
    ports: list[int] = []

    def serve(app: Starlette, port: int) -> int:
        ports.append(port)
        return EXIT_OK

    monkeypatch.setattr("brewberrypi.__main__.serve", serve)
    return ports


def test_simulation_serves_the_app_on_the_configured_port(tmp_path: Path, served: list[int]) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"server": {"port": 9090}}))
    assert main(["--dummymode"]) == EXIT_OK
    assert served == [9090]


def test_without_dummymode_hardware_initialisation_fails_until_M9(served: list[int]) -> None:
    assert main([]) == EXIT_HARDWARE
    assert served == []


def test_AC_084_AC_086_first_start_writes_the_config_file_and_the_recipes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--dummymode", "--config", "config.json"]) == EXIT_OK
    assert "recipes: BENIWEISS, Dummy, HANCOCK, HIPA, TEST, WIT, WaterBoil, ZORA" in capsys.readouterr().out
    assert sorted(path.name for path in tmp_path.iterdir()) == ["config.json", "recipes"]
    assert len(list((tmp_path / "recipes").iterdir())) == 8


def test_log_lines_have_timestamp_level_and_component(capsys: pytest.CaptureFixture[str]) -> None:
    main(["--dummymode"])
    line = capsys.readouterr().out.splitlines()[0]
    assert " INFO brewberrypi: BrewberryPi " in line
    assert line[:4].isdigit()


def test_invalid_configuration_exits_with_code_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"server": {"port": "eighty"}}))
    assert main(["--config", "config.json"]) == EXIT_INVALID_CONFIG
    assert "ERROR brewberrypi: invalid configuration, refusing to start" in capsys.readouterr().out


def test_AC_081_invalid_recipe_file_is_skipped_and_logged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], served: list[int]
) -> None:
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "R.json").write_text(
        json.dumps({"name": "r", "mashing_process": {"goals": [{"type": "Rest", "temperature": 62}]}})
    )
    (tmp_path / "recipes" / "S.json").write_text(
        json.dumps({"name": "s", "mashing_process": {"goals": [{"type": "MashIn"}]}})
    )
    assert main(["--dummymode"]) == EXIT_OK
    output = capsys.readouterr().out
    assert "ERROR brewberrypi.recipes: invalid recipe file, recipe skipped:" in output
    assert "R.json: recipe 'R', goal 1 (index 0, Rest)" in output
    assert "recipes: S\n" in output


def test_a_recipe_directory_that_is_no_directory_exits_with_code_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"recipes": {"directory": "nope"}}))
    (tmp_path / "nope").write_text("a file")
    assert main(["--config", "config.json"]) == EXIT_INVALID_RECIPES
    assert "ERROR brewberrypi: invalid recipe directory, refusing to start" in capsys.readouterr().out


def test_a_server_that_cannot_start_exits_with_code_1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not uvicorn's 3, which the host would read as an invalid recipe directory (runtime contract §2)."""
    monkeypatch.setattr("brewberrypi.__main__.serve", real_serve)

    class NeverStarts:
        def __init__(self, config: object, on_started: object, on_exit: object) -> None:
            pass

        def run(self) -> None:
            raise SystemExit(3)  # what uvicorn does when the port is in use

    monkeypatch.setattr("brewberrypi.__main__._Server", NeverStarts)
    assert main(["--dummymode"]) == EXIT_ERROR
