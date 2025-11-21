"""Pydantic models for API requests and responses."""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


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
                "cleanup": True
            }
        }
    )


class IngestionResponse(BaseModel):
    """Response model for repository ingestion."""

    success: bool = Field(..., description="Whether ingestion was successful")
    message: str = Field(..., description="Status message")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Successfully ingested repository owner_repo. Processed 50 files and created 200 chunks."
            }
        }
    )


class ParsingStatsResponse(BaseModel):
    """Parsing statistics response model."""

    files_parsed: int = Field(..., description="Number of files parsed")
    total_functions: int = Field(..., description="Total number of functions extracted")
    total_classes: int = Field(..., description="Total number of classes extracted")
    total_imports: int = Field(..., description="Total number of imports extracted")
    languages: List[str] = Field(..., description="List of programming languages found")


class DeleteRepositoryResponse(BaseModel):
    """Response model for repository deletion."""

    success: bool = Field(..., description="Whether deletion was successful")
    message: str = Field(..., description="Status message")
    details: Dict[str, bool] = Field(..., description="Status of each deletion step")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Successfully deleted repository 507f1f77bcf86cd799439011 and all associated data",
                "details": {
                    "chunks_deleted": True,
                    "queries_deleted": True,
                    "vector_store_deleted": True,
                    "repository_deleted": True
                }
            }
        }
    )


class RepositoryInfo(BaseModel):
    """Response model for repository information."""

    id: str = Field(..., description="Unique repository identifier (MongoDB ObjectId as string)")
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

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "507f1f77bcf86cd799439011",
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
    )


class RepositorySummary(BaseModel):
    """Lightweight summary for repository list endpoint.
    
    Minimal fields for repository cards display.
    """

    id: str = Field(..., description="Unique repository identifier (MongoDB ObjectId as string)")
    repo_name: str = Field(..., description="Repository name")
    repository_url: str = Field(..., description="Full repository URL")
    files_processed: int = Field(..., description="Number of files processed")
    created_at: datetime = Field(..., description="First ingestion timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class RepositoryListResponse(BaseModel):
    """Response model for paginated repository list."""

    repositories: List[RepositorySummary] = Field(..., description="List of repositories")
    total: int = Field(..., description="Total number of repositories")
    skip: int = Field(..., description="Number of repositories skipped")
    limit: int = Field(..., description="Maximum number of repositories returned")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "repositories": [
                    {
                        "id": "507f1f77bcf86cd799439011",
                        "repo_name": "react-dashboard",
                        "repository_url": "https://github.com/owner/react-dashboard.git",
                        "files_processed": 245,
                        "created_at": "2024-01-15T00:00:00Z"
                    }
                ],
                "total": 1,
                "skip": 0,
                "limit": 10
            }
        }
    )


class RepositoryStats(BaseModel):
    """Response model for repository statistics."""

    id: str = Field(..., description="Repository identifier (MongoDB ObjectId as string)")
    repo_name: str = Field(..., description="Repository name")
    total_chunks: int = Field(..., description="Total number of chunks")
    total_files: int = Field(..., description="Total number of files")
    language_distribution: Dict[str, int] = Field(..., description="Number of chunks per language")
    chunk_type_distribution: Dict[str, int] = Field(..., description="Number of chunks per type")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "507f1f77bcf86cd799439011",
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
    )
