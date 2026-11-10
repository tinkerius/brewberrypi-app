"""Cloud Sync (spec 14; AC-102..AC-115), with a fake provider and a manual clock."""

import asyncio
import json
import logging
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from brewberrypi.clock import ManualClock
from brewberrypi.cloud_sync import create_cloud_sync
from brewberrypi.cloud_sync.google_drive import built_in_client
from brewberrypi.cloud_sync.service import (
    CREDENTIALS_FILE,
    NOTICE_FILE,
    STATE_DIRECTORY,
    CloudSync,
    notice_text,
)
from brewberrypi.config import load_config
from brewberrypi.recipe_migrations import LATEST_VERSION
from brewberrypi.web.messages import validator
from cloud_fixtures import (
    ACCOUNT,
    DEVICE_CODE,
    REFRESH_TOKEN,
    USER_CODE,
    FakeCloud,
    cloud_sync,
    recipe_bytes,
    store_credentials,
)

STATUS = validator("CloudSyncStatus")
WATER = recipe_bytes("Water")
MASH = recipe_bytes("Mash", [{"type": "HeatUp", "temperature": 62}])


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def cloud() -> FakeCloud:
    return FakeCloud()


StartSync = Callable[..., Awaitable[CloudSync]]


@pytest.fixture
async def start(tmp_path: Path, clock: ManualClock, cloud: FakeCloud) -> AsyncIterator[StartSync]:
    """Start Cloud Sync's loop; `connected=True` stores a sign-in first (as after an earlier run)."""
    tasks: list[asyncio.Task[None]] = []

    async def run(*, connected: bool = True, interval: float = 30) -> CloudSync:
        if connected:
            store_credentials(tmp_path)
        sync = cloud_sync(cloud, tmp_path, clock, interval=interval)
        tasks.append(asyncio.create_task(sync.run()))
        await clock.advance(0)
        return sync

    yield run
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def recipes(tmp_path: Path) -> dict[str, bytes]:
    directory = tmp_path / "recipes"
    return {file.name: file.read_bytes() for file in sorted(directory.iterdir()) if file.is_file()}


def backup(tmp_path: Path) -> dict[str, bytes]:
    directory = tmp_path / "recipes" / ".backup"
    return (
        {file.name: file.read_bytes() for file in sorted(directory.iterdir())} if directory.is_dir() else {}
    )


def notice() -> bytes:
    return notice_text("Google Drive", ACCOUNT).encode()


# --- connecting (SYNC-002..006) ---


async def test_AC_102_connect_signs_in_with_a_code_and_then_mirrors(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start(connected=False)
    assert sync.status()["state"] == "disconnected"
    reply = sync.connect()
    assert (reply.success, reply.message) == (True, "Connecting to Google Drive.")
    await clock.advance(0)
    status = sync.status()
    STATUS(status)
    assert status["state"] == "connecting"
    assert status["signIn"] == {
        "code": USER_CODE,
        "url": "https://www.google.com/device",
        "expiresAt": round((clock.wall_time() + 1800) * 1000),
    }
    await clock.advance(5)  # first poll: the brewer has not answered yet
    assert sync.state == "connecting"
    assert not (tmp_path / STATE_DIRECTORY).exists()

    cloud.answer = "allow"
    await clock.advance(5)
    status = sync.status()
    STATUS(status)
    assert (status["state"], status["account"], status["signIn"], status["message"]) == (
        "idle",
        ACCOUNT,
        None,
        None,
    )
    stored = json.loads((tmp_path / STATE_DIRECTORY / CREDENTIALS_FILE).read_text())
    assert stored == {"provider": "googleDrive", "refreshToken": REFRESH_TOKEN, "account": ACCOUNT}
    assert recipes(tmp_path) == {NOTICE_FILE: notice(), "WATER.json": WATER}
    assert ACCOUNT in notice().decode()
    assert status["lastSync"] == round(clock.wall_time() * 1000)


async def test_AC_102_connect_again_while_connecting_starts_a_new_sign_in(
    start: StartSync, cloud: FakeCloud, clock: ManualClock
) -> None:
    sync: CloudSync = await start(connected=False)
    sync.connect()
    await clock.advance(0)
    assert sync.connect().success
    await clock.advance(0)
    assert cloud.calls.count("start_sign_in") == 2
    assert sync.state == "connecting"


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("expired", "The sign-in code expired. Connect again."),
        ("denied", "Access was not allowed. Connect again."),
        ("no_files_access", "Access to Google Drive files was not allowed. Connect again and allow it."),
    ],
)
async def test_AC_111_a_sign_in_without_access_changes_nothing(  # noqa: PLR0913, PLR0917
    start: StartSync,
    cloud: FakeCloud,
    clock: ManualClock,
    tmp_path: Path,
    answer: str,
    message: str,
) -> None:
    (tmp_path / "recipes").mkdir()
    (tmp_path / "recipes" / "LOCAL.json").write_bytes(WATER)
    sync: CloudSync = await start(connected=False)
    sync.connect()
    cloud.answer = answer
    await clock.advance(5)
    status = sync.status()
    assert (status["state"], status["message"], status["signIn"]) == ("disconnected", message, None)
    assert not (tmp_path / STATE_DIRECTORY).exists()
    assert recipes(tmp_path) == {"LOCAL.json": WATER}


async def test_AC_111_the_code_expires(start: StartSync, clock: ManualClock) -> None:
    sync: CloudSync = await start(connected=False)
    sync.connect()
    await clock.advance(1800)
    assert sync.status()["state"] == "disconnected"
    assert sync.status()["message"] == "The sign-in code expired. Connect again."


async def test_AC_111_the_provider_cannot_be_reached(
    start: StartSync, cloud: FakeCloud, clock: ManualClock
) -> None:
    cloud.offline = True
    sync: CloudSync = await start(connected=False)
    sync.connect()
    await clock.advance(0)
    assert sync.status()["state"] == "disconnected"
    assert sync.status()["message"] == "Sign-in failed: no network."
    cloud.offline = False
    cloud.error = "HTTP 401, The OAuth client was not found."  # Google's own full stop is not doubled
    sync.connect()
    await clock.advance(0)
    assert sync.status()["message"] == "Sign-in failed: HTTP 401, The OAuth client was not found."


async def test_AC_109_disconnect_keeps_the_recipes(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    assert recipes(tmp_path) == {NOTICE_FILE: notice(), "WATER.json": WATER}
    reply = sync.disconnect()
    assert (reply.success, reply.message) == (True, "Cloud Sync disconnected.")
    await clock.advance(0)
    assert "end_sign_in" in cloud.calls
    assert sync.status()["state"] == "disconnected"
    assert sync.status()["account"] is None
    assert not (tmp_path / STATE_DIRECTORY / CREDENTIALS_FILE).exists()
    assert recipes(tmp_path) == {"WATER.json": WATER}

    cloud.put("WATER.json", MASH)  # no more syncs, also not after a restart
    await clock.advance(300)
    restarted = cloud_sync(cloud, tmp_path, clock)
    assert restarted.state == "disconnected"
    assert recipes(tmp_path) == {"WATER.json": WATER}


async def test_AC_109_disconnect_while_the_provider_is_offline(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    sync: CloudSync = await start()
    cloud.offline = True
    assert sync.disconnect().success
    await clock.advance(0)
    assert not (tmp_path / STATE_DIRECTORY / CREDENTIALS_FILE).exists()


async def test_AC_109_disconnect_while_connecting_cancels_the_sign_in(
    start: StartSync, cloud: FakeCloud, clock: ManualClock
) -> None:
    sync: CloudSync = await start(connected=False)
    sync.connect()
    await clock.advance(0)
    assert sync.disconnect().success
    cloud.answer = "allow"
    await clock.advance(10)
    assert sync.state == "disconnected"
    assert "finish_sign_in" not in cloud.calls


async def test_AC_110_a_revoked_sign_in_disconnects(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    cloud.revoked = True
    await clock.advance(30)
    status = sync.status()
    assert (status["state"], status["message"]) == (
        "disconnected",
        "Access to Google Drive was revoked. Connect again.",
    )
    assert not (tmp_path / STATE_DIRECTORY / CREDENTIALS_FILE).exists()
    assert recipes(tmp_path) == {"WATER.json": WATER}


async def test_IF_090_every_status_change_is_reported_at_once_also_the_start_of_a_sync(
    tmp_path: Path, clock: ManualClock, cloud: FakeCloud
) -> None:
    store_credentials(tmp_path)
    sync = cloud_sync(cloud, tmp_path, clock)
    seen: list[str] = []
    sync.on_status_changed = lambda: seen.append(sync.state)
    task = asyncio.create_task(sync.run())
    await clock.advance(0)
    await clock.advance(30)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert seen == ["syncing", "idle", "syncing", "idle"]


# --- the mirror (SYNC-011..016) ---


async def test_AC_103_new_and_changed_cloud_recipes_are_written(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    await start()
    cloud.put("MASH.json", MASH)
    cloud.put("WATER.json", recipe_bytes("Water, changed"))
    await clock.advance(30)
    assert recipes(tmp_path) == {
        NOTICE_FILE: notice(),
        "MASH.json": MASH,
        "WATER.json": recipe_bytes("Water, changed"),
    }
    assert backup(tmp_path) == {}  # changed in the cloud, not here: no .local copy


async def test_AC_103_a_sync_without_changes_downloads_and_writes_nothing(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    await start()
    files = sorted((tmp_path / "recipes").iterdir())
    before = {file.name: file.stat().st_mtime_ns for file in files}
    cloud.calls.clear()
    await clock.advance(30)
    assert cloud.calls == ["list_folder"]
    assert {file.name: file.stat().st_mtime_ns for file in files} == before


async def test_AC_104_a_change_on_the_device_is_overwritten_and_kept_in_backup(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    await start()
    changed = recipe_bytes("Water, edited on the share")
    (tmp_path / "recipes" / "WATER.json").write_bytes(changed)
    await clock.advance(30)
    assert recipes(tmp_path)["WATER.json"] == WATER
    assert backup(tmp_path) == {"WATER.local.json": changed}


async def test_AC_104_a_file_deleted_on_the_device_comes_back(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    await start()
    (tmp_path / "recipes" / "WATER.json").unlink()
    (tmp_path / "recipes" / NOTICE_FILE).unlink()
    await clock.advance(30)
    assert recipes(tmp_path) == {NOTICE_FILE: notice(), "WATER.json": WATER}


async def test_AC_105_files_without_a_cloud_recipe_are_moved_to_backup(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "LOCAL.json").write_bytes(MASH)  # only on the device before connecting
    (directory / "notes.txt").write_text("notes")
    (directory / "._LOCAL.json").write_text("macOS metadata")
    (directory / "sub").mkdir()
    cloud.put("WATER.json", WATER)
    cloud.put("OLD.json", MASH)
    await start()
    assert backup(tmp_path) == {"LOCAL.removed.json": MASH}
    cloud.remove("OLD.json")  # removed in the cloud
    cloud.files = {k: (n.replace("WATER", "RENAMED"), r + 1, c) for k, (n, r, c) in cloud.files.items()}
    await clock.advance(30)
    assert recipes(tmp_path) == {
        "._LOCAL.json": b"macOS metadata",
        NOTICE_FILE: notice(),
        "RENAMED.json": WATER,
        "notes.txt": b"notes",
    }
    assert backup(tmp_path) == {
        "LOCAL.removed.json": MASH,
        "OLD.removed.json": MASH,
        "WATER.removed.json": WATER,
    }
    assert (directory / "sub").is_dir()


async def test_AC_106_an_older_format_version_is_written_in_the_latest(
    start: StartSync, cloud: FakeCloud, tmp_path: Path
) -> None:
    cloud.put("OLD.json", recipe_bytes("Old", version=None))
    sync: CloudSync = await start()
    written = json.loads(recipes(tmp_path)["OLD.json"])
    assert written == {"version": 3, "name": "Old", "mashing_process": {"goals": [{"type": "MashIn"}]}}
    assert backup(tmp_path) == {}
    assert sync.status()["issues"] == []


async def test_AC_106_cloud_files_this_app_cannot_read_are_not_synced(
    start: StartSync, cloud: FakeCloud, tmp_path: Path
) -> None:
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "NEWER.json").write_bytes(WATER)
    cloud.put("NEWER.json", recipe_bytes("Newer", version=99))
    cloud.put("BROKEN.json", b"{")
    cloud.put("bad name.json", WATER)
    cloud.add("TWICE.json", WATER)
    cloud.add("TWICE.json", MASH)
    cloud.put("notes.txt", b"not a recipe")
    cloud.put("GOOD.json", WATER)
    sync: CloudSync = await start()
    assert recipes(tmp_path) == {NOTICE_FILE: notice(), "GOOD.json": WATER}
    assert backup(tmp_path) == {"NEWER.removed.json": WATER}
    status = sync.status()
    STATUS(status)
    issues = {issue["recipe"]: issue["message"] for issue in status["issues"]}
    assert set(issues) == {"BROKEN", "NEWER", "TWICE", "bad name"}
    assert issues["bad name"] == "Not a valid recipe file name."
    assert issues["TWICE"] == "More than one file in Google Drive has this name."
    assert issues["BROKEN"].startswith("Google Drive: BrewberryPi/recipes/BROKEN.json:1:2: invalid JSON")
    assert (
        f"format version 99 is newer than this application supports (latest {LATEST_VERSION})"
        in issues["NEWER"]
    )
    assert status["state"] == "idle"


async def test_AC_107_nothing_changes_when_the_provider_cannot_be_reached(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    before = recipes(tmp_path)
    cloud.offline = True
    cloud.remove("WATER.json")
    await clock.advance(30)
    status = sync.status()
    assert (status["state"], status["message"]) == ("offline", "Google Drive cannot be reached: no network.")
    assert recipes(tmp_path) == before


async def test_AC_107_nothing_changes_when_the_folder_is_missing(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    before = recipes(tmp_path)
    cloud.folder_exists = False
    await clock.advance(30)
    status = sync.status()
    assert status["state"] == "error"
    assert status["message"] == (
        "The folder BrewberryPi/recipes was not found in Google Drive. "
        "Save a recipe in the recipe editor first."
    )
    assert recipes(tmp_path) == before


async def test_AC_107_nothing_changes_when_a_download_fails(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    before = recipes(tmp_path)
    cloud.put("WATER.json", MASH)
    cloud.put("NEW.json", MASH)
    cloud.download_fails = True
    await clock.advance(30)
    assert sync.state == "offline"
    assert recipes(tmp_path) == before


async def test_AC_107_a_provider_error(start: StartSync, cloud: FakeCloud, clock: ManualClock) -> None:
    sync: CloudSync = await start()
    cloud.error = "HTTP 400, bad request"
    await clock.advance(30)
    status = sync.status()
    assert (status["state"], status["message"]) == (
        "error",
        "Google Drive answered with an error: HTTP 400, bad request.",
    )


async def test_AC_107_an_empty_cloud_folder_empties_the_recipe_directory(
    start: StartSync, cloud: FakeCloud, clock: ManualClock, tmp_path: Path
) -> None:
    cloud.put("WATER.json", WATER)
    await start()
    cloud.remove("WATER.json")
    await clock.advance(30)
    assert recipes(tmp_path) == {NOTICE_FILE: notice()}
    assert backup(tmp_path) == {"WATER.removed.json": WATER}


async def test_SYNC_015_a_file_that_cannot_be_written_does_not_stop_the_others(
    start: StartSync, cloud: FakeCloud, tmp_path: Path
) -> None:
    directory = tmp_path / "recipes"
    directory.mkdir()
    (directory / "BLOCKED.json").mkdir()  # a directory where the file should go: writing it fails
    cloud.put("BLOCKED.json", WATER)
    cloud.put("WATER.json", WATER)
    sync: CloudSync = await start()
    assert recipes(tmp_path)["WATER.json"] == WATER
    assert sync.state == "idle"


# --- timing (SYNC-020, SYNC-021) ---


async def test_AC_108_syncs_at_startup_every_interval_and_on_sync_now(
    start: StartSync, cloud: FakeCloud, clock: ManualClock
) -> None:
    sync: CloudSync = await start(interval=30)
    assert cloud.calls.count("list_folder") == 1  # at startup, without a new sign-in
    assert "start_sign_in" not in cloud.calls
    await clock.advance(29)
    assert cloud.calls.count("list_folder") == 1
    await clock.advance(1)
    assert cloud.calls.count("list_folder") == 2
    await clock.advance(10)
    reply = sync.sync_now()
    assert (reply.success, reply.message) == (True, "Cloud Sync started.")
    await clock.advance(0)
    assert cloud.calls.count("list_folder") == 3
    await clock.advance(29)
    assert cloud.calls.count("list_folder") == 3  # the interval starts again after Sync now
    await clock.advance(1)
    assert cloud.calls.count("list_folder") == 4


async def test_AC_108_waits_grow_after_failures(
    start: StartSync, cloud: FakeCloud, clock: ManualClock
) -> None:
    cloud.offline = True
    await start(interval=30)
    times: list[float] = []
    for _ in range(7):
        count = cloud.calls.count("list_folder")
        start_time = clock.monotonic()
        while cloud.calls.count("list_folder") == count:
            await clock.advance(1)
        times.append(clock.monotonic() - start_time)
    assert times[:6] == [30, 60, 120, 240, 300, 300]
    cloud.offline = False
    count = cloud.calls.count("list_folder")
    await clock.advance(300)  # the 7th sync just failed; this one succeeds
    assert cloud.calls.count("list_folder") == count + 1
    await clock.advance(30)
    assert cloud.calls.count("list_folder") == count + 2


# --- commands (SYNC-030) ---


async def test_AC_112_commands_in_the_wrong_state_are_rejected(
    start: StartSync, tmp_path: Path, clock: ManualClock
) -> None:
    sync: CloudSync = await start(connected=False)
    for reply in (sync.disconnect(), sync.sync_now()):
        assert (reply.success, reply.message, reply.error) == (
            False,
            "Cloud Sync is not connected.",
            "conflict",
        )
    store_credentials(tmp_path)
    connected = cloud_sync(FakeCloud(), tmp_path, clock)
    reply = connected.connect()
    assert (reply.success, reply.message, reply.error) == (
        False,
        "Cloud Sync is already connected.",
        "conflict",
    )


def test_AC_112_commands_while_unavailable(tmp_path: Path, clock: ManualClock) -> None:
    sync = cloud_sync(None, tmp_path, clock)
    for reply in (sync.connect(), sync.disconnect(), sync.sync_now()):
        assert (reply.success, reply.message, reply.error) == (
            False,
            "Cloud Sync is not available.",
            "conflict",
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": False, "googleDrive": {"clientId": "id.apps.googleusercontent.com"}},
        {"enabled": True, "googleDrive": {"clientId": ""}},
    ],
)
async def test_AC_113_unavailable_without_a_client_or_when_disabled(
    tmp_path: Path, clock: ManualClock, settings: dict[str, object]
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"cloudSync": settings}))
    store_credentials(tmp_path)  # even with a stored sign-in
    config = load_config(tmp_path / "config.json")
    sync = create_cloud_sync(config.cloud_sync, config.recipes.directory, clock)
    status = sync.status()
    STATUS(status)
    assert status["state"] == "unavailable"
    await asyncio.wait_for(sync.run(), 1)  # returns at once: no requests


def test_SYNC_001_available_with_a_client_id(tmp_path: Path, clock: ManualClock) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"cloudSync": {"googleDrive": {"clientId": "x"}}}))
    config = load_config(tmp_path / "config.json")
    assert config.cloud_sync.interval_seconds == 30
    sync = create_cloud_sync(config.cloud_sync, config.recipes.directory, clock)
    assert sync.state == "disconnected"


# --- no secrets in the log (SYNC-004) ---


async def test_AC_115_the_log_has_no_token_code_or_recipe_content(
    start: StartSync,
    cloud: FakeCloud,
    clock: ManualClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_recipe = recipe_bytes("Secret Recipe Name")
    cloud.put("SECRET.json", secret_recipe)
    with caplog.at_level(logging.DEBUG):
        sync: CloudSync = await start(connected=False)
        sync.connect()
        cloud.answer = "allow"
        await clock.advance(5)
        await clock.advance(30)
        sync.disconnect()
        await clock.advance(0)
    assert sync.state == "disconnected"
    text = caplog.text
    for secret in (REFRESH_TOKEN, USER_CODE, DEVICE_CODE, "Secret Recipe Name"):
        assert secret not in text
    assert "Cloud Sync" in text


# --- the built-in Google client (docs/deployment/cloud-sync-google.md) ---


def test_SYNC_001_the_built_in_client_is_read_from_the_package(tmp_path: Path) -> None:
    file = tmp_path / "google-drive-client.json"
    assert built_in_client(file) == ("", "")  # not built in
    file.write_text(json.dumps({"clientId": "id.apps.googleusercontent.com", "clientSecret": "GOCSPX-x"}))
    assert built_in_client(file) == ("id.apps.googleusercontent.com", "GOCSPX-x")
    file.write_text("{")
    assert built_in_client(file) == ("", "")


def test_SYNC_001_a_built_in_client_makes_cloud_sync_available_and_the_config_replaces_it(
    tmp_path: Path, clock: ManualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    import brewberrypi.cloud_sync as cloud_sync_package  # noqa: PLC0415

    monkeypatch.setattr(cloud_sync_package, "built_in_client", lambda: ("built-in-id", "built-in-secret"))
    (tmp_path / "config.json").write_text("{}")
    config = load_config(tmp_path / "config.json")
    sync = create_cloud_sync(config.cloud_sync, config.recipes.directory, clock)
    assert sync.state == "disconnected"
    assert sync._provider._client_id == "built-in-id"  # type: ignore[union-attr]
    (tmp_path / "config.json").write_text(
        json.dumps({"cloudSync": {"googleDrive": {"clientId": "own", "clientSecret": "s"}}})
    )
    config = load_config(tmp_path / "config.json")
    sync = create_cloud_sync(config.cloud_sync, config.recipes.directory, clock)
    assert (sync._provider._client_id, sync._provider._client_secret) == ("own", "s")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("secrets", "written", "code"),
    [
        ({"google_drive_client_id": "id\n", "google_drive_client_secret": "GOCSPX-x\n"}, True, 0),
        ({}, False, 0),
        ({"google_drive_client_id": "id"}, False, 1),
    ],
)
def test_the_release_build_writes_the_client_from_its_build_secrets(
    tmp_path: Path, secrets: dict[str, str], written: bool, code: int
) -> None:
    script = Path(__file__).parents[1] / "scripts" / "google-drive-client.py"
    directory = tmp_path / "secrets"
    directory.mkdir()
    for name, value in secrets.items():
        (directory / name).write_text(value)
    output = tmp_path / "google-drive-client.json"
    result = subprocess.run(
        [sys.executable, str(script), str(directory), str(output)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == code
    assert output.exists() == written
    assert "GOCSPX" not in result.stdout + result.stderr
    if written:
        assert built_in_client(output) == ("id", "GOCSPX-x")
