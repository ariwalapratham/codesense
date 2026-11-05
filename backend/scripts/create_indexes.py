"""Create Mongo indexes after a duplicate check.

Unique indexes match today's upsert keys, not the v2 key that includes
branch and owner_user_id. The CLI duplicate-checks first. Startup only
applies indexes (no collection scan).
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure, PyMongoError

logger = logging.getLogger(__name__)

_MAX_DUPLICATE_GROUPS = 20

_DUPLICATE_CHECKS = (
    ("csChunks", ("chunk_id",)),
    ("csRepositories", ("owner", "repo_name")),
    ("csChatSessions", ("repo_id", "session_id")),
)

_NON_UNIQUE_INDEXES = {
    "csChunks": [
        IndexModel(
            [("repo_id", ASCENDING), ("file_path", ASCENDING)],
            name="idx_chunks_repo_id_file_path",
        ),
        IndexModel(
            [("repo_id", ASCENDING), ("content_hash", ASCENDING)],
            name="idx_chunks_repo_id_content_hash",
        ),
        IndexModel(
            [("repo_id", ASCENDING), ("function_name", ASCENDING)],
            name="idx_chunks_repo_id_function_name",
            sparse=True,
        ),
        IndexModel(
            [("repo_id", ASCENDING), ("class_name", ASCENDING)],
            name="idx_chunks_repo_id_class_name",
            sparse=True,
        ),
    ],
    "csChatSessions": [
        IndexModel(
            [("repo_id", ASCENDING), ("updated_at", ASCENDING)],
            name="idx_chat_sessions_repo_id_updated_at",
        ),
    ],
    "csIngestionRequests": [
        IndexModel(
            [("status", ASCENDING), ("created_at", ASCENDING)],
            name="idx_ingestion_requests_status_created_at",
        ),
        IndexModel(
            [
                ("owner", ASCENDING),
                ("repo_name", ASCENDING),
                ("branch", ASCENDING),
                ("status", ASCENDING),
            ],
            name="idx_ingestion_requests_owner_repo_branch_status",
        ),
    ],
}

_UNIQUE_INDEXES = {
    "csChunks": [
        IndexModel([("chunk_id", ASCENDING)], name="ux_chunks_chunk_id", unique=True),
    ],
    "csRepositories": [
        IndexModel(
            [("owner", ASCENDING), ("repo_name", ASCENDING)],
            name="ux_repositories_owner_repo_name",
            unique=True,
        ),
    ],
    "csChatSessions": [
        IndexModel(
            [("repo_id", ASCENDING), ("session_id", ASCENDING)],
            name="ux_chat_sessions_repo_id_session_id",
            unique=True,
        ),
    ],
}


@dataclass
class DuplicateGroup:
    key: Dict[str, Any]
    count: int


@dataclass
class DuplicateReport:
    collection: str
    fields: Sequence[str]
    groups: List[DuplicateGroup] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.groups)


def _backend_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ensure_backend_on_path() -> None:
    root = str(_backend_root())
    if root not in sys.path:
        sys.path.insert(0, root)


async def find_duplicate_groups(
    db: AsyncDatabase,
    collection_name: str,
    fields: Sequence[str],
    *,
    limit: int = _MAX_DUPLICATE_GROUPS,
) -> DuplicateReport:
    group_id = {field: f"${field}" for field in fields}
    pipeline = [
        {"$group": {"_id": group_id, "count": {"$sum": 1}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": limit},
    ]
    cursor = await db[collection_name].aggregate(pipeline)
    groups = [
        DuplicateGroup(key=doc.get("_id") or {}, count=int(doc.get("count", 0)))
        async for doc in cursor
    ]
    return DuplicateReport(collection=collection_name, fields=fields, groups=groups)


async def check_duplicates(db: AsyncDatabase) -> List[DuplicateReport]:
    reports = []
    for collection_name, fields in _DUPLICATE_CHECKS:
        reports.append(await find_duplicate_groups(db, collection_name, fields))
    return reports


def format_duplicate_reports(reports: Sequence[DuplicateReport]) -> str:
    lines = ["Duplicate check failed: unique indexes would error."]
    for report in reports:
        if not report.found:
            continue
        field_label = ", ".join(report.fields)
        lines.append(
            f"  {report.collection} ({field_label}): {len(report.groups)} group(s)"
        )
        for group in report.groups:
            lines.append(f"    {group.key} x{group.count}")
    lines.append("Fix duplicates by hand, then re-run this script.")
    return "\n".join(lines)


async def _create_collection_indexes(
    db: AsyncDatabase,
    collection_name: str,
    models: Sequence[IndexModel],
) -> None:
    if not models:
        return
    try:
        await db[collection_name].create_indexes(list(models))
    except OperationFailure:
        logger.exception("Failed creating indexes on %s", collection_name)
        raise


async def apply_indexes(db: AsyncDatabase, *, include_unique: bool) -> None:
    for collection_name, models in _NON_UNIQUE_INDEXES.items():
        await _create_collection_indexes(db, collection_name, models)
        logger.info("Ensured non-unique indexes on %s", collection_name)

    if not include_unique:
        logger.warning("Skipping unique indexes because duplicates were found")
        return

    for collection_name, models in _UNIQUE_INDEXES.items():
        await _create_collection_indexes(db, collection_name, models)
        logger.info("Ensured unique indexes on %s", collection_name)


async def ensure_indexes(
    db: Optional[AsyncDatabase] = None,
    *,
    fail_on_duplicates: bool = False,
) -> List[DuplicateReport]:
    """Duplicate-check, then create indexes. Used by CLI and app lifespan."""
    owns_client = db is None
    client: Optional[AsyncMongoClient] = None
    if db is None:
        from config.settings import settings

        client = AsyncMongoClient(
            settings.mongodb_url,
            serverSelectionTimeoutMS=5000,
            appName="CodeSenseIndexes",
        )
        db = client[settings.db_name]

    try:
        reports = await check_duplicates(db)
        duplicates = [report for report in reports if report.found]
        if duplicates:
            summary = format_duplicate_reports(duplicates)
            logger.error(summary)
            await apply_indexes(db, include_unique=False)
            if fail_on_duplicates:
                raise DuplicateIndexError(summary)
            return duplicates

        await apply_indexes(db, include_unique=True)
        logger.info("All indexes ensured")
        return []
    finally:
        if owns_client and client is not None:
            await client.close()


class DuplicateIndexError(RuntimeError):
    """Unique indexes were not created because duplicates exist."""


async def _async_main() -> int:
    from database.mongodb import mongodb

    try:
        await ensure_indexes(mongodb.db, fail_on_duplicates=True)
    except DuplicateIndexError as exc:
        print(exc, file=sys.stderr)
        print("Non-unique indexes were still created.", file=sys.stderr)
        return 1
    except PyMongoError as exc:
        logger.exception("MongoDB error while creating indexes")
        print(f"MongoDB error: {exc}", file=sys.stderr)
        return 2

    print("Indexes created (or already present). Duplicate check passed.")
    return 0


def main() -> int:
    _ensure_backend_on_path()
    from core.logging import setup_logging

    setup_logging()
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
