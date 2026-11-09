"""GitHub OAuth: authorize URL, code exchange, profile fetch. No token storage."""

from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

from config.settings import settings
from core.errors import AppError
from services.github.client import GitHubClient

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_TOKEN_URL = "https://github.com/login/oauth/access_token"
_USER_URL = "https://api.github.com/user"
_EMAILS_URL = "https://api.github.com/user/emails"
_SCOPES = "read:user user:email"


@dataclass(frozen=True)
class GitHubProfile:
    """Public GitHub identity used to upsert csUsers."""

    github_id: int
    login: str
    name: Optional[str]
    email: Optional[str]
    avatar_url: Optional[str]


class GitHubOAuth:
    """Server-side GitHub OAuth web flow. Access tokens are not persisted."""

    def __init__(self, client: Optional[GitHubClient] = None) -> None:
        self._client = client or GitHubClient()

    def _require_oauth_config(self) -> tuple[str, str]:
        client_id = settings.github_oauth_client_id
        client_secret = settings.github_oauth_client_secret
        if not client_id or not client_secret:
            raise AppError(
                "GitHub OAuth is not configured",
                code="upstream_error",
                status_code=503,
            )
        return client_id, client_secret

    def build_authorize_url(self, *, state: str, redirect_uri: str) -> str:
        """Return the GitHub authorize URL for a signed `state`."""
        client_id, _ = self._require_oauth_config()
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": _SCOPES,
                "allow_signup": "true",
            }
        )
        return f"{_AUTHORIZE_URL}?{query}"

    async def exchange_code(self, *, code: str, redirect_uri: str) -> GitHubProfile:
        """Exchange an OAuth code for a GitHub profile. Drops the GitHub access token."""
        client_id, client_secret = self._require_oauth_config()
        token_body = await self._client.fetch_json(
            "POST",
            _TOKEN_URL,
            json={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        if not isinstance(token_body, dict) or token_body.get("error"):
            raise AppError(
                "GitHub OAuth code exchange failed",
                code="unauthorized",
                status_code=401,
            )
        access_token = token_body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AppError(
                "GitHub OAuth code exchange failed",
                code="unauthorized",
                status_code=401,
            )
        headers = {"Authorization": f"Bearer {access_token}"}
        user = await self._client.fetch_json("GET", _USER_URL, headers=headers)
        if not isinstance(user, dict):
            raise AppError(
                "GitHub user payload was invalid",
                code="upstream_error",
                status_code=502,
            )
        email = _optional_str(user.get("email"))
        if not email:
            email = await self._primary_email(headers)
        try:
            github_id = int(user["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AppError(
                "GitHub user payload was invalid",
                code="upstream_error",
                status_code=502,
            ) from exc
        login = _optional_str(user.get("login"))
        if not login:
            raise AppError(
                "GitHub user payload was invalid",
                code="upstream_error",
                status_code=502,
            )
        return GitHubProfile(
            github_id=github_id,
            login=login,
            name=_optional_str(user.get("name")),
            email=email,
            avatar_url=_optional_str(user.get("avatar_url")),
        )

    async def _primary_email(self, headers: dict) -> Optional[str]:
        try:
            body = await self._client.fetch_json("GET", _EMAILS_URL, headers=headers)
        except AppError:
            return None
        if not isinstance(body, list):
            return None
        return _pick_primary_email(body)


def _optional_str(value: Any) -> Optional[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _pick_primary_email(entries: list) -> Optional[str]:
    primary = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        email = _optional_str(entry.get("email"))
        if not email:
            continue
        if entry.get("primary") and entry.get("verified"):
            return email
        if primary is None and entry.get("verified"):
            primary = email
    return primary
