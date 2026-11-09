from datetime import datetime, timedelta, timezone

import pytest

from config.settings import settings
from core.errors import AppError
from pydantic import ValidationError

from config.settings import Settings
from core.security import (
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_oauth_nonce,
    oauth_nonces_match,
    sign_oauth_state,
    verify_oauth_state,
)

_SECRET = "unit-test-jwt-secret-not-for-prod"


@pytest.fixture
def jwt_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", _SECRET)


def test_access_token_round_trip(jwt_secret):
    token = create_access_token(user_id="6720abc", role="user")
    payload = decode_access_token(token)
    assert payload["sub"] == "6720abc"
    assert payload["role"] == "user"
    assert payload["typ"] == "access"


def test_access_token_expired_is_unauthorized(jwt_secret):
    past = datetime.now(timezone.utc) - timedelta(minutes=20)
    token = create_access_token(user_id="6720abc", now=past)
    with pytest.raises(AppError) as exc:
        decode_access_token(token)
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"


def test_tampered_access_token_is_unauthorized(jwt_secret):
    token = create_access_token(user_id="6720abc")
    tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
    with pytest.raises(AppError) as exc:
        decode_access_token(tampered)
    assert exc.value.code == "unauthorized"


def test_oauth_state_round_trip(jwt_secret):
    nonce = new_oauth_nonce()
    state = sign_oauth_state(redirect_to="/auth/callback", nonce=nonce)
    payload = verify_oauth_state(state)
    assert payload["redirect_to"] == "/auth/callback"
    assert payload["typ"] == "oauth_state"
    assert oauth_nonces_match(payload["nonce"], nonce)
    assert not oauth_nonces_match(payload["nonce"], new_oauth_nonce())


def test_tampered_oauth_state_is_unauthorized(jwt_secret):
    state = sign_oauth_state(redirect_to="/ok")
    tampered = state[:-3] + ("X" if state[-3] != "X" else "Y") + state[-2:]
    with pytest.raises(AppError) as exc:
        verify_oauth_state(tampered)
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthorized"


def test_refresh_hash_is_sha256_hex_and_stable():
    digest = hash_refresh_token("raw-token")
    assert digest == hash_refresh_token("raw-token")
    assert digest != hash_refresh_token("other")
    assert len(digest) == 64
    int(digest, 16)


def test_jwt_secret_rejects_short_values():
    with pytest.raises(ValidationError):
        Settings(jwt_secret="too-short", frontend_origin="http://localhost:3000")


def test_samesite_none_requires_secure():
    with pytest.raises(ValidationError):
        Settings(
            auth_cookie_samesite="none",
            auth_cookie_secure=False,
            frontend_origin="http://localhost:3000",
        )
