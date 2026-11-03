"""Liveness and deep dependency health checks."""

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter
from pymongo.errors import PyMongoError

from core.errors import AppError
from database.mongodb import mongodb

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

_QDRANT_SKIPPED = {"status": "skipped", "reason": "not_configured"}


@router.get("/health", summary="Liveness")
async def health() -> Dict[str, str]:
    """Process is up. Does not touch Mongo, Gemini, or disk."""
    return {"status": "healthy", "service": "codesense-api"}


@router.get("/health/deep", summary="Dependency health")
async def health_deep() -> Dict[str, Any]:
    """Ping Mongo and report per-dependency status. Qdrant is skipped until it exists."""
    checks: Dict[str, Any] = {"qdrant": dict(_QDRANT_SKIPPED)}
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

    return {"status": "ok", "checks": checks}
