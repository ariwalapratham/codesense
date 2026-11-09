"""User upsert and refresh-token rotation for GitHub login."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo import ReturnDocument
from pymongo.errors import OperationFailure

from config.settings import settings
from core.errors import AppError
from core.security import (
    access_token_expires_in,
    create_access_token,
    generate_refresh_token,
    hash_refresh_token,
    new_family_id,
)
from services.github.oauth import GitHubProfile

_EMPTY_QUOTAS = {
    "repos": None,
    "ingests_per_day": None,
    "chat_per_day": None,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session_kw(session: Any) -> dict:
    return {"session": session} if session is not None else {}


@dataclass(frozen=True)
class TokenPair:
    """Access JWT plus raw refresh token (return the raw value only to the cookie layer)."""

    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str


class AuthService:
    """Persist csUsers / csRefreshTokens. Does not touch HTTP cookies."""

    def __init__(self, users: Any, refresh_tokens: Any) -> None:
        self._users = users
        self._refresh = refresh_tokens

    async def upsert_user_from_github(self, profile: GitHubProfile) -> dict:
        """Insert or update a user from a GitHub profile and set last_login_at."""
        await self._release_stale_login(profile.login, profile.github_id)
        now = _now()
        doc = await self._users.find_one_and_update(
            {"github_id": profile.github_id},
            {
                "$set": {
                    "github_login": profile.login,
                    "name": profile.name,
                    "email": profile.email,
                    "avatar_url": profile.avatar_url,
                    "last_login_at": now,
                    "status": "active",
                },
                "$setOnInsert": {
                    "role": "user",
                    "quota_overrides": dict(_EMPTY_QUOTAS),
                    "created_at": now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            raise AppError(
                "Failed to persist user",
                code="internal_error",
                status_code=500,
            )
        return doc

    async def _release_stale_login(self, login: str, github_id: int) -> None:
        """Free github_login if another github_id still holds it (rename / reassignment)."""
        stale = await self._users.find_one(
            {"github_login": login, "github_id": {"$ne": github_id}}
        )
        if not stale:
            return
        await self._users.update_one(
            {"_id": stale["_id"]},
            {"$set": {"github_login": f"_stale_{stale['github_id']}"}},
        )

    async def issue_token_pair(
        self,
        user: dict,
        *,
        user_agent: Optional[str] = None,
        family_id: Optional[str] = None,
        session: Any = None,
    ) -> TokenPair:
        """Create an access JWT and a new hashed refresh token (new family unless given)."""
        user_id = user.get("_id") or user.get("id")
        if user_id is None:
            raise AppError(
                "User is missing an id",
                code="internal_error",
                status_code=500,
            )
        user_oid = user_id if isinstance(user_id, ObjectId) else ObjectId(str(user_id))
        user_id_str = str(user_oid)
        role = str(user.get("role") or "user")
        now = _now()
        access = create_access_token(user_id=user_id_str, role=role, now=now)
        raw = generate_refresh_token()
        family = family_id or new_family_id()
        await self._refresh.insert_one(
            {
                "user_id": user_oid,
                "family_id": family,
                "token_hash": hash_refresh_token(raw),
                "created_at": now,
                "expires_at": now + timedelta(days=settings.refresh_token_ttl_days),
                "used_at": None,
                "revoked_at": None,
                "user_agent": user_agent,
            },
            **_session_kw(session),
        )
        return TokenPair(
            access_token=access,
            refresh_token=raw,
            expires_in=access_token_expires_in(),
            user_id=user_id_str,
        )

    async def rotate_refresh(
        self,
        raw_token: str,
        *,
        user_agent: Optional[str] = None,
    ) -> TokenPair:
        """Rotate a refresh token. Reuse of a used token revokes the family."""
        client = getattr(getattr(self._refresh, "database", None), "client", None)
        if client is None:
            return await self._rotate_refresh(raw_token, user_agent=user_agent)
        try:
            async with client.start_session() as session:
                async with session.start_transaction():
                    return await self._rotate_refresh(
                        raw_token, user_agent=user_agent, session=session
                    )
        except OperationFailure:
            return await self._rotate_refresh(raw_token, user_agent=user_agent)

    async def _rotate_refresh(
        self,
        raw_token: str,
        *,
        user_agent: Optional[str] = None,
        session: Any = None,
    ) -> TokenPair:
        now = _now()
        token_hash = hash_refresh_token(raw_token)
        skw = _session_kw(session)
        claimed = await self._refresh.find_one_and_update(
            {
                "token_hash": token_hash,
                "used_at": None,
                "revoked_at": None,
                "expires_at": {"$gt": now},
            },
            {"$set": {"used_at": now}},
            return_document=ReturnDocument.BEFORE,
            **skw,
        )
        if not claimed:
            existing = await self._refresh.find_one({"token_hash": token_hash}, **skw)
            if existing and existing.get("used_at") is not None:
                await self.revoke_family(existing["family_id"], session=session)
            raise AppError(
                "Invalid refresh token",
                code="unauthorized",
                status_code=401,
            )
        user = await self._users.find_one({"_id": claimed["user_id"]}, **skw)
        if not user:
            await self.revoke_family(claimed["family_id"], session=session)
            raise AppError(
                "Invalid refresh token",
                code="unauthorized",
                status_code=401,
            )
        pair = await self.issue_token_pair(
            user,
            user_agent=user_agent,
            family_id=claimed["family_id"],
            session=session,
        )
        claimed_now = await self._refresh.find_one({"_id": claimed["_id"]}, **skw)
        if claimed_now and claimed_now.get("revoked_at") is not None:
            await self.revoke_family(claimed["family_id"], session=session)
            raise AppError(
                "Invalid refresh token",
                code="unauthorized",
                status_code=401,
            )
        return pair

    async def revoke_family(self, family_id: str, *, session: Any = None) -> None:
        """Mark every token in a family as revoked."""
        await self._refresh.update_many(
            {"family_id": family_id, "revoked_at": None},
            {"$set": {"revoked_at": _now()}},
            **_session_kw(session),
        )

    async def logout(self, raw_token: Optional[str]) -> None:
        """Revoke the family for a presented refresh token. Missing tokens are a no-op."""
        if not raw_token:
            return
        doc = await self._refresh.find_one({"token_hash": hash_refresh_token(raw_token)})
        if not doc:
            return
        await self.revoke_family(doc["family_id"])
