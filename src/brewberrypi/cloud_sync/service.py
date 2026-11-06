"""Cloud Sync: sign-in, the sync loop and the status (spec 14, ADR-0003).

One `CloudSync` per application. `run()` is a background task: it syncs at startup when a sign-in is stored,
then every `cloudSync.intervalSeconds` (longer after failures), and at once on `sync_now()` (SYNC-020,
SYNC-021). A sync only touches the recipe directory; the engine, outputs and sensor are never involved
(SYNC-022). The recipe directory is changed only after the cloud folder was listed and every changed file
downloaded (SYNC-014); file operations run without an `await` in between, so a disconnect cannot cut a sync
in half.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from brewberrypi.clock import Clock
from brewberrypi.cloud_sync.mirror import Remove, Write, apply, local_recipes, plan
from brewberrypi.cloud_sync.provider import (
    AccessRevoked,
    CloudProvider,
    Credentials,
    DeviceCode,
    FolderNotFound,
    ProviderError,
    ProviderOffline,
    RemoteFile,
    SignInFailed,
    SignInPending,
)
from brewberrypi.engine import Reply
from brewberrypi.files import make_directory, sync_directory, write_file
from brewberrypi.recipes import RECIPE_ID, RecipeError, read_recipe

log = logging.getLogger(__name__)

State = Literal["unavailable", "disconnected", "connecting", "idle", "syncing", "offline", "error"]

STATE_DIRECTORY = ".cloud-sync"
CREDENTIALS_FILE = "credentials.json"
NOTICE_FILE = "DO-NOT-EDIT-Cloud-Sync.txt"
CLOUD_FOLDER = "BrewberryPi/recipes"
EDITOR_URL = "https://recipes.brewberrypi.org"
MAX_WAIT_SECONDS = 300.0
"""Longest wait after failed syncs (SYNC-021)."""

# Command replies (SYNC-030).
CONNECTING = "Connecting to {provider}."
ALREADY_CONNECTED = "Cloud Sync is already connected."
DISCONNECTED = "Cloud Sync disconnected."
NOT_CONNECTED = "Cloud Sync is not connected."
STARTED = "Cloud Sync started."
NOT_AVAILABLE = "Cloud Sync is not available."

# Status messages (SYNC-003, SYNC-006, SYNC-013, SYNC-014).
CODE_EXPIRED = "The sign-in code expired. Connect again."
ACCESS_DENIED = "Access was not allowed. Connect again."
NO_FILES_ACCESS = "Access to {provider} files was not allowed. Connect again and allow it."
SIGN_IN_FAILED = "Sign-in failed: {reason}."
REVOKED = "Access to {provider} was revoked. Connect again."
OFFLINE = "{provider} cannot be reached: {reason}."
FOLDER_MISSING = "The folder {folder} was not found in {provider}. Save a recipe in the recipe editor first."
PROVIDER_ERROR = "{provider} answered with an error: {reason}."
BAD_FILE_NAME = "Not a valid recipe file name."
DUPLICATE_NAME = "More than one file in {provider} has this name."


def notice_text(provider: str, account: str) -> str:
    """The text of the notice file (SYNC-016)."""
    return (
        f"This folder is a copy of the folder {CLOUD_FOLDER} in {provider} ({account}).\n"
        f"Edit the recipes at {EDITOR_URL}.\n"
        "Changes made here are overwritten at the next sync; replaced files are kept in .backup.\n"
        "To edit the recipes here instead, disconnect Cloud Sync in the BrewberryPi app.\n"
    )


@dataclass(frozen=True)
class _SignIn:
    code: DeviceCode
    expires_at: int
    """Epoch milliseconds, for display."""


class CloudSync:
    def __init__(  # noqa: PLR0913
        self,
        provider: CloudProvider | None,
        *,
        title: str,
        recipe_directory: Path,
        state_directory: Path,
        interval_seconds: float,
        clock: Clock,
        on_recipes_changed: Callable[[], None] = lambda: None,
    ) -> None:
        """`provider` is `None` when Cloud Sync is not available (SYNC-001); `title` is the provider's name.
        `on_recipes_changed` is called after a sync changed recipe files."""
        self._provider = provider
        self._title = title
        self._recipes = recipe_directory
        self._credentials_file = state_directory / STATE_DIRECTORY / CREDENTIALS_FILE
        self._interval = interval_seconds
        self._clock = clock
        self.on_recipes_changed = on_recipes_changed
        self.on_status_changed: Callable[[], None] = lambda: None
        """Called when the status changed, so it is pushed at once (IF-090): a sync is often over before the
        next regular check of the event stream."""
        self._state: State = "unavailable" if provider is None else "disconnected"
        self._credentials: Credentials | None = None
        self._sign_in: _SignIn | None = None
        self._message: str | None = None
        self._issues: list[dict[str, str]] = []
        self._last_sync: int | None = None
        self._failures = 0
        self._cache: dict[str, tuple[str, bytes]] = {}
        """Cloud file id -> (revision, content) of the downloaded files."""
        self._written: dict[str, bytes] = {}
        """Recipe id -> content this application last wrote (SYNC-011: `.local` copies)."""
        self._wake = asyncio.Event()
        self._sign_in_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[None]] = set()
        self._logged: tuple[State, str | None] | None = None
        if provider is not None:
            self._credentials = self._load_credentials(provider.key)
            if self._credentials is not None:
                self._state = "idle"

    # --- status (IF-090) ---

    @property
    def state(self) -> State:
        return self._state

    def status(self) -> dict[str, Any]:
        sign_in = self._sign_in
        return {
            "state": self._state,
            "provider": self._title,
            "account": None if self._credentials is None else self._credentials.account,
            "signIn": None
            if sign_in is None
            else {
                "code": sign_in.code.user_code,
                "url": sign_in.code.verification_url,
                "expiresAt": sign_in.expires_at,
            },
            "lastSync": self._last_sync,
            "message": self._message,
            "issues": list(self._issues),
        }

    @property
    def connected(self) -> bool:
        return self._state in ("idle", "syncing", "offline", "error")

    # --- commands (SYNC-030) ---

    def connect(self) -> Reply:
        if self._provider is None:
            return Reply(False, NOT_AVAILABLE, "conflict")
        if self.connected:
            return Reply(False, ALREADY_CONNECTED, "conflict")
        self._cancel_sign_in()
        self._set("connecting", None)
        self._sign_in_task = asyncio.create_task(self._run_sign_in(self._provider))
        return Reply(True, CONNECTING.format(provider=self._title))

    def disconnect(self) -> Reply:
        if self._provider is None:
            return Reply(False, NOT_AVAILABLE, "conflict")
        if self._state == "disconnected":
            return Reply(False, NOT_CONNECTED, "conflict")
        self._cancel_sign_in()
        credentials = self._credentials
        if credentials is not None:
            self._spawn(self._end_sign_in(self._provider, credentials))
        self._forget(None)
        log.info("Cloud Sync disconnected")
        return Reply(True, DISCONNECTED)

    def sync_now(self) -> Reply:
        if self._provider is None:
            return Reply(False, NOT_AVAILABLE, "conflict")
        if not self.connected:
            return Reply(False, NOT_CONNECTED, "conflict")
        self._wake.set()
        return Reply(True, STARTED)

    # --- the loop ---

    async def run(self) -> None:
        """Sync while connected until cancelled (SYNC-020, SYNC-021)."""
        if self._provider is None:
            return
        try:
            while True:
                if self.connected:
                    await self._sync(self._provider)
                await self._wait(self._next_wait() if self.connected else None)
        finally:
            self._cancel_sign_in()
            for task in list(self._background):
                task.cancel()
            await self._provider.close()

    def _next_wait(self) -> float:
        if self._failures == 0:
            return self._interval
        return min(self._interval * 2.0 ** (self._failures - 1), MAX_WAIT_SECONDS)

    async def _wait(self, seconds: float | None) -> None:
        """Until `seconds` passed (forever with `None`) or `_wake` is set."""
        waker = asyncio.ensure_future(self._wake.wait())
        tasks: set[asyncio.Future[Any]] = {waker}
        if seconds is not None:
            tasks.add(asyncio.ensure_future(self._clock.sleep(seconds)))
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            self._wake.clear()

    async def _sync(self, provider: CloudProvider) -> None:
        credentials = self._credentials
        assert credentials is not None
        self._state = "syncing"
        self.on_status_changed()
        try:
            files = await provider.list_folder(credentials)
            wanted, issues = await self._collect(provider, credentials, files)
        except (AccessRevoked, FolderNotFound, ProviderOffline, ProviderError) as error:
            if self._credentials is credentials:  # else disconnected meanwhile
                self._sync_failed(error)
            return
        if self._credentials is not credentials:
            return  # disconnected meanwhile: change nothing
        self._mirror(wanted)
        self._write_notice(credentials)
        self._issues = issues
        self._failures = 0
        self._last_sync = round(self._clock.wall_time() * 1000)
        self._set("idle", None)

    async def _collect(
        self, provider: CloudProvider, credentials: Credentials, files: list[RemoteFile]
    ) -> tuple[dict[str, bytes], list[dict[str, str]]]:
        """The recipes to mirror and the cloud files that are not synced (SYNC-012, SYNC-013)."""
        by_name: dict[str, list[RemoteFile]] = {}
        for file in files:
            if file.name.endswith(".json"):
                by_name.setdefault(file.name, []).append(file)
        wanted: dict[str, bytes] = {}
        issues: list[dict[str, str]] = []
        cache: dict[str, tuple[str, bytes]] = {}
        for name in sorted(by_name):
            recipe_id = name.removesuffix(".json")
            same_name = by_name[name]
            if len(same_name) > 1:
                issues.append({"recipe": recipe_id, "message": DUPLICATE_NAME.format(provider=self._title)})
                continue
            if RECIPE_ID.fullmatch(recipe_id) is None:
                issues.append({"recipe": recipe_id, "message": BAD_FILE_NAME})
                continue
            file = same_name[0]
            cached = self._cache.get(file.file_id)
            if cached is not None and cached[0] == file.revision:
                content = cached[1]
            else:
                content = await provider.download(credentials, file)
            cache[file.file_id] = (file.revision, content)
            try:
                wanted[recipe_id] = _recipe_content(
                    recipe_id, content, f"{self._title}: {CLOUD_FOLDER}/{name}"
                )
            except RecipeError as error:
                issues.append({"recipe": recipe_id, "message": str(error)})
        self._cache = cache
        return wanted, issues

    def _mirror(self, wanted: dict[str, bytes]) -> None:
        try:
            local = local_recipes(self._recipes)
        except OSError as error:
            log.warning("Cloud Sync: cannot read the recipe directory, retried at the next sync: %s", error)
            return
        actions = plan(local, wanted, self._written)
        done = apply(self._recipes, actions, local)
        for action in done:
            if isinstance(action, Write):
                self._written[action.recipe_id] = action.content
            elif isinstance(action, Remove):
                self._written.pop(action.recipe_id, None)
        if done:
            self.on_recipes_changed()

    def _sync_failed(self, error: Exception) -> None:
        """SYNC-006, SYNC-014: the recipe directory stays as it is."""
        if isinstance(error, AccessRevoked):
            self._forget(REVOKED.format(provider=self._title))
            return
        self._failures += 1
        if isinstance(error, FolderNotFound):
            self._set("error", FOLDER_MISSING.format(folder=CLOUD_FOLDER, provider=self._title))
        elif isinstance(error, ProviderOffline):
            self._set("offline", OFFLINE.format(provider=self._title, reason=_reason(error)))
        else:
            self._set("error", PROVIDER_ERROR.format(provider=self._title, reason=_reason(error)))

    # --- sign-in (SYNC-002, SYNC-003) ---

    async def _run_sign_in(self, provider: CloudProvider) -> None:
        try:
            code = await provider.start_sign_in()
        except (ProviderOffline, ProviderError) as error:
            self._end_connecting(SIGN_IN_FAILED.format(reason=_reason(error)))
            return
        self._sign_in = _SignIn(code, round((self._clock.wall_time() + code.expires_in) * 1000))
        self.on_status_changed()
        log.info("Cloud Sync: waiting for the brewer to allow access to %s", self._title)
        deadline = self._clock.monotonic() + code.expires_in
        interval = code.interval
        while True:
            await self._clock.sleep(interval)
            if self._clock.monotonic() >= deadline:
                self._end_connecting(CODE_EXPIRED)
                return
            try:
                credentials = await provider.finish_sign_in(code.device_code)
            except SignInPending as pending:
                if pending.interval is not None:
                    interval = max(interval, pending.interval)
                continue
            except SignInFailed as failed:
                messages = {
                    "expired": CODE_EXPIRED,
                    "denied": ACCESS_DENIED,
                    "no_files_access": NO_FILES_ACCESS.format(provider=self._title),
                }
                self._end_connecting(messages[failed.outcome])
                return
            except ProviderOffline:
                continue  # try again until the code expires
            except ProviderError as error:
                self._end_connecting(SIGN_IN_FAILED.format(reason=_reason(error)))
                return
            break
        try:
            self._store_credentials(credentials)
        except OSError as error:
            self._spawn(self._end_sign_in(provider, credentials))
            self._end_connecting(SIGN_IN_FAILED.format(reason=f"cannot store it ({error.strerror})"))
            return
        self._sign_in = None
        self._credentials = credentials
        self._cache.clear()
        self._written.clear()
        self._failures = 0
        self._set("idle", None)
        log.info("Cloud Sync connected to %s", self._title)
        self._wake.set()  # sync at once

    def _end_connecting(self, message: str) -> None:
        self._sign_in = None
        self._set("disconnected", message)

    def _cancel_sign_in(self) -> None:
        if self._sign_in_task is not None:
            self._sign_in_task.cancel()
            self._sign_in_task = None
        self._sign_in = None

    async def _end_sign_in(self, provider: CloudProvider, credentials: Credentials) -> None:
        try:
            await provider.end_sign_in(credentials)
        except (ProviderOffline, ProviderError) as error:
            log.warning(
                "Cloud Sync: %s could not end the sign-in (%s); it is deleted on this device",
                self._title,
                error,
            )

    def _forget(self, message: str | None) -> None:
        """Disconnect here (SYNC-005, SYNC-006): credentials and notice file go, recipe files stay."""
        self._credentials = None
        self._sign_in = None
        self._issues = []
        self._cache.clear()
        self._written.clear()
        self._failures = 0
        for file in (self._credentials_file, self._recipes / NOTICE_FILE):
            try:
                file.unlink(missing_ok=True)
            except OSError as error:
                log.warning("Cloud Sync: cannot delete %s: %s", file, error)
        self._set("disconnected", message)

    # --- files ---

    def _load_credentials(self, provider_key: str) -> Credentials | None:
        try:
            raw = json.loads(self._credentials_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            log.warning("Cloud Sync: cannot read the stored sign-in, connect again: %s", type(error).__name__)
            return None
        if not (
            isinstance(raw, dict)
            and raw.get("provider") == provider_key
            and isinstance(raw.get("refreshToken"), str)
            and isinstance(raw.get("account"), str)
        ):
            log.warning("Cloud Sync: the stored sign-in is not for %s, connect again", self._title)
            return None
        return Credentials(provider_key, raw["refreshToken"], raw["account"])

    def _store_credentials(self, credentials: Credentials) -> None:
        directory = self._credentials_file.parent
        if not directory.is_dir():
            make_directory(directory)
        data = {
            "provider": credentials.provider,
            "refreshToken": credentials.refresh_token,
            "account": credentials.account,
        }
        write_file(self._credentials_file, json.dumps(data, indent=2) + "\n")
        sync_directory(directory)

    def _write_notice(self, credentials: Credentials) -> None:
        file = self._recipes / NOTICE_FILE
        text = notice_text(self._title, credentials.account or self._title)
        try:
            if file.is_file() and file.read_text(encoding="utf-8") == text:
                return
            write_file(file, text)
        except (OSError, UnicodeDecodeError) as error:
            log.warning("Cloud Sync: cannot write %s: %s", file, error)

    # --- helpers ---

    def _set(self, state: State, message: str | None) -> None:
        self._state = state
        self._message = message
        logged = (state, message)
        if logged != self._logged:  # once per change, not on every sync (SYNC-022)
            self._logged = logged
            if message is None:
                log.info("Cloud Sync: %s", state)
            else:
                log.warning("Cloud Sync: %s: %s", state, message)
        self.on_status_changed()

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)


def _recipe_content(recipe_id: str, content: bytes, source: str) -> bytes:
    """What to write for a cloud file: itself, or migrated to the latest format version (SYNC-012).
    `RecipeError` if the recipe directory would skip it (SYNC-013)."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RecipeError(f"{source}: cannot read recipe file: {error}") from None
    parsed = read_recipe(recipe_id, text, source)
    if parsed.migrated is None:
        return content
    return (json.dumps(parsed.migrated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _reason(error: Exception) -> str:
    """An error's text for a message that ends with its own full stop."""
    return str(error).rstrip(". ") or type(error).__name__
