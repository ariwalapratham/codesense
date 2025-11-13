"""Pydantic schema for repository documents in MongoDB."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class ParsingStats(BaseModel):
    """Parsing statistics for a repository."""

    files_parsed: int = Field(..., description="Number of files parsed")
    total_functions: int = Field(..., description="Total number of functions extracted")
    total_classes: int = Field(..., description="Total number of classes extracted")
    total_imports: int = Field(..., description="Total number of imports extracted")
    languages: List[str] = Field(..., description="List of programming languages found")


class RepositorySchema(BaseModel):
    """Schema for repository documents in MongoDB."""

    repo_id: str = Field(..., description="Unique repository identifier")
    owner: str = Field(..., description="Repository owner/username")
    repo_name: str = Field(..., description="Repository name")
    repository_url: str = Field(..., description="Full repository URL")
    branch: Optional[str] = Field(None, description="Branch name")
    commit_hash: Optional[str] = Field(None, description="Current commit hash")
    status: str = Field(
        default="processing",
        description="Repository ingestion status: processing, completed, failed"
    )
    files_processed: int = Field(default=0, description="Number of files processed")
    chunks_count: int = Field(default=0, description="Total number of chunks created")
    chunks_with_embeddings: int = Field(
        default=0, description="Number of chunks with successful embeddings"
    )
    parsing_stats: Optional[ParsingStats] = Field(
        None, description="Parsing statistics"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="First ingestion timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )
    last_ingested_at: Optional[datetime] = Field(
        None, description="Last successful ingestion timestamp"
    )

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "repo_id": "owner_repo",
                "owner": "owner",
                "repo_name": "repo",
                "repository_url": "https://github.com/owner/repo.git",
                "branch": "main",
                "commit_hash": "abc1234",
                "status": "completed",
                "files_processed": 50,
                "chunks_count": 200,
                "chunks_with_embeddings": 200,
                "parsing_stats": {
                    "files_parsed": 50,
                    "total_functions": 120,
                    "total_classes": 15,
                    "total_imports": 45,
                    "languages": ["python", "javascript"]
                },
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "last_ingested_at": "2024-01-01T00:00:00Z"
            }
        }

