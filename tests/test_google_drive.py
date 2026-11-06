"""The Google Drive provider of Cloud Sync (spec 14, ADR-0003) against a fake Google (httpx.MockTransport)."""

import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from brewberrypi.clock import ManualClock
from brewberrypi.cloud_sync.google_drive import DEVICE_GRANT, DRIVE_SCOPE, GoogleDrive
from brewberrypi.cloud_sync.provider import (
    AccessRevoked,
    Credentials,
    FolderNotFound,
    ProviderError,
    ProviderOffline,
    RemoteFile,
    SignInFailed,
    SignInPending,
)

CREDENTIALS = Credentials("googleDrive", "refresh-1", "brewer@example.com")
Handler = Callable[[httpx.Request], httpx.Response]


def id_token(claims: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def form(request: httpx.Request) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


class FakeGoogle:
    """Google's OAuth and Drive endpoints, as far as Cloud Sync uses them."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.token_answers: list[httpx.Response] = []
        self.access_tokens = 0
        self.refresh_valid = True
        self.root: str | None = "root-id"
        self.folder: str | None = "recipes-id"
        self.pages: list[list[dict[str, str]]] = [[{"id": "f1", "name": "WATER.json", "version": "7"}]]
        self.drive_failure: httpx.Response | None = None
        self.reject_token_once = False

    def __call__(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/device/code"):
            assert form(request)["scope"] == f"openid email {DRIVE_SCOPE}"
            return httpx.Response(200, json={
                "device_code": "dc", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
                "expires_in": 1800, "interval": 5,
            })  # fmt: skip
        if url.startswith("https://oauth2.googleapis.com/token"):
            data = form(request)
            if data["grant_type"] == DEVICE_GRANT:
                return self.token_answers.pop(0)
            assert data["grant_type"] == "refresh_token"
            if not self.refresh_valid:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.access_tokens += 1
            return httpx.Response(
                200, json={"access_token": f"access-{self.access_tokens}", "expires_in": 3600}
            )
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        assert url.startswith("https://www.googleapis.com/drive/v3/files")
        if self.reject_token_once:
            self.reject_token_once = False
            return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})
        if self.drive_failure is not None:
            return self.drive_failure
        if request.url.params.get("alt") == "media":
            return httpx.Response(200, content=b'{"name": "Water"}')
        query = request.url.params["q"]
        if "value='root'" in query:
            return httpx.Response(200, json={"files": [] if self.root is None else [{"id": self.root}]})
        if "value='recipes'" in query:
            assert f"'{self.root}' in parents" in query
            return httpx.Response(200, json={"files": [] if self.folder is None else [{"id": self.folder}]})
        assert "' in parents and trashed = false and mimeType != " in query
        token = request.url.params.get("pageToken")
        index = 0 if token is None else int(token)
        page: dict[str, Any] = {"files": self.pages[index]}
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        return httpx.Response(200, json=page)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def drive(google: FakeGoogle, clock: ManualClock) -> GoogleDrive:
    return GoogleDrive("client-id", "client-secret", clock, transport=httpx.MockTransport(google))


async def test_sign_in_with_a_device_code(drive: GoogleDrive, google: FakeGoogle) -> None:
    code = await drive.start_sign_in()
    assert (code.user_code, code.verification_url, code.expires_in, code.interval) == (
        "ABCD-EFGH", "https://www.google.com/device", 1800, 5,
    )  # fmt: skip
    google.token_answers = [
        httpx.Response(428, json={"error": "authorization_pending"}),
        httpx.Response(403, json={"error": "slow_down", "interval": 10}),
        httpx.Response(200, json={
            "access_token": "a", "expires_in": 3600, "refresh_token": "refresh-1",
            "scope": f"openid {DRIVE_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            "id_token": id_token({"email": "brewer@example.com"}),
        }),
    ]  # fmt: skip
    with pytest.raises(SignInPending) as pending:
        await drive.finish_sign_in(code.device_code)
    assert pending.value.interval is None
    with pytest.raises(SignInPending) as slow:
        await drive.finish_sign_in(code.device_code)
    assert slow.value.interval == 10
    credentials = await drive.finish_sign_in(code.device_code)
    assert credentials == CREDENTIALS
    poll = form(google.requests[-1])
    assert poll == {
        "client_id": "client-id",
        "client_secret": "client-secret",
        "device_code": "dc",
        "grant_type": DEVICE_GRANT,
    }
    # the access token of the sign-in is used right away, no refresh needed
    await drive.list_folder(credentials)
    assert google.access_tokens == 0
    assert google.requests[-1].headers["Authorization"] == "Bearer a"


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (httpx.Response(403, json={"error": "access_denied"}), "denied"),
        (httpx.Response(400, json={"error": "expired_token"}), "expired"),
        (
            httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "scope": "openid email"}),
            "no_files_access",
        ),
    ],
)
async def test_SYNC_003_sign_in_without_access(
    drive: GoogleDrive, google: FakeGoogle, answer: httpx.Response, outcome: str
) -> None:
    google.token_answers = [answer]
    with pytest.raises(SignInFailed) as failed:
        await drive.finish_sign_in("dc")
    assert failed.value.outcome == outcome


async def test_lists_the_recipes_folder_the_editor_marked(drive: GoogleDrive, google: FakeGoogle) -> None:
    google.pages = [
        [{"id": "f1", "name": "WATER.json", "version": "7"}],
        [{"id": "f2", "name": "MASH.json", "version": "3"}],
    ]
    files = await drive.list_folder(CREDENTIALS)
    assert files == [RemoteFile("f1", "WATER.json", "7"), RemoteFile("f2", "MASH.json", "3")]
    assert google.requests[1].headers["Authorization"] == "Bearer access-1"
    google.requests.clear()
    await drive.list_folder(CREDENTIALS)  # folder id and access token are kept: one request per page
    assert len(google.requests) == 2
    assert await drive.download(CREDENTIALS, files[0]) == b'{"name": "Water"}'


async def test_an_empty_listing_looks_the_folder_up_again(drive: GoogleDrive, google: FakeGoogle) -> None:
    await drive.list_folder(CREDENTIALS)
    google.pages = [[]]
    google.folder = None  # the folder was trashed
    with pytest.raises(FolderNotFound):
        await drive.list_folder(CREDENTIALS)


async def test_SYNC_014_a_missing_folder(drive: GoogleDrive, google: FakeGoogle) -> None:
    google.root = None
    with pytest.raises(FolderNotFound):
        await drive.list_folder(CREDENTIALS)


async def test_SYNC_006_a_revoked_sign_in(drive: GoogleDrive, google: FakeGoogle) -> None:
    google.refresh_valid = False
    with pytest.raises(AccessRevoked):
        await drive.list_folder(CREDENTIALS)


async def test_an_expired_access_token_is_renewed(
    drive: GoogleDrive, google: FakeGoogle, clock: ManualClock
) -> None:
    await drive.list_folder(CREDENTIALS)
    await clock.advance(3600)
    await drive.list_folder(CREDENTIALS)
    assert google.access_tokens == 2
    google.reject_token_once = True  # Google rejects a token early: renewed once and retried
    await drive.list_folder(CREDENTIALS)
    assert google.access_tokens == 3


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(503, json={"error": {"message": "Backend Error"}}), ProviderOffline),
        (httpx.Response(429), ProviderOffline),
        (
            httpx.Response(
                403, json={"error": {"message": "Rate", "errors": [{"reason": "userRateLimitExceeded"}]}}
            ),
            ProviderOffline,
        ),
        (
            httpx.Response(
                403, json={"error": {"message": "Forbidden", "errors": [{"reason": "forbidden"}]}}
            ),
            ProviderError,
        ),
        (httpx.Response(400, json={"error": {"message": "Invalid query"}}), ProviderError),
    ],
)
async def test_SYNC_014_errors(
    drive: GoogleDrive, google: FakeGoogle, answer: httpx.Response, error: type
) -> None:
    google.drive_failure = answer
    with pytest.raises(error) as raised:
        await drive.list_folder(CREDENTIALS)
    assert str(raised.value).startswith(f"HTTP {answer.status_code}")


async def test_SYNC_014_no_network(clock: ManualClock) -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Name or service not known")

    drive = GoogleDrive("id", "secret", clock, transport=httpx.MockTransport(unreachable))
    with pytest.raises(ProviderOffline, match="Name or service not known"):
        await drive.list_folder(CREDENTIALS)
    with pytest.raises(ProviderOffline):
        await drive.start_sign_in()


async def test_SYNC_005_end_sign_in_revokes_the_token(drive: GoogleDrive, google: FakeGoogle) -> None:
    await drive.end_sign_in(CREDENTIALS)
    assert str(google.requests[-1].url) == "https://oauth2.googleapis.com/revoke"
    assert form(google.requests[-1]) == {"token": "refresh-1"}
