from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bson import ObjectId
from pymongo import ReturnDocument

from config.settings import settings
from core.errors import AppError
from core.security import hash_refresh_token
from services.auth_service import AuthService
from services.github.oauth import GitHubProfile

_SECRET = "unit-test-jwt-secret-not-for-prod"


class MemoryCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def _match(self, doc: dict, filt: dict) -> bool:
        for key, expected in filt.items():
            actual = doc.get(key)
            if isinstance(expected, dict) and any(str(op).startswith("$") for op in expected):
                if "$gt" in expected and (actual is None or not actual > expected["$gt"]):
                    return False
                if "$ne" in expected and actual == expected["$ne"]:
                    return False
                continue
            if expected is None:
                if actual is not None:
                    return False
                continue
            if actual != expected:
                return False
        return True

    def _apply(self, doc: dict, update: dict, *, is_new: bool) -> None:
        if "$set" in update:
            doc.update(update["$set"])
        if is_new and "$setOnInsert" in update:
            for key, value in update["$setOnInsert"].items():
                doc.setdefault(key, value)

    async def find_one(self, filt: dict, **kwargs):
        for doc in self.docs:
            if self._match(doc, filt):
                return dict(doc)
        return None

    async def insert_one(self, doc: dict, **kwargs):
        stored = dict(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(self, filt: dict, update: dict, **kwargs):
        for doc in self.docs:
            if self._match(doc, filt):
                self._apply(doc, update, is_new=False)
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def find_one_and_update(
        self,
        filt: dict,
        update: dict,
        upsert: bool = False,
        return_document=None,
        **kwargs,
    ):
        idx = next((i for i, doc in enumerate(self.docs) if self._match(doc, filt)), None)
        if idx is None:
            if not upsert:
                return None
            created = dict(filt)
            created["_id"] = ObjectId()
            self._apply(created, update, is_new=True)
            self.docs.append(created)
            return dict(created)
        before = dict(self.docs[idx])
        self._apply(self.docs[idx], update, is_new=False)
        if return_document == ReturnDocument.BEFORE:
            return before
        return dict(self.docs[idx])

    async def update_many(self, filt: dict, update: dict, **kwargs):
        count = 0
        for doc in self.docs:
            if self._match(doc, filt):
                self._apply(doc, update, is_new=False)
                count += 1
        return SimpleNamespace(modified_count=count)


@pytest.fixture
def jwt_secret(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", _SECRET)


@pytest.fixture
def auth(jwt_secret):
    return AuthService(MemoryCollection(), MemoryCollection())


def _profile(**overrides) -> GitHubProfile:
    data = dict(
        github_id=42,
        login="octocat",
        name="The Octocat",
        email="octocat@example.com",
        avatar_url="https://example/a",
    )
    data.update(overrides)
    return GitHubProfile(**data)


async def test_upsert_releases_stale_github_login(auth):
    first = await auth.upsert_user_from_github(_profile())
    other = await auth.upsert_user_from_github(_profile(github_id=99, login="other"))
    claimed = await auth.upsert_user_from_github(_profile(github_id=99, login="octocat"))
    stored_first = await auth._users.find_one({"_id": first["_id"]})
    assert claimed["_id"] == other["_id"]
    assert claimed["github_login"] == "octocat"
    assert stored_first["github_login"] == f"_stale_{first['github_id']}"


async def test_upsert_user_from_github_inserts_then_refreshes_login(auth):
    first = await auth.upsert_user_from_github(_profile())
    second = await auth.upsert_user_from_github(_profile(login="octocat-new", name="New"))
    assert first["_id"] == second["_id"]
    assert second["github_login"] == "octocat-new"
    assert second["name"] == "New"
    assert second["github_id"] == 42
    assert second["role"] == "user"


async def test_issue_and_rotate_refresh_keeps_family(auth):
    user = await auth.upsert_user_from_github(_profile())
    first = await auth.issue_token_pair(user, user_agent="pytest")
    rotated = await auth.rotate_refresh(first.refresh_token, user_agent="pytest")
    assert rotated.user_id == first.user_id
    assert rotated.refresh_token != first.refresh_token
    stored = [doc for doc in auth._refresh.docs]
    assert len(stored) == 2
    assert stored[0]["family_id"] == stored[1]["family_id"]
    assert stored[0]["used_at"] is not None
    assert stored[1]["used_at"] is None
    assert stored[0]["token_hash"] == hash_refresh_token(first.refresh_token)


async def test_refresh_reuse_revokes_family(auth):
    user = await auth.upsert_user_from_github(_profile())
    first = await auth.issue_token_pair(user)
    await auth.rotate_refresh(first.refresh_token)
    with pytest.raises(AppError) as exc:
        await auth.rotate_refresh(first.refresh_token)
    assert exc.value.status_code == 401
    assert all(doc["revoked_at"] is not None for doc in auth._refresh.docs)


async def test_logout_revokes_family(auth):
    user = await auth.upsert_user_from_github(_profile())
    pair = await auth.issue_token_pair(user)
    await auth.logout(pair.refresh_token)
    assert auth._refresh.docs[0]["revoked_at"] is not None
    with pytest.raises(AppError):
        await auth.rotate_refresh(pair.refresh_token)


async def test_rotate_aborts_if_family_revoked_during_insert(auth):
    user = await auth.upsert_user_from_github(_profile())
    first = await auth.issue_token_pair(user)
    orig_insert = auth._refresh.insert_one

    async def insert_after_revoke(doc, **kwargs):
        auth._refresh.docs[0]["revoked_at"] = datetime.now(timezone.utc)
        return await orig_insert(doc, **kwargs)

    auth._refresh.insert_one = insert_after_revoke
    with pytest.raises(AppError) as exc:
        await auth.rotate_refresh(first.refresh_token)
    assert exc.value.status_code == 401
    assert all(doc.get("revoked_at") is not None for doc in auth._refresh.docs)


async def test_expired_refresh_is_rejected(auth):
    user = await auth.upsert_user_from_github(_profile())
    pair = await auth.issue_token_pair(user)
    auth._refresh.docs[0]["expires_at"] = datetime.now(timezone.utc) - timedelta(days=1)
    with pytest.raises(AppError) as exc:
        await auth.rotate_refresh(pair.refresh_token)
    assert exc.value.code == "unauthorized"
