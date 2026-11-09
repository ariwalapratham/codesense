"""Pydantic schema for refresh-token documents in MongoDB (`csRefreshTokens`)."""

from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RefreshTokenSchema(BaseModel):
    """Schema for hashed refresh tokens. The raw token is never stored."""

    id: Optional[ObjectId] = Field(None, alias="_id", description="MongoDB document ID")
    user_id: ObjectId = Field(..., description="Owning user")
    family_id: str = Field(..., description="Rotation family; reuse revokes the family")
    token_hash: str = Field(..., description="SHA-256 hex of the raw refresh token")
    created_at: datetime = Field(default_factory=_utc_now)
    expires_at: datetime = Field(..., description="TTL index target")
    used_at: Optional[datetime] = Field(None, description="Set when rotated; reuse is theft")
    revoked_at: Optional[datetime] = Field(None, description="Family or logout revocation")
    user_agent: Optional[str] = Field(None, description="Client user-agent at issue time")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "user_id": "507f1f77bcf86cd799439011",
                "family_id": "9f1c2a3b-4d5e-6f70-8192-a3b4c5d6e7f8",
                "token_hash": "ab" * 32,
                "used_at": None,
                "revoked_at": None,
                "user_agent": "Mozilla/5.0",
            }
        },
    )
