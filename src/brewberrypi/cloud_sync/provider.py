"""What Cloud Sync needs from a cloud storage provider (spec 14, ADR-0003).

A provider signs in without a browser on the device (a device code the brewer enters elsewhere), keeps a
long-lived sign-in, and reads one folder: list its files with a revision each, and download a file. Nothing in
this interface writes to the cloud (one way, `Q-35`). Google Drive is the only provider so far
(`google_drive.py`); another one (OneDrive, Dropbox) implements the same protocol.
"""

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class DeviceCode:
    """A started sign-in (SYNC-002): the brewer enters `user_code` at `verification_url`."""

    device_code: str
    """The provider's handle of this sign-in; never shown or logged."""
    user_code: str
    verification_url: str
    expires_in: float
    """Seconds until the code expires."""
    interval: float
    """Seconds between two polls, as the provider asks."""


@dataclass(frozen=True)
class Credentials:
    """A completed sign-in, stored in `.cloud-sync/credentials.json` (SYNC-004)."""

    provider: str
    """The provider's key, e.g. `googleDrive`."""
    refresh_token: str
    account: str
    """The account's e-mail address, shown in the UI."""


@dataclass(frozen=True)
class RemoteFile:
    """A file of the cloud folder."""

    file_id: str
    name: str
    revision: str
    """Changes whenever the file's content changes."""


SignInOutcome = Literal["expired", "denied", "no_files_access"]


class SignInPending(Exception):
    """The brewer has not answered yet; poll again after `interval` seconds."""

    def __init__(self, interval: float | None = None) -> None:
        super().__init__("sign-in pending")
        self.interval = interval
        """A longer poll interval the provider asks for (`slow_down`), or `None`."""


class SignInFailed(Exception):
    """The sign-in ended without access (SYNC-003)."""

    def __init__(self, outcome: SignInOutcome) -> None:
        super().__init__(outcome)
        self.outcome = outcome


class ProviderOffline(Exception):
    """The provider cannot be reached, or answered that it is busy or failing (SYNC-014: `offline`)."""


class ProviderError(Exception):
    """The provider answered with an error (SYNC-014: `error`)."""


class AccessRevoked(Exception):
    """The stored sign-in is no longer valid (SYNC-006)."""


class FolderNotFound(Exception):
    """The cloud folder does not exist (SYNC-014)."""


class CloudProvider(Protocol):
    """One cloud storage provider. Every method raises `ProviderOffline` or `ProviderError` on failure."""

    key: str
    """Stored with the credentials, e.g. `googleDrive`."""
    title: str
    """Shown to the brewer, e.g. `Google Drive`."""

    async def start_sign_in(self) -> DeviceCode: ...

    async def finish_sign_in(self, device_code: str) -> Credentials:
        """The credentials once access was allowed; `SignInPending` until then, `SignInFailed` if not."""
        ...

    async def end_sign_in(self, credentials: Credentials) -> None:
        """Revoke the stored sign-in at the provider (SYNC-005)."""
        ...

    async def list_folder(self, credentials: Credentials) -> list[RemoteFile]:
        """The files of the cloud folder; `FolderNotFound`, `AccessRevoked`."""
        ...

    async def download(self, credentials: Credentials, file: RemoteFile) -> bytes:
        """The content of one file; `AccessRevoked`."""
        ...

    async def close(self) -> None: ...
