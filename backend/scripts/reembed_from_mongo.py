"""Embed Mongo chunks with embedded_at=null. No clone. Resume-safe."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

from pymongo.errors import PyMongoError

from services.ingestion_service import index_chunks

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_backend_on_path() -> None:
    root = str(_backend_root())
    if root not in sys.path:
        sys.path.insert(0, root)


async def reembed_repo(
    repo_id: str,
    *,
    embedder,
    vector_store,
    mongodb,
) -> Dict[str, Any]:
    """Embed missing chunks for one repo. Pages until embedded_at is set; hash-reuses inside the repo."""
    repo = await mongodb.get_repository(repo_id)
    if not repo:
        raise ValueError(f"Repository not found: {repo_id}")

    total_embedded = 0
    while True:
        missing: List[Dict] = await mongodb.list_chunks_missing_embeddings(
            repo_id, limit=_PAGE_SIZE
        )
        if not missing:
            break
        hash_to_point = await mongodb.list_embedded_hash_index(repo_id)
        filled, embedded_count = await index_chunks(
            missing,
            repo_id=str(repo_id),
            embedder=embedder,
            vector_store=vector_store,
            hash_to_point=hash_to_point,
            commit_hash=repo.get("commit_hash") or "",
        )
        if embedded_count != len(missing):
            raise RuntimeError(
                f"Embedded {embedded_count} of {len(missing)} chunks for {repo_id}"
            )
        if not await mongodb.mark_chunks_embedded(filled):
            raise RuntimeError(f"Failed to persist embedded_at for {repo_id}")
        total_embedded += embedded_count

    total = await mongodb.recount_embedded_chunks(repo_id)
    return {
        "embedded": total_embedded,
        "chunks_with_embeddings": total,
    }


async def _async_main(repo_id: str) -> int:
    from config.settings import settings
    from services.embeddings.gemini import GeminiEmbedder
    from services.mongodb_service import MongoDBService
    from services.vector_store.qdrant import QdrantVectorStore

    if not settings.qdrant_url:
        print("QDRANT_URL is required", file=sys.stderr)
        return 2
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is required", file=sys.stderr)
        return 2

    store = None
    result = None
    try:
        store = QdrantVectorStore()
        await store.ensure_collection()
        result = await reembed_repo(
            repo_id,
            embedder=GeminiEmbedder(),
            vector_store=store,
            mongodb=MongoDBService(),
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (PyMongoError, RuntimeError) as exc:
        logger.exception("Reembed failed")
        print(f"Reembed error: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            await store.close()

    if result is None:
        return 2
    print(
        f"reembed {repo_id}: embedded={result['embedded']} "
        f"chunks_with_embeddings={result['chunks_with_embeddings']}"
    )
    return 0


def main(argv: List[str] | None = None) -> int:
    _ensure_backend_on_path()
    from core.logging import setup_logging

    setup_logging()
    parser = argparse.ArgumentParser(description="Re-embed Mongo chunks missing vectors")
    parser.add_argument("repo_id", help="Mongo ObjectId of the repository")
    args = parser.parse_args(argv)
    return asyncio.run(_async_main(args.repo_id))


if __name__ == "__main__":
    raise SystemExit(main())
