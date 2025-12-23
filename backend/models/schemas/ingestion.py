"""Pydantic schemas for ingestion endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from schemas import IngestionStatus


class IngestionRequest(BaseModel):
    """Request model for repository ingestion."""

    repo_url: HttpUrl = Field(..., description="URL of the repository to ingest")
    branch: Optional[str] = Field("main", description="Specific branch to clone (optional)")
    cleanup: bool = Field(True,
    description="Whether to cleanup temporary repository after processing")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "repo_url": "https://github.com/owner/repo.git",
                "branch": "main",
                "cleanup": True,
            }
        }
    )


class IngestionResponse(BaseModel):
    """Response model for repository ingestion request creation."""

    request_id: Optional[str] = Field(None, description="Ingestion request ID (MongoDB ObjectId)")
    encoded_id: Optional[str] = Field(None, description="Encoded repository identifier (base64) - use this for status queries")
    repo_id: Optional[str] = Field(None, description="Repository ID (if linked)")
    owner: str = Field(..., description="Repository owner")
    repo_name: str = Field(..., description="Repository name")
    branch: str = Field(..., description="Repository branch")
    commit_hash: Optional[str] = Field(None, description="Repository commit hash")
    status: IngestionStatus = Field(..., description="Initial status")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "6631c2f1e8a13b0b2fcd91aa",
                "encoded_id": "eyJicmFuY2giOiJtYWluIiwib3duZXIiOiJvd25lciIsInJlcG9fbmFtZSI6InJlcG8ifQ",
                "owner": "owner",
                "repo_name": "repo",
                "branch": "main",
                "status": "pending",
                "message": "Ingestion request created and queued",
            }
        }
    )

class IngestionStatusResponse(BaseModel):
    """Response model for ingestion status query."""

    request_id: str = Field(..., description="Ingestion request ID")
    repo_id: Optional[str] = Field(None, description="Repository ID (if linked)")
    owner: Optional[str] = Field(None, description="Repository owner")
    repo_name: Optional[str] = Field(None, description="Repository name")
    branch: Optional[str] = Field(None, description="Repository branch")
    commit_hash: Optional[str] = Field(None, description="Repository commit hash")
    status: IngestionStatus = Field(..., description="Current request status")
    error: Optional[str] = Field(None, description="Error message if failed")
    retry_count: int = Field(..., description="Number of retry attempts")
    retry_after: Optional[datetime] = Field(
        None, description="Timestamp after which retry is allowed (for rate limiting)"
    )
    message: Optional[str] = Field(None, description="Status message")
    created_at: datetime = Field(..., description="Request creation timestamp")
    started_at: Optional[datetime] = Field(None, description="When processing started")
    completed_at: Optional[datetime] = Field(None, description="When processing completed")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "req_abc123",
                "repo_id": "507f1f77bcf86cd799439011",
                "owner": "owner",
                "repo_name": "repo",
                "branch": "main",
                "status": "processing",
                "error": None,
                "retry_count": 0,
                "retry_after": None,
                "message": "Processing repository...",
                "created_at": "2024-01-01T10:00:00Z",
                "started_at": "2024-01-01T10:00:05Z",
                "completed_at": None,
            }
        }
    )
