"""GitHub OAuth, refresh-cookie rotation, and the current-user endpoint."""

import logging
import re
from datetime import timedelta
from typing import Literal, Optional
from urllib.parse import urlencode, urljoin

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from api.deps import get_auth_service, get_current_user, get_github_oauth
from config.settings import settings
from core.errors import AppError
from core.security import (
    new_oauth_nonce,
    oauth_nonces_match,
    sign_oauth_state,
    verify_oauth_state,
)
from models.schemas import TokenResponse, UserPublic
from services.auth_service import AuthService, TokenPair
from services.github.oauth import GitHubOAuth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_NONCE_COOKIE = "codesense_oauth_nonce"
_OAUTH_NONCE_MAX_AGE = 600
_DEFAULT_REDIRECT = "/auth/callback"
_ALLOWED_REDIRECTS = frozenset({"/auth/callback", "/callback", "/login/callback"})
_REDIRECT_RE = re.compile(r"^/[A-Za-z0-9/_-]*$")
_SameSite = Literal["lax", "strict", "none"]


def _samesite() -> _SameSite:
    value = settings.auth_cookie_samesite
    if value in ("lax", "strict", "none"):
        return value
    return "lax"


def _callback_uri(request: Request) -> str:
    return str(request.url_for("github_oauth_callback"))


def _frontend_dest(path: str, *, error: Optional[str] = None) -> str:
    dest = urljoin(f"{settings.frontend_origin}/", path.lstrip("/"))
    if error:
        dest = f"{dest}?{urlencode({'error': error})}"
    return dest


def _verified_oauth_state(request: Request, state: Optional[str]) -> dict:
    """Verify signed OAuth state against the nonce cookie. Raises 401 on mismatch."""
    if not state:
        raise AppError(
            "Missing OAuth state",
            code="unauthorized",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    payload = verify_oauth_state(state)
    cookie_nonce = request.cookies.get(_OAUTH_NONCE_COOKIE) or ""
    if not oauth_nonces_match(cookie_nonce, str(payload["nonce"])):
        raise AppError(
            "Invalid OAuth state",
            code="unauthorized",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    return payload


def _require_cookie_origin(request: Request) -> None:
    """When the refresh cookie is cross-site, require Origin/Referer from the frontend."""
    if settings.auth_cookie_samesite != "none":
        return
    allowed = settings.frontend_origin
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin:
        if origin != allowed:
            raise AppError(
                "Invalid request origin",
                code="forbidden",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return
    referer = request.headers.get("referer") or ""
    if referer == allowed or referer.startswith(f"{allowed}/"):
        return
    raise AppError(
        "Invalid request origin",
        code="forbidden",
        status_code=status.HTTP_403_FORBIDDEN,
    )


def _safe_redirect_path(redirect_to: str) -> str:
    path = (redirect_to or "").strip() or _DEFAULT_REDIRECT
    if (
        not _REDIRECT_RE.fullmatch(path)
        or path.startswith("//")
        or ".." in path
        or path not in _ALLOWED_REDIRECTS
    ):
        raise AppError(
            "redirect_to must be an allowed frontend path",
            code="validation_error",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    return path


def _set_refresh_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=raw,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(),
        path="/auth",
        max_age=int(timedelta(days=settings.refresh_token_ttl_days).total_seconds()),
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.auth_refresh_cookie_name,
        path="/auth",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(),
    )


def _set_oauth_nonce_cookie(response: Response, nonce: str) -> None:
    response.set_cookie(
        key=_OAUTH_NONCE_COOKIE,
        value=nonce,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(),
        path="/auth",
        max_age=_OAUTH_NONCE_MAX_AGE,
    )


def _clear_oauth_nonce_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_OAUTH_NONCE_COOKIE,
        path="/auth",
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=_samesite(),
    )


def _user_public(user: dict) -> UserPublic:
    """Map a Mongo user document to the public API shape."""
    return UserPublic(
        id=str(user["_id"]),
        github_login=user["github_login"],
        name=user.get("name"),
        avatar_url=user.get("avatar_url"),
        role=str(user.get("role") or "user"),
        created_at=user["created_at"],
    )


@router.get("/github/login", summary="Start GitHub OAuth")
async def github_login(
    request: Request,
    redirect_to: str = Query(default=_DEFAULT_REDIRECT),
    oauth: GitHubOAuth = Depends(get_github_oauth),
) -> RedirectResponse:
    """Redirect to GitHub with a signed state bound to a nonce cookie."""
    path = _safe_redirect_path(redirect_to)
    nonce = new_oauth_nonce()
    state = sign_oauth_state(redirect_to=path, nonce=nonce)
    url = oauth.build_authorize_url(state=state, redirect_uri=_callback_uri(request))
    response = RedirectResponse(url=url, status_code=status.HTTP_302_FOUND)
    _set_oauth_nonce_cookie(response, nonce)
    return response


@router.get("/github/callback", name="github_oauth_callback", summary="GitHub OAuth callback")
async def github_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    oauth: GitHubOAuth = Depends(get_github_oauth),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    """Exchange the GitHub code, set the refresh cookie, and return to the frontend."""
    if error:
        payload = _verified_oauth_state(request, state)
        redirect_to = _safe_redirect_path(str(payload["redirect_to"]))
        logger.info("GitHub OAuth cancelled error=%s", error)
        response = RedirectResponse(
            url=_frontend_dest(redirect_to, error="oauth_denied"),
            status_code=status.HTTP_302_FOUND,
        )
        _clear_oauth_nonce_cookie(response)
        return response
    if not code or not state:
        raise AppError(
            "Missing OAuth code or state",
            code="unauthorized",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    payload = _verified_oauth_state(request, state)
    redirect_to = _safe_redirect_path(str(payload["redirect_to"]))
    profile = await oauth.exchange_code(code=code, redirect_uri=_callback_uri(request))
    user = await auth_service.upsert_user_from_github(profile)
    pair = await auth_service.issue_token_pair(
        user,
        user_agent=request.headers.get("user-agent"),
    )
    logger.info("GitHub OAuth succeeded github_id=%s", profile.github_id)
    response = RedirectResponse(
        url=_frontend_dest(redirect_to),
        status_code=status.HTTP_302_FOUND,
    )
    _set_refresh_cookie(response, pair.refresh_token)
    _clear_oauth_nonce_cookie(response)
    return response


@router.post("/refresh", response_model=TokenResponse, summary="Rotate refresh token")
async def refresh(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    """Issue a new access token from the httpOnly refresh cookie."""
    _require_cookie_origin(request)
    raw = request.cookies.get(settings.auth_refresh_cookie_name)
    if not raw:
        raise AppError(
            "Not authenticated",
            code="unauthorized",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    await auth_service.enforce_refresh_rate(raw)
    try:
        pair: TokenPair = await auth_service.rotate_refresh(
            raw,
            user_agent=request.headers.get("user-agent"),
        )
    except AppError as exc:
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            _clear_refresh_cookie(response)
        raise
    _set_refresh_cookie(response, pair.refresh_token)
    return TokenResponse(
        access_token=pair.access_token,
        token_type="bearer",
        expires_in=pair.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Revoke refresh family")
async def logout(
    request: Request,
    response: Response,
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    """Revoke the refresh family and clear the cookie."""
    _require_cookie_origin(request)
    raw = request.cookies.get(settings.auth_refresh_cookie_name)
    await auth_service.logout(raw)
    _clear_refresh_cookie(response)


@router.get("/me", response_model=UserPublic, summary="Current user")
async def me(user: dict = Depends(get_current_user)) -> UserPublic:
    """Return the caller identified by the access JWT."""
    return _user_public(user)
