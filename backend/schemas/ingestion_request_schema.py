"""Pydantic schema for ingestion request documents in MongoDB."""

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


class IngestionStatus(str, Enum):
    """Ingestion request status enum."""

    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class IngestionStage(str, Enum):
    """Pipeline stage reported on an ingestion job."""

    QUEUED = "queued"
    FETCHING = "fetching"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    FINALIZING = "finalizing"


class IngestionProgress(BaseModel):
    """Progress counters for an ingestion job."""

    stage: IngestionStage = Field(default=IngestionStage.QUEUED, description="Current pipeline stage")
    files_total: int = Field(default=0, ge=0, description="Files discovered")
    files_done: int = Field(default=0, ge=0, description="Files processed")
    chunks_total: int = Field(default=0, ge=0, description="Chunks produced")
    chunks_embedded: int = Field(default=0, ge=0, description="Chunks with vectors written")


class IngestionLease(BaseModel):
    """Worker lease so an expired job can be claimed again."""

    worker_id: Optional[str] = Field(default=None, description="Process that holds the lease")
    heartbeat_at: Optional[datetime] = Field(default=None, description="Last heartbeat")
    expires_at: Optional[datetime] = Field(default=None, description="Lease expiry")


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
    target_commit: Optional[str] = Field(
        None,
        description="Remote commit the job should index (7-char hash)",
    )
    requested_by: Optional[ObjectId] = Field(None, description="User who requested the job")
    type: Literal["full", "incremental"] = Field(default="full", description="Job type")
    trigger: Literal["user", "retry", "webhook"] = Field(
        default="user",
        description="What created the job",
    )
    cleanup: bool = Field(default=True, description="Delete the temp clone when the job finishes")
    base_commit: Optional[str] = Field(
        None,
        description="Indexed commit an incremental job starts from",
    )
    progress: IngestionProgress = Field(default_factory=IngestionProgress, description="Stage and counts")
    lease: Optional[IngestionLease] = Field(default=None, description="Worker lease")
    claim_count: int = Field(default=0, ge=0, description="Times a worker claimed this job")

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
                "message": "Processing repository...",
                "target_commit": "a1b2c3d",
                "type": "full",
                "trigger": "user",
                "cleanup": True,
                "progress": {
                    "stage": "embedding",
                    "files_total": 312,
                    "files_done": 312,
                    "chunks_total": 1480,
                    "chunks_embedded": 640,
                },
                "lease": {
                    "worker_id": "api-1",
                    "heartbeat_at": "2024-01-01T10:00:20Z",
                    "expires_at": "2024-01-01T10:01:05Z",
                },
                "claim_count": 1,
            }
        }
    )
