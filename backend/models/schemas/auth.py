"""API request/response models for auth."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class TokenResponse(BaseModel):
    """Response for POST /auth/refresh."""

    access_token: str = Field(..., description="Short-lived JWT")
    token_type: str = Field(default="bearer", description="Always bearer")
    expires_in: int = Field(..., description="Access token lifetime in seconds")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJ...",
                "token_type": "bearer",
                "expires_in": 900,
            }
        }
    )


class UserPublic(BaseModel):
    """Public user payload for GET /auth/me."""

    id: str = Field(..., description="User ObjectId string")
    github_login: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    role: str = "user"
    created_at: datetime

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "6720abc",
                "github_login": "octocat",
                "name": "The Octocat",
                "avatar_url": "https://avatars.githubusercontent.com/u/1",
                "role": "user",
                "created_at": "2026-11-04T10:00:00Z",
            }
        }
    )
