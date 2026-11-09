from types import SimpleNamespace

import pytest

from api.routes.auth import (
    _ALLOWED_REDIRECTS,
    _DEFAULT_REDIRECT,
    _frontend_dest,
    _require_cookie_origin,
    _safe_redirect_path,
)
from config.settings import settings
from core.errors import AppError


def test_safe_redirect_allows_allowlisted_paths():
    for path in _ALLOWED_REDIRECTS:
        assert _safe_redirect_path(path) == path
    assert _safe_redirect_path("") == _DEFAULT_REDIRECT
    assert _safe_redirect_path("  ") == _DEFAULT_REDIRECT


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/phish",
        "//evil.example",
        "/auth/callback/../admin",
        "/not-on-the-list",
        "auth/callback",
    ],
)
def test_safe_redirect_rejects_open_redirects(path):
    with pytest.raises(AppError) as exc:
        _safe_redirect_path(path)
    assert exc.value.status_code == 400
    assert exc.value.code == "validation_error"


def test_frontend_dest_appends_oauth_denied():
    dest = _frontend_dest("/auth/callback", error="oauth_denied")
    assert dest == "http://localhost:3000/auth/callback?error=oauth_denied"


def test_cookie_origin_skipped_when_samesite_is_not_none(monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_samesite", "lax")
    request = SimpleNamespace(headers={})
    _require_cookie_origin(request)


def test_cookie_origin_rejects_cross_site_when_samesite_none(monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_samesite", "none")
    monkeypatch.setattr(settings, "frontend_origin", "https://app.example")
    request = SimpleNamespace(headers={"origin": "https://evil.example"})
    with pytest.raises(AppError) as exc:
        _require_cookie_origin(request)
    assert exc.value.status_code == 403
    assert exc.value.code == "forbidden"


def test_cookie_origin_allows_frontend_when_samesite_none(monkeypatch):
    monkeypatch.setattr(settings, "auth_cookie_samesite", "none")
    monkeypatch.setattr(settings, "frontend_origin", "https://app.example")
    request = SimpleNamespace(headers={"origin": "https://app.example"})
    _require_cookie_origin(request)
