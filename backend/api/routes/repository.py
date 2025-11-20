"""Repository API routes for repository management."""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, Query, status

from models.schemas import (
    ParsingStatsResponse,
    RepositoryInfo,
    RepositoryListResponse,
    RepositorySummary,
    RepositoryStats,
)
from services.mongodb_service import MongoDBService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/repository",
    tags=["repository"],
)

mongodb_service = MongoDBService()


def _to_repository_info(repo_dict: Dict) -> Optional[RepositoryInfo]:
    """
    Convert a repository dictionary from MongoDB into RepositoryInfo.

    Args:
        repo_dict: Raw repository dictionary

    Returns:
        RepositoryInfo model or None if conversion fails
    """
    parsed_repo = dict(repo_dict)
    parsing_stats = parsed_repo.get("parsing_stats")
    if parsing_stats:
        parsed_repo["parsing_stats"] = ParsingStatsResponse(**parsing_stats)
    try:
        return RepositoryInfo(**parsed_repo)
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to convert repository to RepositoryInfo: %s", exc)
        return None


def _to_repository_summary(repo_dict: Dict) -> RepositorySummary:
    """
    Convert a repository dictionary into RepositorySummary.

    Args:
        repo_dict: Raw repository dictionary

    Returns:
        RepositorySummary model

    Raises:
        ValueError: If conversion fails
    """
    try:
        return RepositorySummary(**repo_dict)
    except Exception as exc:  # pylint: disable=broad-except
        raise ValueError(f"Invalid repository summary payload: {exc}") from exc


@router.get(
    "/",
    response_model=RepositoryListResponse,
    summary="List repositories",
    description="Get a paginated list of repositories with optional search.",
)
async def list_repositories(
    skip: int = Query(0, ge=0, description="Number of repositories to skip"),
    limit: int = Query(10, ge=1, le=100, description="Maximum number of repositories to return"),
    search: Optional[str] = Query(None, description="Search repo_name, owner, or repository_url"),
) -> RepositoryListResponse:
    """
    List repositories with pagination and optional search.

    Args:
        skip: Number of repositories to skip
        limit: Maximum number of repositories to return
        search: Optional search term (case-insensitive)

    Returns:
        RepositoryListResponse containing repositories and pagination metadata
    """
    try:
        repositories, total = await mongodb_service.list_repositories(
            skip=skip,
            limit=limit,
            search=search,
        )

        repo_summaries = []
        for repo_dict in repositories:
            try:
                repo_summaries.append(_to_repository_summary(repo_dict))
            except ValueError as exc:
                logger.error("Failed to convert repository summary: %s", exc, exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to convert repository summary document",
                ) from exc

        logger.info(
            "Listed %d repositories (skip: %d, limit: %d, total: %d, search: %s)",
            len(repo_summaries),
            skip,
            limit,
            total,
            search or "none",
        )

        return RepositoryListResponse(
            repositories=repo_summaries,
            total=total,
            skip=skip,
            limit=limit,
        )

    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error listing repositories: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list repositories: {exc}",
        ) from exc


@router.get(
    "/{repo_id}",
    response_model=RepositoryInfo,
    summary="Get repository by ID",
    description="Get detailed information about a specific repository.",
)
async def get_repository(repo_id: str) -> RepositoryInfo:
    """
    Get detailed information about a repository.

    Args:
        repo_id: Repository identifier (MongoDB ObjectId as string)

    Returns:
        RepositoryInfo object with id field (MongoDB ObjectId as string)
    """
    try:
        repo_dict = await mongodb_service.get_repository(repo_id)
        if not repo_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository not found: {repo_id}",
            )

        repo_info = _to_repository_info(repo_dict)
        if repo_info is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to convert repository document",
            )

        logger.info("Retrieved repository: %s", repo_id)
        return repo_info

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error fetching repository %s: %s", repo_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch repository: {exc}",
        ) from exc


@router.get(
    "/{repo_id}/stats",
    response_model=RepositoryStats,
    summary="Get repository statistics",
    description="Get aggregated statistics about a repository.",
)
async def get_repository_stats(repo_id: str) -> RepositoryStats:
    """
    Get repository statistics including chunk and language distributions.

    Args:
        repo_id: Repository identifier (MongoDB ObjectId as string)

    Returns:
        RepositoryStats object with id field (MongoDB ObjectId as string)
    """
    try:
        stats_dict = await mongodb_service.get_repository_stats(repo_id)
        if not stats_dict:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Repository not found: {repo_id}",
            )

        stats = RepositoryStats(**stats_dict)
        logger.info("Retrieved statistics for repository: %s", repo_id)
        return stats

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error fetching repository stats for %s: %s", repo_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch repository statistics: {exc}",
        ) from exc
