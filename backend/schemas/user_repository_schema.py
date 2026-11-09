"""Pydantic schema for library links in MongoDB (`csUserRepositories`)."""

from datetime import datetime, timezone
from typing import Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRepositorySchema(BaseModel):
    """Link between a user and an indexed repo. Public repos are shared; this row is per user."""

    id: Optional[ObjectId] = Field(None, alias="_id", description="MongoDB document ID")
    user_id: ObjectId = Field(..., description="Library owner")
    repo_id: ObjectId = Field(..., description="csRepositories _id")
    role: Literal["member", "owner"] = Field(
        default="member",
        description="member for a linked public repo; owner is stretch/private",
    )
    added_at: datetime = Field(default_factory=_utc_now)
    last_opened_at: Optional[datetime] = Field(None, description="Last time the user opened this repo")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "user_id": "507f1f77bcf86cd799439011",
                "repo_id": "507f1f77bcf86cd799439012",
                "role": "member",
                "added_at": "2026-11-09T10:00:00Z",
                "last_opened_at": None,
            }
        },
    )
