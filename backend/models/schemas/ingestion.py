"""Pydantic schemas for ingestion endpoints."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from schemas import IngestionStatus


class IngestionRequest(BaseModel):
    """Request model for repository ingestion."""

    repo_url: HttpUrl = Field(..., description="URL of the repository to ingest")
    branch: Optional[str] = Field(None, description="Specific branch to clone (optional)")
    cleanup: bool = Field(True, description="Whether to cleanup temporary repository after processing")

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

    request_id: str = Field(..., description="Ingestion request ID")
    repo_id: Optional[str] = Field(None, description="Repository ID (if created)")
    status: IngestionStatus = Field(..., description="Initial status")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "req_abc123",
                "repo_id": "507f1f77bcf86cd799439011",
                "status": "pending",
                "message": "Ingestion request created and queued for processing",
            }
        }
    )


class IngestionStatusResponse(BaseModel):
    """Response model for ingestion status query."""

    request_id: str = Field(..., description="Ingestion request ID")
    repo_id: Optional[str] = Field(None, description="Repository ID (if linked)")
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
