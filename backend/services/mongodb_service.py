"""MongoDB service for upserting repositories and chunks."""

import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

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

    async def get_repository(self, repo_id: str) -> Optional[Dict]:
        """
        Get a single repository by ID.

        Args:
            repo_id: Repository identifier

        Returns:
            Repository data dictionary or None if not found
        """
        try:
            repo_doc = await self.repositories.find_one({"repo_id": repo_id})
            
            if not repo_doc:
                logger.debug("Repository not found: %s", repo_id)
                return None
            
            # Remove MongoDB _id field
            repo_doc.pop("_id", None)
            
            # Validate with schema
            try:
                repo_schema = RepositorySchema(**repo_doc)
                return repo_schema.model_dump()
            except Exception as e:
                logger.warning(
                    "Repository document validation failed for %s: %s",
                    repo_id,
                    e
                )
                # Return raw document if validation fails
                return repo_doc

        except PyMongoError as e:
            logger.error(
                "MongoDB error fetching repository %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return None
        except Exception as e:
            logger.error(
                "Unexpected error fetching repository %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return None

    async def list_repositories(
        self,
        skip: int = 0,
        limit: int = 10,
        search: Optional[str] = None
    ) -> Tuple[List[Dict], int]:
        """
        List repositories with pagination and optional search.

        Args:
            skip: Number of repositories to skip
            limit: Maximum number of repositories to return
            search: Optional search query to filter by repo_name, owner, or repository_url

        Returns:
            Tuple of (list of repository dictionaries, total count)
        """
        try:
            # Build search filter
            search_filter = {}
            if search:
                # Case-insensitive regex search across repo_name, owner, and repository_url
                search_regex = {"$regex": search, "$options": "i"}
                search_filter = {
                    "$or": [
                        {"repo_name": search_regex},
                        {"owner": search_regex},
                        {"repository_url": search_regex}
                    ]
                }
            
            # Get total count with search filter
            total = await self.repositories.count_documents(search_filter)
            
            # Fetch repositories with pagination and search, sorted by updated_at descending
            cursor = (
                self.repositories.find(search_filter)
                .sort("updated_at", -1)
                .skip(skip)
                .limit(limit)
            )
            repo_docs = await cursor.to_list(length=limit)
            
            # Process and validate repositories
            repositories = []
            for repo_doc in repo_docs:
                # Remove MongoDB _id field
                repo_doc.pop("_id", None)
                
                # Validate with schema
                try:
                    repo_schema = RepositorySchema(**repo_doc)
                    repositories.append(repo_schema.model_dump())
                except Exception as e:
                    logger.warning(
                        "Repository document validation failed: %s",
                        e
                    )
                    # Include raw document if validation fails
                    repositories.append(repo_doc)
            
            logger.debug(
                "Listed %d repositories (skip: %d, limit: %d, total: %d, search: %s)",
                len(repositories),
                skip,
                limit,
                total,
                search or "none"
            )
            
            return repositories, total

        except PyMongoError as e:
            logger.error(
                "MongoDB error listing repositories: %s",
                e,
                exc_info=True
            )
            return [], 0
        except Exception as e:
            logger.error(
                "Unexpected error listing repositories: %s",
                e,
                exc_info=True
            )
            return [], 0

    async def get_repository_stats(self, repo_id: str) -> Optional[Dict]:
        """
        Get repository statistics using aggregation pipeline.

        Args:
            repo_id: Repository identifier

        Returns:
            Statistics dictionary or None if repository not found
        """
        try:
            # First, verify repository exists and get basic info
            repo = await self.get_repository(repo_id)
            if not repo:
                logger.debug("Repository not found for stats: %s", repo_id)
                return None
            
            # Get total chunks count
            total_chunks = await self.chunks.count_documents({"repo_id": repo_id})
            
            # Get language distribution using find and manual counting
            # (more reliable than aggregate for async PyMongo)
            language_distribution = {}
            async for chunk in self.chunks.find({"repo_id": repo_id}, {"language": 1}):
                language = chunk.get("language", "unknown")
                language_distribution[language] = language_distribution.get(language, 0) + 1
            
            # Get chunk type distribution from metadata.type
            chunk_type_distribution = {}
            async for chunk in self.chunks.find({"repo_id": repo_id}, {"metadata.type": 1}):
                chunk_type = chunk.get("metadata", {}).get("type", "unknown")
                chunk_type_distribution[chunk_type] = chunk_type_distribution.get(chunk_type, 0) + 1
            
            # Get unique file count using distinct
            unique_files = await self.chunks.distinct("file_path", {"repo_id": repo_id})
            total_files = len(unique_files)
            
            stats = {
                "repo_id": repo_id,
                "repo_name": repo.get("repo_name", ""),
                "total_chunks": total_chunks,
                "total_files": total_files,
                "language_distribution": language_distribution,
                "chunk_type_distribution": chunk_type_distribution
            }
            
            logger.debug(
                "Retrieved stats for repository %s: %d chunks, %d files",
                repo_id,
                total_chunks,
                total_files
            )
            
            return stats

        except PyMongoError as e:
            logger.error(
                "MongoDB error fetching repository stats for %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return None
        except Exception as e:
            logger.error(
                "Unexpected error fetching repository stats for %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return None

