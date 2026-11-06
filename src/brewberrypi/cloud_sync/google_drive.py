"""Google Drive as a Cloud Sync provider (spec 14, ADR-0003).

Sign-in: OAuth 2.0 for TV and limited-input devices (device code), client type *TVs and Limited Input devices*
in the Google Cloud project of the recipe editor, scopes `openid email drive.file`. Google does not treat the
client secret of such a client as confidential, so the client is built into the application.

The folder is `BrewberryPi/recipes` as the recipe editor creates it: a folder marked with the app property
`brewberrypi=recipes` inside one marked `brewberrypi=root` (the editor's `DriveStore`). With `drive.file`,
Google lists only the files of this Cloud project: files the editor created or the brewer picked in it.
Read only: nothing here writes to Drive.
"""

import base64
import json
import logging
from importlib.resources import files
from importlib.resources.abc import Traversable
from typing import Any

import httpx

from brewberrypi.clock import Clock
from brewberrypi.cloud_sync.provider import (
    AccessRevoked,
    Credentials,
    DeviceCode,
    FolderNotFound,
    ProviderError,
    ProviderOffline,
    RemoteFile,
    SignInFailed,
    SignInPending,
)

log = logging.getLogger(__name__)

KEY = "googleDrive"
TITLE = "Google Drive"

BUILT_IN_CLIENT_FILE = files("brewberrypi").joinpath("data/google-drive-client.json")
"""The application's OAuth client (type *TVs and Limited Input devices*), `{"clientId", "clientSecret"}`.
Not in git: the release build writes it from the repository's Actions settings (`GOOGLE_DRIVE_CLIENT_ID`,
`GOOGLE_DRIVE_CLIENT_SECRET`; Containerfile, docs/deployment/cloud-sync-google.md). Without it, and without
`cloudSync.googleDrive.clientId`, Cloud Sync is unavailable (SYNC-001)."""


def built_in_client(file: Traversable = BUILT_IN_CLIENT_FILE) -> tuple[str, str]:
    """`(client id, client secret)` built into this build, or `("", "")`."""
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "", ""
    except (OSError, ValueError) as error:
        log.warning("Cloud Sync: the built-in Google client cannot be read: %s", type(error).__name__)
        return "", ""
    client_id = data.get("clientId") if isinstance(data, dict) else None
    client_secret = data.get("clientSecret") if isinstance(data, dict) else None
    if not isinstance(client_id, str) or not isinstance(client_secret, str):
        return "", ""
    return client_id, client_secret


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.file"
SCOPES = f"openid email {DRIVE_SCOPE}"

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
FOLDER_MIME = "application/vnd.google-apps.folder"

TIMEOUT_SECONDS = 20.0
TOKEN_MARGIN_SECONDS = 60.0
"""An access token is renewed this long before it expires."""


class GoogleDrive:
    """The Google Drive provider (`CloudProvider`)."""

    key = KEY
    title = TITLE

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock
        self._http = httpx.AsyncClient(transport=transport, timeout=TIMEOUT_SECONDS)
        self._access: tuple[str, str, float] | None = None
        """(refresh token, access token, monotonic expiry) of the last access token."""
        self._folder: str | None = None
        """The recipes folder's id once found."""

    async def close(self) -> None:
        await self._http.aclose()

    # --- sign-in ---

    async def start_sign_in(self) -> DeviceCode:
        data = await self._oauth(DEVICE_CODE_URL, {"client_id": self._client_id, "scope": SCOPES})
        try:
            return DeviceCode(
                device_code=str(data["device_code"]),
                user_code=str(data["user_code"]),
                verification_url=str(data.get("verification_url") or data["verification_uri"]),
                expires_in=float(data["expires_in"]),
                interval=float(data.get("interval", 5)),
            )
        except (KeyError, TypeError, ValueError):
            raise ProviderError("unexpected answer to the sign-in request") from None

    async def finish_sign_in(self, device_code: str) -> Credentials:
        form = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "device_code": device_code,
            "grant_type": DEVICE_GRANT,
        }
        response = await self._post(TOKEN_URL, form)
        data = _json(response)
        if response.status_code != httpx.codes.OK:
            error = data.get("error")
            if error == "authorization_pending":
                raise SignInPending()
            if error == "slow_down":
                raise SignInPending(interval=float(data.get("interval", 0)) or None)
            if error == "access_denied":
                raise SignInFailed("denied")
            if error in ("expired_token", "invalid_grant"):
                raise SignInFailed("expired")
            _raise_for(response, data)
        if DRIVE_SCOPE not in str(data.get("scope", "")).split():
            raise SignInFailed("no_files_access")  # Google lets the brewer untick single scopes
        refresh_token = data.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ProviderError("Google sent no long-lived sign-in")
        credentials = Credentials(KEY, refresh_token, _email(data.get("id_token")))
        self._remember(credentials, data)
        return credentials

    async def end_sign_in(self, credentials: Credentials) -> None:
        response = await self._post(REVOKE_URL, {"token": credentials.refresh_token})
        self._access = None
        self._folder = None
        if response.status_code == httpx.codes.BAD_REQUEST:
            return  # already invalid
        if response.status_code != httpx.codes.OK:
            _raise_for(response, _json(response))

    # --- the folder ---

    async def list_folder(self, credentials: Credentials) -> list[RemoteFile]:
        files = await self._list_children(credentials, await self._folder_id(credentials))
        if not files:
            # Empty, or the folder was trashed or replaced: look it up again before trusting "empty".
            self._folder = None
            files = await self._list_children(credentials, await self._folder_id(credentials))
        return files

    async def download(self, credentials: Credentials, file: RemoteFile) -> bytes:
        response = await self._drive(credentials, f"{FILES_URL}/{file.file_id}", {"alt": "media"})
        return response.content

    async def _folder_id(self, credentials: Credentials) -> str:
        if self._folder is None:
            root = await self._find_folder(credentials, "root", None)
            folder = None if root is None else await self._find_folder(credentials, "recipes", root)
            if folder is None:
                raise FolderNotFound()
            self._folder = folder
        return self._folder

    async def _find_folder(self, credentials: Credentials, role: str, parent: str | None) -> str | None:
        conditions = [
            f"mimeType = '{FOLDER_MIME}'",
            "trashed = false",
            f"appProperties has {{ key='brewberrypi' and value='{role}' }}",
        ]
        if parent is not None:
            conditions.append(f"'{parent}' in parents")
        params = {
            "q": " and ".join(conditions),
            "fields": "files(id)",
            "orderBy": "createdTime",
            "pageSize": "10",
            "spaces": "drive",
        }
        files = _json(await self._drive(credentials, FILES_URL, params)).get("files") or []
        return str(files[0]["id"]) if files else None

    async def _list_children(self, credentials: Credentials, folder: str) -> list[RemoteFile]:
        result: list[RemoteFile] = []
        page_token: str | None = None
        while True:
            params = {
                "q": f"'{folder}' in parents and trashed = false and mimeType != '{FOLDER_MIME}'",
                "fields": "nextPageToken,files(id,name,version)",
                "pageSize": "1000",
                "spaces": "drive",
            }
            if page_token is not None:
                params["pageToken"] = page_token
            data = _json(await self._drive(credentials, FILES_URL, params))
            for item in data.get("files") or []:
                result.append(RemoteFile(str(item["id"]), str(item["name"]), str(item.get("version", ""))))
            page_token = data.get("nextPageToken")
            if not page_token:
                return result

    # --- HTTP ---

    async def _drive(self, credentials: Credentials, url: str, params: dict[str, str]) -> httpx.Response:
        """A Drive request with a valid access token; a rejected token is renewed once."""
        for attempt in range(2):
            token = await self._access_token(credentials, renew=attempt > 0)
            try:
                response = await self._http.get(
                    url, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.HTTPError as error:
                raise ProviderOffline(_describe(error)) from None
            if response.status_code != httpx.codes.UNAUTHORIZED:
                break
        if response.status_code == httpx.codes.NOT_FOUND and "alt" not in params:
            self._folder = None
        if response.status_code != httpx.codes.OK:
            _raise_for(response, _json(response))
        return response

    async def _access_token(self, credentials: Credentials, *, renew: bool) -> str:
        access = self._access
        if (
            not renew
            and access is not None
            and access[0] == credentials.refresh_token
            and self._clock.monotonic() < access[2]
        ):
            return access[1]
        form = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": credentials.refresh_token,
            "grant_type": "refresh_token",
        }
        response = await self._post(TOKEN_URL, form)
        data = _json(response)
        if response.status_code != httpx.codes.OK:
            if data.get("error") == "invalid_grant":
                self._access = None
                raise AccessRevoked()
            _raise_for(response, data)
        return self._remember(credentials, data)

    def _remember(self, credentials: Credentials, data: dict[str, Any]) -> str:
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("Google sent no access token")
        lifetime = float(data.get("expires_in", 3600))
        self._access = (
            credentials.refresh_token,
            token,
            self._clock.monotonic() + lifetime - TOKEN_MARGIN_SECONDS,
        )
        return token

    async def _oauth(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        response = await self._post(url, form)
        data = _json(response)
        if response.status_code != httpx.codes.OK:
            _raise_for(response, data)
        return data

    async def _post(self, url: str, form: dict[str, str]) -> httpx.Response:
        try:
            return await self._http.post(url, data=form)
        except httpx.HTTPError as error:
            raise ProviderOffline(_describe(error)) from None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _raise_for(response: httpx.Response, data: dict[str, Any]) -> None:
    """`ProviderOffline` for answers worth retrying (429, 5xx, rate limits), else `ProviderError`."""
    status = response.status_code
    error = data.get("error")
    if isinstance(error, dict):
        reason = str(error.get("message") or error.get("status") or "")
        reasons = {str(item.get("reason")) for item in error.get("errors") or [] if isinstance(item, dict)}
    else:
        reason = str(data.get("error_description") or error or "")
        reasons = set()
    text = f"HTTP {status}" + (f", {reason}" if reason else "")
    if status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise ProviderOffline(text)
    if status == httpx.codes.FORBIDDEN and reasons & {"rateLimitExceeded", "userRateLimitExceeded"}:
        raise ProviderOffline(text)
    raise ProviderError(text)


def _describe(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "no answer"
    return str(error) or type(error).__name__


def _email(id_token: Any) -> str:
    """The `email` claim of the ID token Google sent straight to us over TLS (not used for authentication)."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:  # noqa: PLR2004
        return ""
    payload = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return ""
    email = claims.get("email") if isinstance(claims, dict) else None
    return email if isinstance(email, str) else ""
