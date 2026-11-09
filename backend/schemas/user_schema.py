"""Pydantic schema for user documents in MongoDB (`csUsers`)."""

from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class QuotaOverrides(BaseModel):
    """Per-user quota overrides; null means the default limit applies."""

    repos: Optional[int] = Field(default=None, description="Max linked repos")
    ingests_per_day: Optional[int] = Field(default=None, description="Max ingests per day")
    chat_per_day: Optional[int] = Field(default=None, description="Max chat messages per day")


class UserSchema(BaseModel):
    """Schema for user documents in MongoDB."""

    id: Optional[ObjectId] = Field(None, alias="_id", description="MongoDB document ID")
    github_id: int = Field(..., description="GitHub numeric user id")
    github_login: str = Field(..., description="GitHub login; refreshed on each sign-in")
    name: Optional[str] = Field(None, description="Display name")
    email: Optional[str] = Field(None, description="Primary email if GitHub provided one")
    avatar_url: Optional[str] = Field(None, description="GitHub avatar URL")
    role: Literal["user"] = Field(default="user", description="Application role")
    status: Literal["active"] = Field(default="active", description="Account status")
    quota_overrides: QuotaOverrides = Field(default_factory=QuotaOverrides)
    created_at: datetime = Field(default_factory=_utc_now)
    last_login_at: Optional[datetime] = Field(None, description="Last successful login")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "github_id": 12345678,
                "github_login": "octocat",
                "name": "The Octocat",
                "email": None,
                "avatar_url": "https://avatars.githubusercontent.com/u/1",
                "role": "user",
                "status": "active",
                "quota_overrides": {
                    "repos": None,
                    "ingests_per_day": None,
                    "chat_per_day": None,
                },
            }
        },
    )
