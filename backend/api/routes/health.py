"""Liveness and deep dependency health checks."""

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter, Depends
from pymongo.errors import PyMongoError

from api.deps import get_vector_store
from config.settings import settings
from core.errors import AppError
from database.mongodb import mongodb
from services.vector_store.base import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_QDRANT_SKIPPED = {"status": "skipped", "reason": "not_configured"}


@router.get("/health", summary="Liveness")
async def health() -> Dict[str, str]:
    """Process is up. Does not touch Mongo, Gemini, or disk."""
    return {"status": "healthy", "service": "codesense-api"}


@router.get("/health/deep", summary="Dependency health")
async def health_deep(
    vector_store: VectorStore = Depends(get_vector_store),
) -> Dict[str, Any]:
    """Ping Mongo and Qdrant. Configured Qdrant down is 503; omitted URL is skipped."""
    checks: Dict[str, Any] = {
        "qdrant": (
            await _qdrant_check(vector_store)
            if settings.qdrant_url
            else dict(_QDRANT_SKIPPED)
        )
    }
    started = time.perf_counter()
    try:
        await mongodb.ping()
        checks["mongo"] = {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except PyMongoError as exc:
        checks["mongo"] = {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        logger.error("MongoDB ping failed: %s", exc)
        raise AppError(
            "MongoDB is unavailable",
            code="upstream_error",
            status_code=503,
            details={"checks": checks},
        ) from exc

    if checks["qdrant"].get("status") == "error":
        raise AppError(
            "Qdrant is unavailable",
            code="upstream_error",
            status_code=503,
            details={"checks": checks},
        )
    return {"status": "ok", "checks": checks}


async def _qdrant_check(store: VectorStore) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        await store.ping()
        return {
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:
        logger.error("Qdrant ping failed: %s", exc)
        return {
            "status": "error",
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
