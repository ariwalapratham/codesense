"""Ingestion API routes for repository processing."""

import logging
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException, status

from models.schemas import IngestionRequest, IngestionResponse
from services.ingestion_service import IngestionService

# Setup logger
logger = logging.getLogger(__name__)

# Create router
router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"]
)

# Lazy-loaded ingestion service instance
_ingestion_service: Optional[IngestionService] = None


def get_ingestion_service() -> IngestionService:
    """
    Get or create the ingestion service instance (lazy initialization).

    Returns:
        IngestionService instance
    """
    global _ingestion_service
    
    if _ingestion_service is None:
        _ingestion_service = IngestionService()
        logger.info("Ingestion service initialized")
    
    return _ingestion_service


@router.post(
    "/ingest",
    response_model=IngestionResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest a repository",
    description="Clone, parse, chunk, embed, and store a repository for code search and analysis."
)
async def ingest_repository(request: IngestionRequest) -> IngestionResponse:
    """
    Ingest a repository through the complete pipeline.

    Pipeline steps:
    1. Clone repository to temporary directory
    2. Discover and parse code files
    3. Chunk code into semantic pieces
    4. Generate embeddings
    5. Store in vector database and metadata store
    6. Cleanup temporary repository

    Args:
        request: Ingestion request with repository URL and options

    Returns:
        IngestionResponse with success status and metadata

    Raises:
        HTTPException: If ingestion fails
    """
    try:
        logger.info("Received ingestion request for repository: %s", request.repo_url)
        
        service = get_ingestion_service()
        
        # Convert HttpUrl to string
        repo_url = str(request.repo_url)
        
        # Call ingestion service
        success, message, metadata = await service.ingest_repository(
            repo_url=repo_url,
            branch=request.branch,
            cleanup=request.cleanup
        )
        
        if not success:
            logger.warning("Ingestion failed: %s", message)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=message
            )
        
        logger.info("Ingestion completed successfully for repository: %s", repo_url)
        
        return IngestionResponse(
            success=success,
            message=message,
            metadata=metadata
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Unexpected error during ingestion: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        ) from e


@router.get(
    "/health",
    summary="Health check for ingestion service",
    description="Check if the ingestion service is available and ready."
)
async def health_check() -> Dict[str, str]:
    """
    Health check endpoint for ingestion service.

    Returns:
        Health status
    """
    try:
        service = get_ingestion_service()
        return {
            "status": "healthy",
            "service": "ingestion",
            "message": "Ingestion service is ready"
        }
    except HTTPException:
        return {
            "status": "unhealthy",
            "service": "ingestion",
            "message": "Ingestion service is not initialized"
        }
