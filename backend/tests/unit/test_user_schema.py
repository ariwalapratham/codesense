from datetime import datetime, timezone

from bson import ObjectId

from schemas import RefreshTokenSchema, UserRepositorySchema, UserSchema


def test_user_schema_keeps_github_id_and_quota_defaults():
    user = UserSchema(github_id=12345678, github_login="octocat")
    dumped = user.model_dump()
    assert dumped["github_id"] == 12345678
    assert dumped["github_login"] == "octocat"
    assert dumped["role"] == "user"
    assert dumped["status"] == "active"
    assert dumped["quota_overrides"]["repos"] is None


def test_user_repository_schema_defaults_to_member():
    user_id = ObjectId()
    repo_id = ObjectId()
    link = UserRepositorySchema(user_id=user_id, repo_id=repo_id)
    dumped = link.model_dump()
    assert dumped["user_id"] == user_id
    assert dumped["repo_id"] == repo_id
    assert dumped["role"] == "member"
    assert dumped["last_opened_at"] is None


def test_refresh_token_schema_requires_hash_and_family():
    user_id = ObjectId()
    expires = datetime.now(timezone.utc)
    token = RefreshTokenSchema(
        user_id=user_id,
        family_id="family-1",
        token_hash="ab" * 32,
        expires_at=expires,
    )
    dumped = token.model_dump()
    assert dumped["user_id"] == user_id
    assert dumped["family_id"] == "family-1"
    assert dumped["used_at"] is None
    assert dumped["revoked_at"] is None
    assert "raw" not in dumped
