"""Idempotent v2 field backfill. Does not embed or write Qdrant."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict

from pymongo import UpdateOne
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.embeddings.base import EMBEDDING_DIM
from services.embeddings.hashing import content_hash
from services.vector_store.ids import make_point_id

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2
_BATCH_SIZE = 500


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_backend_on_path() -> None:
    root = str(_backend_root())
    if root not in sys.path:
        sys.path.insert(0, root)


def repo_patch(
    doc: Dict[str, Any],
    *,
    embedding_model: str,
    embedding_dim: int,
) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    if not doc.get("branch"):
        patch["branch"] = "main"
    if not doc.get("visibility"):
        patch["visibility"] = "public"
    if "owner_user_id" not in doc:
        patch["owner_user_id"] = None
    if not doc.get("embedding_model"):
        patch["embedding_model"] = embedding_model
    if not doc.get("embedding_dim"):
        patch["embedding_dim"] = embedding_dim
    if doc.get("schema_version") != SCHEMA_VERSION:
        patch["schema_version"] = SCHEMA_VERSION
    return patch


def chunk_patch(doc: Dict[str, Any]) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    if not doc.get("content_hash"):
        patch["content_hash"] = content_hash(doc.get("code_text") or "")
    if not doc.get("point_id") and doc.get("chunk_id") is not None:
        patch["point_id"] = make_point_id(str(doc.get("repo_id", "")), doc["chunk_id"])
    if "embedded_at" not in doc:
        patch["embedded_at"] = None
    if doc.get("schema_version") != SCHEMA_VERSION:
        patch["schema_version"] = SCHEMA_VERSION
    return patch


def session_patch(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Fill schema_version only. Do not invent user_id for legacy sessions."""
    patch: Dict[str, Any] = {}
    if doc.get("schema_version") != SCHEMA_VERSION:
        patch["schema_version"] = SCHEMA_VERSION
    return patch


def ingestion_patch(doc: Dict[str, Any]) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    if not doc.get("type"):
        patch["type"] = "full"
    if not doc.get("trigger"):
        patch["trigger"] = "user"
    return patch


async def _apply_patches(
    collection: AsyncCollection,
    patch_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> int:
    operations: list[UpdateOne] = []
    updated = 0
    cursor = collection.find({})
    async for doc in cursor:
        patch = patch_fn(doc)
        if not patch:
            continue
        operations.append(UpdateOne({"_id": doc["_id"]}, {"$set": patch}))
        if len(operations) >= _BATCH_SIZE:
            result = await collection.bulk_write(operations, ordered=False)
            updated += result.modified_count
            operations = []
    if operations:
        result = await collection.bulk_write(operations, ordered=False)
        updated += result.modified_count
    return updated


async def backfill_v2(db, *, embedding_model: str, embedding_dim: int) -> Dict[str, int]:
    counts = {
        "csRepositories": await _apply_patches(
            db["csRepositories"],
            lambda doc: repo_patch(
                doc, embedding_model=embedding_model, embedding_dim=embedding_dim
            ),
        ),
        "csChunks": await _apply_patches(db["csChunks"], chunk_patch),
        "csChatSessions": await _apply_patches(db["csChatSessions"], session_patch),
        "csIngestionRequests": await _apply_patches(
            db["csIngestionRequests"], ingestion_patch
        ),
    }
    for name, count in counts.items():
        logger.info("Backfilled %s: %d document(s)", name, count)
    return counts


async def _async_main() -> int:
    from config.settings import settings
    from database.mongodb import mongodb

    try:
        counts = await backfill_v2(
            mongodb.db,
            embedding_model=settings.gemini_embedding_model,
            embedding_dim=EMBEDDING_DIM,
        )
    except PyMongoError as exc:
        logger.exception("MongoDB error during v2 backfill")
        print(f"MongoDB error: {exc}", file=sys.stderr)
        return 2

    print("v2 backfill complete: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def main() -> int:
    _ensure_backend_on_path()
    from core.logging import setup_logging

    setup_logging()
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
