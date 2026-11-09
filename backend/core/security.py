"""JWT access tokens, refresh-token hashing, and signed OAuth state."""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

import jwt

from config.settings import settings
from core.errors import AppError

_ACCESS_TYP = "access"
_STATE_TYP = "oauth_state"
_HS256 = "HS256"
_STATE_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_jwt_secret() -> str:
    secret = settings.jwt_secret
    if not secret or len(secret) < 32:
        raise AppError(
            "JWT_SECRET is not configured",
            code="internal_error",
            status_code=500,
        )
    return secret


def hash_refresh_token(raw: str) -> str:
    """Return the SHA-256 hex digest of a refresh token. Never log `raw`."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    """Return a new unguessable refresh token (store only the hash)."""
    return secrets.token_urlsafe(32)


def new_family_id() -> str:
    """Return a new refresh-token family id."""
    return str(uuid4())


def create_access_token(
    *,
    user_id: str,
    role: str = "user",
    now: Optional[datetime] = None,
) -> str:
    """Sign a short-lived access JWT for `user_id`."""
    issued = now or _now()
    ttl = timedelta(minutes=settings.access_token_ttl_min)
    payload: Dict[str, Any] = {
        "sub": user_id,
        "role": role,
        "typ": _ACCESS_TYP,
        "iat": issued,
        "exp": issued + ttl,
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm=_HS256)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Verify an access JWT. Raises AppError 401 on failure."""
    try:
        payload = jwt.decode(
            token,
            _require_jwt_secret(),
            algorithms=[_HS256],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            "Access token expired",
            code="unauthorized",
            status_code=401,
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(
            "Invalid access token",
            code="unauthorized",
            status_code=401,
        ) from exc
    if payload.get("typ") != _ACCESS_TYP or not payload.get("sub"):
        raise AppError(
            "Invalid access token",
            code="unauthorized",
            status_code=401,
        )
    return payload


def new_oauth_nonce() -> str:
    """Return a nonce to bind OAuth state to the initiating browser cookie."""
    return secrets.token_urlsafe(16)


def oauth_nonces_match(left: str, right: str) -> bool:
    """Constant-time compare for OAuth cookie nonce vs signed state nonce."""
    if not left or not right or len(left) != len(right):
        return False
    return secrets.compare_digest(left, right)


def sign_oauth_state(
    *,
    redirect_to: str,
    nonce: Optional[str] = None,
    now: Optional[datetime] = None,
) -> str:
    """Sign a short-lived OAuth `state` that carries `redirect_to`."""
    issued = now or _now()
    payload: Dict[str, Any] = {
        "typ": _STATE_TYP,
        "redirect_to": redirect_to,
        "nonce": nonce or new_oauth_nonce(),
        "iat": issued,
        "exp": issued + _STATE_TTL,
    }
    return jwt.encode(payload, _require_jwt_secret(), algorithm=_HS256)


def verify_oauth_state(state: str) -> Dict[str, Any]:
    """Verify a signed OAuth `state`. Raises AppError 401 on failure."""
    try:
        payload = jwt.decode(
            state,
            _require_jwt_secret(),
            algorithms=[_HS256],
        )
    except jwt.ExpiredSignatureError as exc:
        raise AppError(
            "OAuth state expired",
            code="unauthorized",
            status_code=401,
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError(
            "Invalid OAuth state",
            code="unauthorized",
            status_code=401,
        ) from exc
    nonce = payload.get("nonce")
    if (
        payload.get("typ") != _STATE_TYP
        or not isinstance(payload.get("redirect_to"), str)
        or not isinstance(nonce, str)
        or not nonce
    ):
        raise AppError(
            "Invalid OAuth state",
            code="unauthorized",
            status_code=401,
        )
    return payload


def access_token_expires_in() -> int:
    """Access-token lifetime in seconds (TokenResponse.expires_in)."""
    return settings.access_token_ttl_min * 60
