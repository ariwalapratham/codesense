"""HTTP tests for /auth/*. GitHub is mocked; tokens must never appear in redirect URLs."""

from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from config.settings import settings
from services.github.oauth import GitHubProfile

_NONCE_COOKIE = "codesense_oauth_nonce"


@pytest.fixture(autouse=True)
def _isolate_auth_client(client):
    oauth = client.app.state.github_oauth
    original_exchange = oauth.exchange_code
    client.cookies.clear()
    yield
    oauth.exchange_code = original_exchange
    client.cookies.clear()


def _profile() -> GitHubProfile:
    github_id = uuid4().int % 10**12
    return GitHubProfile(
        github_id=github_id,
        login=f"user_{github_id}",
        name="Test User",
        email=f"user_{github_id}@example.com",
        avatar_url="https://example.com/avatar.png",
    )


def _set_exchange(client, profile: GitHubProfile) -> AsyncMock:
    mock = AsyncMock(return_value=profile)
    client.app.state.github_oauth.exchange_code = mock
    return mock


def _start_login(client, redirect_to: str = "/auth/callback"):
    response = client.get(
        "/auth/github/login",
        params={"redirect_to": redirect_to},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    return response, query["state"][0]


def _cookie_headers(response, name: str) -> list[str]:
    headers = response.headers
    getter = getattr(headers, "get_list", None)
    values = getter("set-cookie") if getter is not None else [headers.get("set-cookie") or ""]
    return [value for value in values if value.lower().startswith(f"{name.lower()}=")]


def _login(client, profile: GitHubProfile, redirect_to: str = "/auth/callback"):
    mock = _set_exchange(client, profile)
    login_resp, state = _start_login(client, redirect_to=redirect_to)
    callback = client.get(
        "/auth/github/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    return login_resp, callback, mock


def test_github_login_sets_nonce_cookie_and_redirects_to_github(client):
    response, state = _start_login(client)
    location = urlparse(response.headers["location"])
    query = parse_qs(location.query)
    assert location.netloc == "github.com"
    assert location.path == "/login/oauth/authorize"
    assert query["client_id"] == [settings.github_oauth_client_id]
    assert query["state"] == [state]
    assert "repo" not in query["scope"][0].split()
    nonce_headers = _cookie_headers(response, _NONCE_COOKIE)
    assert nonce_headers
    assert "httponly" in nonce_headers[0].lower()
    assert "path=/auth" in nonce_headers[0].lower()
    assert response.cookies.get(_NONCE_COOKIE)


def test_github_login_rejects_open_redirect(client):
    response = client.get(
        "/auth/github/login",
        params={"redirect_to": "https://evil.example/phish"},
        follow_redirects=False,
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["request_id"] == response.headers.get("x-request-id")


def test_github_callback_sets_refresh_cookie_and_redirects_to_frontend(client):
    profile = _profile()
    _, callback, mock = _login(client, profile)
    assert callback.status_code == 302
    assert callback.headers["location"] == "http://localhost:3000/auth/callback"
    assert profile.login not in callback.headers["location"]
    refresh_headers = _cookie_headers(callback, settings.auth_refresh_cookie_name)
    assert refresh_headers
    cookie = refresh_headers[0].lower()
    assert "httponly" in cookie
    assert "path=/auth" in cookie
    raw = callback.cookies.get(settings.auth_refresh_cookie_name)
    assert raw
    assert raw not in callback.headers["location"]
    mock.assert_awaited_once()
    kwargs = mock.await_args.kwargs
    assert kwargs["code"] == "fake-code"
    assert kwargs["redirect_uri"].endswith("/auth/github/callback")


def test_github_callback_rejects_missing_nonce_cookie(client):
    profile = _profile()
    _set_exchange(client, profile)
    _, state = _start_login(client)
    client.cookies.clear()
    response = client.get(
        "/auth/github/callback",
        params={"code": "fake-code", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert not _cookie_headers(response, settings.auth_refresh_cookie_name)


def test_github_callback_denied_redirects_to_frontend_without_refresh_cookie(client):
    _, state = _start_login(client)
    response = client.get(
        "/auth/github/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3000/auth/callback?error=oauth_denied"
    assert not _cookie_headers(response, settings.auth_refresh_cookie_name)


def test_github_callback_rejects_invalid_state(client):
    response = client.get(
        "/auth/github/callback",
        params={"code": "fake-code", "state": "not-a-jwt"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert error["request_id"] == response.headers.get("x-request-id")
    assert response.headers.get("location") is None


def test_refresh_rotates_cookie_and_returns_access_token(client):
    profile = _profile()
    _, callback, _ = _login(client, profile)
    first_refresh = callback.cookies.get(settings.auth_refresh_cookie_name)
    response = client.post("/auth/refresh")
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == settings.access_token_ttl_min * 60
    assert body["access_token"]
    rotated = response.cookies.get(settings.auth_refresh_cookie_name)
    assert rotated
    assert rotated != first_refresh

    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    payload = me.json()
    assert payload["github_login"] == profile.login
    assert payload["name"] == profile.name
    assert payload["role"] == "user"
    assert payload["id"]


def test_refresh_reuse_is_unauthorized(client):
    _, callback, _ = _login(client, _profile())
    first_refresh = callback.cookies.get(settings.auth_refresh_cookie_name)
    rotated = client.post("/auth/refresh")
    assert rotated.status_code == 200
    client.cookies.clear()
    client.cookies.set(settings.auth_refresh_cookie_name, first_refresh, path="/auth")
    reuse = client.post("/auth/refresh")
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "unauthorized"


def test_refresh_without_cookie_is_unauthorized(client):
    response = client.post("/auth/refresh")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_logout_revokes_family_and_clears_cookie(client):
    _, callback, _ = _login(client, _profile())
    refresh = callback.cookies.get(settings.auth_refresh_cookie_name)
    response = client.post("/auth/logout")
    assert response.status_code == 204
    assert response.content == b""
    client.cookies.clear()
    client.cookies.set(settings.auth_refresh_cookie_name, refresh, path="/auth")
    after = client.post("/auth/refresh")
    assert after.status_code == 401


def test_me_requires_bearer_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert error["request_id"] == response.headers.get("x-request-id")


def test_me_rejects_invalid_bearer_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
