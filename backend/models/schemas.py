"""Pydantic models for API requests and responses."""

from typing import Optional
from pydantic import BaseModel, Field, HttpUrl


class IngestionRequest(BaseModel):
    """Request model for repository ingestion."""

    repo_url: HttpUrl = Field(..., description="URL of the repository to ingest")
    branch: Optional[str] = Field(None, description="Specific branch to clone (optional)")
    cleanup: bool = Field(True, description="Whether to cleanup temporary repository after processing")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "repo_url": "https://github.com/owner/repo.git",
                "branch": "main",
                "cleanup": True
            }
        }


class IngestionResponse(BaseModel):
    """Response model for repository ingestion."""

    success: bool = Field(..., description="Whether ingestion was successful")
    message: str = Field(..., description="Status message")
    metadata: Optional[dict] = Field(None, description="Ingestion metadata")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Successfully ingested repository owner_repo. Processed 50 files and created 200 chunks.",
                "metadata": {
                    "repo_id": "owner_repo",
                    "status": "completed",
                    "files_processed": 50,
                    "chunks_processed": 200,
                    "commit_hash": "abc1234"
                }
            }
        }
