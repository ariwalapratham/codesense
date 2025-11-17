"""Pydantic models for API requests and responses."""

from datetime import datetime
from typing import Dict, List, Optional

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

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Successfully ingested repository owner_repo. Processed 50 files and created 200 chunks."
            }
        }


class ParsingStatsResponse(BaseModel):
    """Parsing statistics response model."""

    files_parsed: int = Field(..., description="Number of files parsed")
    total_functions: int = Field(..., description="Total number of functions extracted")
    total_classes: int = Field(..., description="Total number of classes extracted")
    total_imports: int = Field(..., description="Total number of imports extracted")
    languages: List[str] = Field(..., description="List of programming languages found")


class RepositoryInfo(BaseModel):
    """Response model for repository information."""

    repo_id: str = Field(..., description="Unique repository identifier")
    owner: str = Field(..., description="Repository owner/username")
    repo_name: str = Field(..., description="Repository name")
    repository_url: str = Field(..., description="Full repository URL")
    branch: Optional[str] = Field(None, description="Branch name")
    commit_hash: Optional[str] = Field(None, description="Current commit hash")
    status: str = Field(..., description="Repository ingestion status")
    files_processed: int = Field(..., description="Number of files processed")
    chunks_count: int = Field(..., description="Total number of chunks created")
    chunks_with_embeddings: int = Field(..., description="Number of chunks with successful embeddings")
    parsing_stats: Optional[ParsingStatsResponse] = Field(None, description="Parsing statistics")
    created_at: datetime = Field(..., description="First ingestion timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    last_ingested_at: Optional[datetime] = Field(None, description="Last successful ingestion timestamp")

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


class RepositoryListResponse(BaseModel):
    """Response model for paginated repository list."""

    repositories: List[RepositoryInfo] = Field(..., description="List of repositories")
    total: int = Field(..., description="Total number of repositories")
    skip: int = Field(..., description="Number of repositories skipped")
    limit: int = Field(..., description="Maximum number of repositories returned")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "repositories": [
                    {
                        "repo_id": "owner_repo",
                        "owner": "owner",
                        "repo_name": "repo",
                        "repository_url": "https://github.com/owner/repo.git",
                        "status": "completed",
                        "files_processed": 50,
                        "chunks_count": 200
                    }
                ],
                "total": 1,
                "skip": 0,
                "limit": 10
            }
        }


class RepositoryStats(BaseModel):
    """Response model for repository statistics."""

    repo_id: str = Field(..., description="Repository identifier")
    repo_name: str = Field(..., description="Repository name")
    total_chunks: int = Field(..., description="Total number of chunks")
    total_files: int = Field(..., description="Total number of files")
    language_distribution: Dict[str, int] = Field(..., description="Number of chunks per language")
    chunk_type_distribution: Dict[str, int] = Field(..., description="Number of chunks per type")

    class Config:
        """Pydantic config."""

        json_schema_extra = {
            "example": {
                "repo_id": "owner_repo",
                "repo_name": "repo",
                "total_chunks": 200,
                "total_files": 50,
                "language_distribution": {
                    "python": 150,
                    "javascript": 50
                },
                "chunk_type_distribution": {
                    "function": 120,
                    "class": 30,
                    "module_level": 50
                }
            }
        }
