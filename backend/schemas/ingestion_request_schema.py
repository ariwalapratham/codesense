"""Pydantic schema for ingestion request documents in MongoDB."""

from datetime import datetime
from enum import Enum
from typing import Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(str, Enum):
    """Ingestion request status enum."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IngestionRequestSchema(BaseModel):
    """Schema for ingestion request documents in MongoDB."""

    id: Optional[ObjectId] = Field(None, alias="_id", description="MongoDB document ID (unique identifier)")
    repo_id: Optional[ObjectId] = Field(None, description="Repository ObjectId (set after repo creation)")
    owner: str = Field(..., description="Repository owner")
    repo_name: str = Field(..., description="Repository name")
    repository_url: str = Field(..., description="Full repository URL")
    branch: Optional[str] = Field(None, description="Branch to clone")
    status: IngestionStatus = Field(
        default=IngestionStatus.PENDING,
        description="Request status"
    )
    error: Optional[str] = Field(None, description="Error message if failed")
    retry_count: int = Field(default=0, description="Number of retry attempts")
    retry_after: Optional[datetime] = Field(
        None,
        description="Timestamp after which retry is allowed (for rate limiting)"
    )
    created_at: datetime = Field(default_factory=datetime.now, description="Request creation timestamp")
    updated_at: datetime = Field(default_factory=datetime.now, description="Last update timestamp")
    started_at: Optional[datetime] = Field(None, description="When processing started")
    completed_at: Optional[datetime] = Field(None, description="When processing completed")
    message: Optional[str] = Field(None, description="Status message")

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        json_schema_extra={
            "example": {
                "id": "507f1f77bcf86cd799439011",
                "repo_id": "507f1f77bcf86cd799439012",
                "owner": "owner",
                "repo_name": "repo",
                "repository_url": "https://github.com/owner/repo.git",
                "branch": "main",
                "status": "processing",
                "error": None,
                "retry_count": 0,
                "retry_after": None,
                "created_at": "2024-01-01T10:00:00Z",
                "updated_at": "2024-01-01T10:00:05Z",
                "started_at": "2024-01-01T10:00:05Z",
                "completed_at": None,
                "message": "Processing repository..."
            }
        }
    )

