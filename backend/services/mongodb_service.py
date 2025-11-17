"""MongoDB service for upserting repositories and chunks."""

import logging
from datetime import datetime
from typing import Dict, List

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError

from database.mongodb import mongodb
from schemas import ChunkSchema, RepositorySchema

logger = logging.getLogger(__name__)


class MongoDBService:
    """Service for MongoDB operations on repositories and chunks."""

    def __init__(self):
        """Initialize MongoDB service with collections."""
        self.repositories = mongodb.db["csRepositories"]
        self.chunks = mongodb.db["csChunks"]

    async def upsert_repository(self, repo_data: Dict) -> bool:
        """
        Upsert repository document.

        Args:
            repo_data: Repository data dictionary

        Returns:
            True if successful, False otherwise
        """
        try:
            # Validate with schema
            repo_schema = RepositorySchema(**repo_data)
            repo_dict = repo_schema.model_dump()

            # Prepare update document
            repo_id = repo_dict["repo_id"]
            update_doc = {
                "$set": {
                    **{k: v for k, v in repo_dict.items() if k != "created_at"},
                    "updated_at": datetime.now()
                },
                "$setOnInsert": {
                    "created_at": repo_dict.get("created_at", datetime.utcnow())
                }
            }

            # Upsert repository
            result = await self.repositories.update_one(
                {"repo_id": repo_id},
                update_doc,
                upsert=True
            )

            logger.info(
                "Upserted repository: %s (matched: %d, modified: %d, upserted: %s)",
                repo_id,
                result.matched_count,
                result.modified_count,
                result.upserted_id is not None
            )

            return True

        except Exception as e:
            logger.error("Failed to upsert repository: %s", e, exc_info=True)
            return False

    async def upsert_chunks(self, chunks_data: List[Dict]) -> bool:
        """
        Upsert chunk documents in bulk.

        Args:
            chunks_data: List of chunk data dictionaries

        Returns:
            True if successful, False otherwise
        """
        if not chunks_data:
            logger.warning("No chunks data provided for upsert")
            return True

        try:
            operations: List[UpdateOne] = []
            for chunk_data in chunks_data:
                try:
                    chunk_schema = ChunkSchema(**chunk_data)
                    chunk_dict = chunk_schema.model_dump()

                    operations.append(
                        UpdateOne(
                            {"chunk_id": chunk_dict["chunk_id"]},
                            {"$set": chunk_dict},
                            upsert=True
                        )
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to validate chunk %s: %s",
                        chunk_data.get("chunk_id", "unknown"),
                        e
                    )
                    continue

            if not operations:
                logger.error("No valid chunks to upsert")
                return False

            # Bulk write
            result = await self.chunks.bulk_write(operations, ordered=False)

            logger.info(
                "Upserted chunks: %d matched, %d modified, %d upserted",
                result.matched_count,
                result.modified_count,
                result.upserted_count
            )

            return True

        except BulkWriteError as e:
            logger.error(
                "Bulk write error: %d errors out of %d operations",
                len(e.details.get("writeErrors", [])),
                len(chunks_data)
            )
            # Still return True if some operations succeeded
            return e.details.get("nInserted", 0) + e.details.get("nModified", 0) > 0

        except Exception as e:
            logger.error("Failed to upsert chunks: %s", e, exc_info=True)
            return False

    async def delete_chunks_by_repo(self, repo_id: str) -> bool:
        """
        Delete all chunks for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            result = await self.chunks.delete_many({"repo_id": repo_id})
            logger.info(
                "Deleted %d chunks for repository: %s",
                result.deleted_count,
                repo_id
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to delete chunks for repository %s: %s",
                repo_id,
                e
            )
            return False

