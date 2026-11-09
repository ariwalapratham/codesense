from urllib.parse import parse_qs, urlparse

import pytest

from config.settings import settings
from core.errors import AppError
from services.github.oauth import GitHubOAuth, _TOKEN_URL, _USER_URL


class _FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def fetch_json(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        key = (method.upper(), url)
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def oauth_settings(monkeypatch):
    monkeypatch.setattr(settings, "github_oauth_client_id", "client-id")
    monkeypatch.setattr(settings, "github_oauth_client_secret", "client-secret")


def test_build_authorize_url(oauth_settings):
    oauth = GitHubOAuth(client=_FakeClient({}))
    url = oauth.build_authorize_url(
        state="signed-state",
        redirect_uri="https://api.example/auth/github/callback",
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.com"
    assert query["client_id"] == ["client-id"]
    assert query["state"] == ["signed-state"]
    assert "repo" not in query["scope"][0].split()
    assert "read:user" in query["scope"][0]


async def test_exchange_code_returns_profile_and_drops_token(oauth_settings):
    client = _FakeClient(
        {
            ("POST", _TOKEN_URL): {"access_token": "gho_should_not_leak"},
            ("GET", _USER_URL): {
                "id": 99,
                "login": "octocat",
                "name": "The Octocat",
                "email": "octocat@example.com",
                "avatar_url": "https://example/avatar",
            },
        }
    )
    oauth = GitHubOAuth(client=client)
    profile = await oauth.exchange_code(
        code="abc",
        redirect_uri="https://api.example/auth/github/callback",
    )
    assert profile.github_id == 99
    assert profile.login == "octocat"
    assert profile.email == "octocat@example.com"
    assert "gho_should_not_leak" not in profile.__dict__.values()


async def test_exchange_code_rejects_github_error(oauth_settings):
    client = _FakeClient({("POST", _TOKEN_URL): {"error": "bad_verification_code"}})
    oauth = GitHubOAuth(client=client)
    with pytest.raises(AppError) as exc:
        await oauth.exchange_code(code="bad", redirect_uri="https://api.example/cb")
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"
