"""Repository API routes for repository management."""

import logging
from datetime import datetime
from typing import Dict, Optional
from urllib.parse import urlparse
from bson import ObjectId

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from models.schemas import (
    DeleteRepositoryResponse,
    IngestionRequest,
    IngestionResponse,
    IngestionStatusResponse,
    ParsingStatsResponse,
    RepositoryInfo,
    RepositoryListResponse,
    RepositorySummary,
    RepositoryStats,
)
from schemas import IngestionStatus
from services.ingestion_service import IngestionService
from services.mongodb_service import MongoDBService
from database.mongodb import mongodb

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/repository",
    tags=["repository"],
)

mongodb_service = MongoDBService()
ingestion_service = IngestionService()


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


@router.delete(
    "/{repo_id}",
    response_model=DeleteRepositoryResponse,
    summary="Delete repository",
    description="Delete a repository and all its associated data (chunks, queries, vector store).",
)
async def delete_repository(repo_id: str) -> DeleteRepositoryResponse:
    """
    Delete a repository and all its data.

    Deletes:
    - All code chunks
    - All queries (if collection exists)
    - Vector store files (FAISS index and mappings)
    - Repository record

    Args:
        repo_id: Repository identifier (MongoDB ObjectId as string)

    Returns:
        DeleteRepositoryResponse with deletion status
    """
    try:
        success, message, details = await ingestion_service.delete_repository(repo_id)

        if not success:
            # Check if repository was not found
            if "not found" in message.lower():
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=message,
                )
            # For partial deletions, return 207 Multi-Status or 200 with details
            # Using 200 with success=False to show partial failure
            logger.warning("Partial deletion for repository %s: %s", repo_id, message)

        logger.info("Delete repository operation completed for: %s", repo_id)
        return DeleteRepositoryResponse(
            success=success,
            message=message,
            details=details
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error deleting repository %s: %s", repo_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete repository: {exc}",
        ) from exc


def _extract_repo_info(repo_url: str) -> tuple:
    """
    Extract repository owner and name from URL.

    Args:
        repo_url: Repository URL (GitHub/GitLab)

    Returns:
        Tuple of (owner, repo_name)
    """
    parsed = urlparse(str(repo_url))
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) >= 2:
        owner = path_parts[0]
        repo_name = path_parts[1].replace(".git", "")
        return owner, repo_name
    else:
        raise ValueError(f"Invalid repository URL format: {repo_url}")


@router.post(
    "/ingest",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a repository",
    description="Create an ingestion request to clone, parse, chunk, embed, and store a repository for code search and analysis. Returns immediately with request_id for status tracking."
)
async def ingest_repository(
    request: IngestionRequest,
    background_tasks: BackgroundTasks
) -> IngestionResponse:
    """
    Create an ingestion request and start processing in background.

    Pipeline steps (run asynchronously):
    1. Clone repository to temporary directory
    2. Discover and parse code files
    3. Chunk code into semantic pieces
    4. Generate embeddings
    5. Store in vector database and metadata store
    6. Cleanup temporary repository

    Args:
        request: Ingestion request with repository URL and options
        background_tasks: FastAPI background tasks for async processing

    Returns:
        IngestionResponse with request_id and initial status

    Raises:
        HTTPException: If request creation fails or rate limit exceeded
    """
    try:
        logger.info("Received ingestion request for repository: %s", request.repo_url)

        # Convert HttpUrl to string
        repo_url = str(request.repo_url)

        # Extract repository information
        try:
            owner, repo_name = _extract_repo_info(repo_url)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            ) from e

        # Check if repository already exists and is completed
        existing_repo = await mongodb_service.find_repository_by_owner_and_name(owner, repo_name)
        if existing_repo:
            repo_id = str(existing_repo.get("_id"))
            # Get the latest completed ingestion request for this repo
            latest_completed_request = await mongodb.db["csIngestionRequests"].find_one(
                {"repo_id": ObjectId(repo_id), "status": "completed"},
                sort=[("created_at", -1)]
            )

            if latest_completed_request:
                # Update any pending/processing requests for this repo to completed
                await mongodb.db["csIngestionRequests"].update_many(
                    {
                        "repo_id": ObjectId(repo_id),
                        "status": {"$in": ["pending", "processing"]}
                    },
                    {
                        "$set": {
                            "status": "completed",
                            "message": f"Repository {owner}/{repo_name} has already been ingested successfully",
                            "completed_at": datetime.now(),
                            "updated_at": datetime.now()
                        },
                        "$unset": {"error": ""}
                    }
                )

                return IngestionResponse(
                    request_id=str(latest_completed_request["_id"]),
                    repo_id=repo_id,
                    status=IngestionStatus.COMPLETED,
                    message=f"Repository {owner}/{repo_name} has already been ingested successfully"
                )

        # Check for active ingestion request (only if not completed)
        active_request = await mongodb_service.get_active_ingestion_request(owner, repo_name)
        if active_request:
            # If repository exists but no completed request found, update active request to completed
            if existing_repo:
                repo_id = str(existing_repo.get("_id"))
                # Update the active request to completed since repo already exists
                await mongodb_service.update_ingestion_request_status(
                    request_id=active_request.get("id"),
                    status="completed",
                    message=f"Repository {owner}/{repo_name} has already been ingested successfully"
                )
                return IngestionResponse(
                    request_id=active_request.get("id"),
                    repo_id=repo_id,
                    status=IngestionStatus.COMPLETED,
                    message=f"Repository {owner}/{repo_name} has already been ingested successfully"
                )

            if active_request.get("status") == "processing":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Repository {owner}/{repo_name} is already being processed. Request ID: {active_request.get('id')}"
                )

            # If pending and repo doesn't exist yet, return pending
            return IngestionResponse(
                request_id=active_request.get("id"),
                repo_id=active_request.get("repo_id"),
                status=IngestionStatus.PENDING,
                message="Ingestion request already exists and is pending"
            )

        # Check retry rate limit
        allowed, retry_count, retry_after = await mongodb_service.check_retry_rate_limit(
            owner=owner,
            repo_name=repo_name,
            time_window_hours=2,
            max_retries=3
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "message": f"Maximum 3 retries allowed within 2 hours. Current retry count: {retry_count}",
                    "retry_count": retry_count,
                    "retry_after": retry_after.isoformat() if retry_after else None
                }
            )

        # Create ingestion request
        request_data = {
            "owner": owner,
            "repo_name": repo_name,
            "repository_url": repo_url,
            "branch": request.branch,
            "status": "pending"
        }

        request_object_id = await mongodb_service.create_ingestion_request(request_data)
        if not request_object_id:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to create ingestion request"
            )

        # Use the ObjectId as the request_id
        request_id = str(request_object_id)

        # Create or get repository record (without status - keep it clean)
        # The repository will be created/updated during ingestion
        repo_id = None
        if existing_repo:
            repo_id = str(existing_repo.get("_id"))

        # Link repo_id to ingestion request if available
        if repo_id:
            await mongodb_service.update_ingestion_request_status(
                request_id=request_id,
                status="pending",
                repo_id=repo_id
            )

        # Add background task for ingestion
        background_tasks.add_task(
            ingestion_service.ingest_repository,
            repo_url=repo_url,
            branch=request.branch,
            cleanup=request.cleanup,
            request_id=request_id
        )

        logger.info("Created ingestion request: %s for repository: %s", request_id, repo_url)

        return IngestionResponse(
            request_id=request_id,
            repo_id=repo_id,
            status=IngestionStatus.PENDING,
            message="Ingestion request created and queued for processing"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error during ingestion request creation: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@router.get(
    "/ingest/{request_id}/status",
    response_model=IngestionStatusResponse,
    summary="Get ingestion status",
    description="Get the current status of an ingestion request by request_id."
)
async def get_ingestion_status(request_id: str) -> IngestionStatusResponse:
    """
    Get the current status of an ingestion request.

    Args:
        request_id: Ingestion request identifier

    Returns:
        IngestionStatusResponse with current status, progress, and metadata

    Raises:
        HTTPException: If request not found
    """
    try:
        ingestion_request = await mongodb_service.get_ingestion_request(request_id)

        if not ingestion_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ingestion request not found: {request_id}"
            )

        # Get the actual status - check if repository is completed even if request says pending
        request_status = ingestion_request.get("status")
        repo_id = ingestion_request.get("repo_id")

        # If status is pending/processing but repo exists, check if it's actually completed
        if request_status in ["pending", "processing"] and repo_id:
            # Check if repository exists
            repo = await mongodb_service.get_repository(repo_id)
            if repo:
                # Repository exists, so update this request to completed
                await mongodb_service.update_ingestion_request_status(
                    request_id=request_id,
                    status="completed",
                    message="Repository has already been ingested successfully"
                )
                # Refresh the request data
                ingestion_request = await mongodb_service.get_ingestion_request(request_id)
                if ingestion_request:
                    request_status = ingestion_request.get("status")

        return IngestionStatusResponse(
            request_id=ingestion_request.get("id"),
            repo_id=ingestion_request.get("repo_id"),
            status=request_status,
            error=ingestion_request.get("error"),
            retry_count=ingestion_request.get("retry_count", 0),
            retry_after=ingestion_request.get("retry_after"),
            message=ingestion_request.get("message"),
            created_at=ingestion_request.get("created_at"),
            started_at=ingestion_request.get("started_at"),
            completed_at=ingestion_request.get("completed_at")
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching ingestion status for %s: %s", request_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch ingestion status: {str(e)}"
        ) from e


@router.post(
    "/ingest/{request_id}/retry",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry failed ingestion",
    description="Retry a failed ingestion request. Only works if status is 'failed', not 'completed' or 'processing'."
)
async def retry_ingestion(
    request_id: str,
    background_tasks: BackgroundTasks
) -> IngestionResponse:
    """
    Retry a failed ingestion request.

    Args:
        request_id: Ingestion request identifier
        background_tasks: FastAPI background tasks for async processing

    Returns:
        IngestionResponse with updated request_id and status

    Raises:
        HTTPException: If request not found, not failed, or rate limit exceeded
    """
    try:
        # Get the ingestion request
        ingestion_request = await mongodb_service.get_ingestion_request(request_id)

        if not ingestion_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ingestion request not found: {request_id}"
            )

        current_status = ingestion_request.get("status")

        # Only allow retry if status is "failed"
        if current_status == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot retry a completed ingestion request"
            )

        if current_status == "processing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot retry an ingestion request that is currently processing"
            )

        if current_status != "failed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot retry ingestion request with status: {current_status}"
            )

        # Extract repository information for rate limiting check
        owner = ingestion_request.get("owner")
        repo_name = ingestion_request.get("repo_name")
        repo_url = ingestion_request.get("repository_url")
        branch = ingestion_request.get("branch")

        if not owner or not repo_name or not repo_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ingestion request missing required repository information"
            )

        # Check retry rate limit
        allowed, retry_count, retry_after = await mongodb_service.check_retry_rate_limit(
            owner=owner,
            repo_name=repo_name,
            time_window_hours=2,
            max_retries=3
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "message": f"Maximum 3 retries allowed within 2 hours. Current retry count: {retry_count}",
                    "retry_count": retry_count,
                    "retry_after": retry_after.isoformat() if retry_after else None
                }
            )

        # Increment retry count
        await mongodb_service.increment_retry_count(request_id)

        # Reset status to pending (error will be cleared automatically)
        await mongodb_service.update_ingestion_request_status(
            request_id=request_id,
            status="pending",
            message="Retrying ingestion..."
        )

        # Add background task for ingestion
        background_tasks.add_task(
            ingestion_service.ingest_repository,
            repo_url=repo_url,
            branch=branch,
            cleanup=True,  # Default cleanup
            request_id=request_id
        )

        logger.info("Retrying ingestion request: %s for repository: %s", request_id, repo_url)

        return IngestionResponse(
            request_id=request_id,
            repo_id=ingestion_request.get("repo_id"),
            status=IngestionStatus.PENDING,
            message="Ingestion retry initiated"
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error retrying ingestion for %s: %s", request_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retry ingestion: {str(e)}"
        ) from e
