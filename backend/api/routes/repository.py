"""Repository API routes for repository management."""

import base64
import json
import logging
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from bson import ObjectId
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from api.deps import (
    get_ingestion_service,
    get_job_queue,
    get_mongodb_service,
    get_repository_service,
)
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
from services.jobs.queue import JobQueue
from services.mongodb_service import MongoDBService
from services.repository_service import RepositoryService

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/repository",
    tags=["repository"],
)


def _progress_from_doc(doc: Dict) -> Dict:
    """Build the status progress object, defaulting missing counters to zero."""
    progress = doc.get("progress") or {}
    return {
        "stage": progress.get("stage") or "queued",
        "files_total": progress.get("files_total") or 0,
        "files_done": progress.get("files_done") or 0,
        "chunks_total": progress.get("chunks_total") or 0,
        "chunks_embedded": progress.get("chunks_embedded") or 0,
    }


def _ingestion_status_response(
    doc: Dict,
    *,
    commit_hash: Optional[str] = None,
) -> IngestionStatusResponse:
    """Map a csIngestionRequests document to the status response."""
    repo_id = doc.get("repo_id")
    if repo_id:
        repo_id = str(repo_id)
    return IngestionStatusResponse(
        request_id=str(doc.get("id")),
        repo_id=repo_id,
        owner=doc.get("owner"),
        repo_name=doc.get("repo_name"),
        branch=doc.get("branch"),
        commit_hash=commit_hash,
        type=doc.get("type") or "full",
        status=doc.get("status"),
        error=doc.get("error"),
        retry_count=doc.get("retry_count", 0),
        retry_after=doc.get("retry_after"),
        message=doc.get("message") or "Ingestion in progress",
        progress=_progress_from_doc(doc),
        created_at=doc.get("created_at"),
        started_at=doc.get("started_at"),
        completed_at=doc.get("completed_at"),
    )


def encode_repo_identifier(owner: str, repo_name: str, branch: str) -> str:
    """
    Encode owner/repo_name/branch into a base64 string identifier.
    
    Args:
        owner: Repository owner
        repo_name: Repository name
        branch: Branch name
        
    Returns:
        Base64-encoded string identifier (URL-safe, no padding)
    """
    identifier_data = {
        "owner": owner,
        "repo_name": repo_name,
        "branch": branch or "main"
    }
    json_str = json.dumps(identifier_data, sort_keys=True)
    encoded = base64.urlsafe_b64encode(json_str.encode('utf-8')).decode('utf-8')
    # Remove padding for cleaner URLs
    return encoded.rstrip('=')


def decode_repo_identifier(encoded_id: str) -> Tuple[str, str, str]:
    """
    Decode base64 identifier back to owner/repo_name/branch.
    
    Args:
        encoded_id: Base64-encoded identifier
        
    Returns:
        Tuple of (owner, repo_name, branch)
        
    Raises:
        ValueError: If decoding fails
    """
    try:
        # Add padding if needed
        padding = 4 - len(encoded_id) % 4
        if padding != 4:
            encoded_id += '=' * padding
        
        decoded_bytes = base64.urlsafe_b64decode(encoded_id.encode('utf-8'))
        json_str = decoded_bytes.decode('utf-8')
        data = json.loads(json_str)
        
        return data["owner"], data["repo_name"], data.get("branch", "main")
    except Exception as e:
        raise ValueError(f"Invalid repository identifier: {e}") from e

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
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
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
async def get_repository(
    repo_id: str,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> RepositoryInfo:
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
async def get_repository_stats(
    repo_id: str,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> RepositoryStats:
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
async def delete_repository(
    repo_id: str,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> DeleteRepositoryResponse:
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
    description="Create an ingestion request and start processing asynchronously.",
)
async def ingest_repository(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
    repository_service: RepositoryService = Depends(get_repository_service),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
) -> IngestionResponse:
    """
    Create an ingestion request and start processing in the background.

    **Changed Behavior:**
    1. Check if repository already exists with same owner/repo/branch FIRST
    2. If exists AND same commit hash then return existing repo details
    3. If exists AND different commit hash then update existing repo (re-ingest)
    4. If not exists then create new

    **Status Codes:**
    - 202: New ingestion started or existing repo returned
    - 409: Currently processing (cannot start another)
    - 429: Rate limit exceeded
    - 400: Invalid request
    """
    try:
        logger.info("Received ingestion request for repository: %s", request.repo_url)

        repo_url = str(request.repo_url)

        # Extract owner and repository name
        try:
            owner, repo_name = _extract_repo_info(repo_url)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        branch = request.branch or "main" # default branch is main

        # Check if repository already exists with same owner, repo_name, and branch
        existing_repo = await mongodb_service.find_repository_by_identifier(
            owner=owner,
            repo_name=repo_name,
            branch=branch
        )

        # Get remote commit hash
        try:
            remote_commit_hash = await repository_service.get_remote_commit_hash(
                repo_url=repo_url,
                branch=branch
            )
        except Exception as e:
            logger.warning("Could not fetch remote commit hash: %s", e)
            remote_commit_hash = None

        # If repository already exists with same commit hash, return existing repository details
        if existing_repo and remote_commit_hash:
            existing_commit = existing_repo.get("commit_hash")

            if existing_commit == remote_commit_hash:
                # Exact same version already ingested
                repo_id = str(existing_repo.get("_id"))

                logger.info(
                    "Repository %s/%s (%s) already exists with commit %s",
                    owner, repo_name, branch, remote_commit_hash
                )

                # Generate encoded identifier
                encoded_id = encode_repo_identifier(owner, repo_name, branch)

                return IngestionResponse(
                    request_id=None,  # No new request created
                    encoded_id=encoded_id,
                    repo_id=repo_id,
                    owner=owner,
                    repo_name=repo_name,
                    branch=branch,
                    commit_hash=remote_commit_hash,
                    status=IngestionStatus.COMPLETED,
                    message=f"Repository already ingested with commit {remote_commit_hash[:7]}"
                )



        # Check for active ingestion on the same (owner, repo, branch)
        active_request = await mongodb_service.get_active_ingestion_request(
            owner=owner,
            repo_name=repo_name,
            branch=branch,
        )

        if active_request:
            active_status = active_request.get("status")
            active_request_id = active_request.get("id")

            # Block if actively processing OR pending (to prevent duplicate tasks)
            if active_status in [IngestionStatus.PROCESSING, IngestionStatus.PENDING]:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "Repository ingestion already in progress",
                        "request_id": active_request_id,
                        "repo_id": active_request.get("repo_id"),
                        "status": active_status,
                        "message": (
                            f"Repository {owner}/{repo_name} ({branch}) "
                            f"is already being processed (status: {active_status}). "
                            f"Use request_id {active_request_id} to check status."
                        )
                    }
                )

            # Only reuse if status is FAILED (allow retry)
            if active_status == IngestionStatus.FAILED:
                request_id = active_request_id
                repo_id = active_request.get("repo_id")
                logger.info(
                    "Reusing failed ingestion request %s for retry",
                    request_id
                )
            else:
                # Should not reach here, but handle gracefully
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "Unexpected active request status",
                        "request_id": active_request_id,
                        "status": active_status
                    }
            )
        else:
            # Rate limit check (only for new requests)
            allowed, retry_count, retry_after = await mongodb_service.check_retry_rate_limit(
                owner=owner,
                repo_name=repo_name,
                branch=branch,
                time_window_hours=2,
                max_retries=3,
            )

            if not allowed:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "Rate limit exceeded",
                        "message": (
                            "Maximum 3 ingestion attempts allowed within 2 hours. "
                            f"Current attempt count: {retry_count}"
                        ),
                        "retry_count": retry_count,
                        "retry_after": retry_after.isoformat() if retry_after else None,
                    },
                )

            # Create new ingestion request
            request_data = {
                "owner": owner,
                "repo_name": repo_name,
                "repository_url": repo_url,
                "branch": branch,
                "status": IngestionStatus.PENDING.value,
                "target_commit": remote_commit_hash,
                "cleanup": request.cleanup,
                "type": "full",
                "trigger": "user",
                "progress": {
                    "stage": "queued",
                    "files_total": 0,
                    "files_done": 0,
                    "chunks_total": 0,
                    "chunks_embedded": 0,
                },
            }

            request_object_id = await mongodb_service.create_ingestion_request(request_data)
            if not request_object_id:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to create ingestion request",
                )

            request_id = str(request_object_id)
            repo_id = str(existing_repo.get("_id")) if existing_repo else None

        # Add background task
        background_tasks.add_task(
            ingestion_service.ingest_repository,
            repo_url=repo_url,
            branch=branch,
            cleanup=request.cleanup,
            request_id=request_id,
        )

        logger.info(
            "Created/reused ingestion request %s for repository %s/%s (%s)",
            request_id, owner, repo_name, branch,
        )

        # Generate encoded identifier for UI (can be regenerated from owner/repo_name/branch)
        encoded_id = encode_repo_identifier(owner, repo_name, branch)

        return IngestionResponse(
            request_id=request_id,
            encoded_id=encoded_id,
            repo_id=repo_id,
            owner=owner,
            repo_name=repo_name,
            branch=branch,
            commit_hash=remote_commit_hash,
            status=IngestionStatus.PENDING,
            message=(
                "Ingestion request created and queued"
                if not active_request
                else "Retrying ingestion"
            )
        )

    except HTTPException:
        raise

    except Exception as exc:
        logger.error(
            "Unexpected error during ingestion request creation: %s",
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error during ingestion request creation",
        ) from exc

@router.get(
    "/ingest/status/{identifier}",
    response_model=IngestionStatusResponse,
    summary="Get ingestion status",
    description="Get ingestion status by request_id (MongoDB ObjectId) or encoded repository identifier (base64).",
)
async def get_ingestion_status(
    identifier: str,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
) -> IngestionStatusResponse:
    """
    Get ingestion status by identifier.

    Accepts either:
    - MongoDB ObjectId (from ingest response request_id)
    - Base64-encoded repository identifier (owner/repo_name/branch)
    
    The encoded_id is returned from POST /repository/ingest and can be
    regenerated from owner/repo_name/branch, making it perfect for page reloads.

    Args:
        identifier: Either MongoDB ObjectId or base64-encoded repo identifier

    Returns:
        IngestionStatusResponse with current status

    Raises:
        HTTPException: If request not found
    """
    try:
        ingestion_request = None
        
        # Try as MongoDB ObjectId first (backward compatibility)
        if ObjectId.is_valid(identifier):
            ingestion_request = await mongodb_service.get_ingestion_request(identifier)
        
        # If not found, try as encoded identifier
        if not ingestion_request:
            try:
                owner, repo_name, branch = decode_repo_identifier(identifier)
                ingestion_request = await mongodb_service.get_ingestion_request_by_identifier(
                    owner=owner,
                    repo_name=repo_name,
                    branch=branch
                )
            except ValueError as e:
                logger.debug("Failed to decode identifier as base64: %s", e)
                # Continue to 404 below

        if not ingestion_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ingestion request not found: {identifier}"
            )

        repo_id = ingestion_request.get("repo_id")
        commit_hash = None
        if repo_id:
            repo = await mongodb_service.get_repository(str(repo_id))
            if repo:
                commit_hash = repo.get("commit_hash")

        return _ingestion_status_response(ingestion_request, commit_hash=commit_hash)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Error fetching ingestion status for identifier %s: %s",
            identifier, e, exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch ingestion status: {str(e)}"
        ) from e

@router.post(
    "/ingest/{request_id}/retry",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry failed ingestion",
    description="Retry a failed ingestion request. Only works if status is 'failed', not 'completed' or 'processing'.",
)
async def retry_ingestion(
    request_id: str,
    background_tasks: BackgroundTasks,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
    ingestion_service: IngestionService = Depends(get_ingestion_service),
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
        # Use explicit string comparison since MongoDB returns strings
        if current_status == IngestionStatus.COMPLETED.value or current_status == "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot retry a completed ingestion request"
            )

        if current_status == IngestionStatus.PROCESSING.value or current_status == "processing":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot retry an ingestion request that is currently processing"
            )

        if current_status == IngestionStatus.PENDING.value or current_status == "pending":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot retry an ingestion request that is pending. It will be processed automatically."
            )

        if current_status != IngestionStatus.FAILED.value and current_status != "failed":
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
            branch=branch,
            time_window_hours=2,
            max_retries=3
        )

        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={
                    "error": "Rate limit exceeded",
                    "message": (
                        f"Maximum 3 retries allowed within 2 hours. "
                        f"Current retry count: {retry_count}"
                    ),
                    "retry_count": retry_count,
                    "retry_after": retry_after.isoformat() if retry_after else None
                }
            )

        # Increment retry count
        await mongodb_service.increment_retry_count(request_id)

        # Reset status to pending and clear error
        await mongodb_service.update_ingestion_request_status(
            request_id=request_id,
            status=IngestionStatus.PENDING.value,
            error=None,  # Clear previous error
            message="Retrying ingestion..."
        )

        cleanup = ingestion_request.get("cleanup")
        if cleanup is None:
            cleanup = True

        # Add background task for ingestion
        background_tasks.add_task(
            ingestion_service.ingest_repository,
            repo_url=repo_url,
            branch=branch,
            cleanup=cleanup,
            request_id=request_id
        )

        logger.info("Retrying ingestion request: %s for repository: %s", request_id, repo_url)

        # Get repo_id if available
        repo_id = ingestion_request.get("repo_id")
        if repo_id:
            repo_id = str(repo_id)

        # Get commit hash if repo exists
        commit_hash = None
        if repo_id:
            repo = await mongodb_service.get_repository(repo_id)
            if repo:
                commit_hash = repo.get("commit_hash")

        # Generate encoded identifier
        encoded_id = encode_repo_identifier(owner, repo_name, branch or "main")

        return IngestionResponse(
            request_id=request_id,
            encoded_id=encoded_id,
            repo_id=repo_id,
            owner=owner,
            repo_name=repo_name,
            branch=branch or "main",
            commit_hash=commit_hash,
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


@router.post(
    "/ingest/{request_id}/cancel",
    response_model=IngestionStatusResponse,
    summary="Cancel a pending ingestion",
    description=(
        "Cancel a pending ingestion job. Processing jobs cannot be cancelled "
        "until the in-process worker is running."
    ),
)
async def cancel_ingestion(
    request_id: str,
    mongodb_service: MongoDBService = Depends(get_mongodb_service),
    job_queue: JobQueue = Depends(get_job_queue),
) -> IngestionStatusResponse:
    """Cancel a pending ingestion request."""
    try:
        ingestion_request = await mongodb_service.get_ingestion_request(request_id)
        if not ingestion_request:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Ingestion request not found: {request_id}",
            )

        current_status = ingestion_request.get("status")
        if current_status == IngestionStatus.PENDING.value or current_status == "pending":
            cancelled = await job_queue.cancel_pending(request_id)
            if not cancelled:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "conflict",
                        "message": "Ingestion request is no longer pending",
                        "request_id": request_id,
                    },
                )
            return _ingestion_status_response(cancelled)

        if current_status == IngestionStatus.PROCESSING.value or current_status == "processing":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "conflict",
                    "message": "Cannot cancel a job that is currently processing",
                    "request_id": request_id,
                    "status": current_status,
                },
            )

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "conflict",
                "message": f"Cannot cancel a job with status: {current_status}",
                "request_id": request_id,
                "status": current_status,
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error cancelling ingestion %s: %s", request_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel ingestion: {str(e)}",
        ) from e
