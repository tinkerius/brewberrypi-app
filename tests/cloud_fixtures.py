"""A fake cloud provider for the Cloud Sync tests (spec 14): no network, the test fills the folder."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brewberrypi.clock import ManualClock
from brewberrypi.cloud_sync.provider import (
    AccessRevoked,
    Credentials,
    DeviceCode,
    FolderNotFound,
    ProviderError,
    ProviderOffline,
    RemoteFile,
    SignInFailed,
    SignInOutcome,
    SignInPending,
)
from brewberrypi.cloud_sync.service import CREDENTIALS_FILE, STATE_DIRECTORY, CloudSync

REFRESH_TOKEN = "1//secret-refresh-token"
ACCOUNT = "brewer@example.com"
USER_CODE = "WXYZ-ABCD"
DEVICE_CODE = "device-code-secret"


def recipe_bytes(name: str, goals: list[dict[str, Any]] | None = None, version: int | None = 2) -> bytes:
    recipe: dict[str, Any] = {} if version is None else {"version": version}
    recipe |= {"name": name, "mashing_process": {"goals": goals or [{"type": "MashIn"}]}}
    return (json.dumps(recipe, indent=2) + "\n").encode()


@dataclass
class FakeCloud:
    """`CloudProvider` with a folder of files; failures are switched on by the test."""

    key: str = "googleDrive"
    title: str = "Google Drive"
    files: dict[str, tuple[str, int, bytes]] = field(default_factory=dict)
    """file id -> (name, revision, content)"""
    folder_exists: bool = True
    offline: bool = False
    error: str | None = None
    revoked: bool = False
    download_fails: bool = False
    answer: SignInOutcome | str = "pending"
    """What `finish_sign_in` does: `pending`, `allow`, or a `SignInFailed` outcome."""
    calls: list[str] = field(default_factory=list)
    _next_id: int = 0

    def put(self, name: str, content: bytes) -> str:
        """Add a file, or change the file of that name (a new revision)."""
        for file_id, (existing, revision, _) in self.files.items():
            if existing == name:
                self.files[file_id] = (name, revision + 1, content)
                return file_id
        return self.add(name, content)

    def add(self, name: str, content: bytes) -> str:
        """Add a file, even if one of that name exists."""
        self._next_id += 1
        file_id = f"id{self._next_id}"
        self.files[file_id] = (name, 1, content)
        return file_id

    def remove(self, name: str) -> None:
        self.files = {k: v for k, v in self.files.items() if v[0] != name}

    def _check(self) -> None:
        if self.offline:
            raise ProviderOffline("no network")
        if self.error is not None:
            raise ProviderError(self.error)

    async def start_sign_in(self) -> DeviceCode:
        self.calls.append("start_sign_in")
        self._check()
        return DeviceCode(DEVICE_CODE, USER_CODE, "https://www.google.com/device", 1800, 5)

    async def finish_sign_in(self, device_code: str) -> Credentials:
        self.calls.append("finish_sign_in")
        assert device_code == DEVICE_CODE
        self._check()
        if self.answer == "pending":
            raise SignInPending()
        if self.answer != "allow":
            raise SignInFailed(self.answer)  # type: ignore[arg-type]
        return Credentials(self.key, REFRESH_TOKEN, ACCOUNT)

    async def end_sign_in(self, credentials: Credentials) -> None:
        self.calls.append("end_sign_in")
        self._check()

    async def list_folder(self, credentials: Credentials) -> list[RemoteFile]:
        self.calls.append("list_folder")
        assert credentials.refresh_token == REFRESH_TOKEN
        self._check()
        if self.revoked:
            raise AccessRevoked()
        if not self.folder_exists:
            raise FolderNotFound()
        return [RemoteFile(file_id, name, str(rev)) for file_id, (name, rev, _) in self.files.items()]

    async def download(self, credentials: Credentials, file: RemoteFile) -> bytes:
        self.calls.append(f"download {file.name}")
        self._check()
        if self.download_fails:
            raise ProviderOffline("connection reset")
        return self.files[file.file_id][2]

    async def close(self) -> None:
        self.calls.append("close")


def store_credentials(state_directory: Path) -> None:
    """A sign-in stored by an earlier run (SYNC-004)."""
    directory = state_directory / STATE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    data = {"provider": "googleDrive", "refreshToken": REFRESH_TOKEN, "account": ACCOUNT}
    (directory / CREDENTIALS_FILE).write_text(json.dumps(data))


def cloud_sync(
    cloud: FakeCloud | None, tmp_path: Path, clock: ManualClock, *, interval: float = 30
) -> CloudSync:
    recipes = tmp_path / "recipes"
    recipes.mkdir(exist_ok=True)
    return CloudSync(
        cloud,
        title="Google Drive",
        recipe_directory=recipes,
        state_directory=tmp_path,
        interval_seconds=interval,
        clock=clock,
    )
