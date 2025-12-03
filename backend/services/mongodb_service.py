"""MongoDB service for upserting repositories and chunks."""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union

from bson import ObjectId

from pymongo import UpdateOne
from pymongo.errors import BulkWriteError, PyMongoError

from database.mongodb import mongodb
from schemas import (
    ChatSessionSchema,
    ChunkSchema,
    RepositorySchema,
    ChatMessageDocument,
    IngestionRequestSchema,
)

# pylint: disable=broad-exception-caught

logger = logging.getLogger(__name__)


class MongoDBService:
    """Service for MongoDB operations on repositories and chunks."""

    def __init__(self):
        """Initialize MongoDB service with collections."""
        self.repositories = mongodb.db["csRepositories"]
        self.chunks = mongodb.db["csChunks"]
        self.chat_sessions = mongodb.db["csChatSessions"]
        self.ingestion_requests = mongodb.db["csIngestionRequests"]

    @staticmethod
    def _to_object_id(value: Union[str, ObjectId]) -> ObjectId:
        """
        Convert a string or ObjectId-like value into an ObjectId.

        Args:
            value: The value to convert.

        Returns:
            ObjectId instance.

        Raises:
            ValueError: If the value cannot be converted.
        """
        if isinstance(value, ObjectId):
            return value
        if isinstance(value, str) and ObjectId.is_valid(value):
            return ObjectId(value)
        raise ValueError(f"Invalid ObjectId value: {value}")

    async def upsert_repository(self, repo_data: Dict) -> Optional[ObjectId]:
        """
        Upsert repository document.

        Args:
            repo_data: Repository data dictionary

        Returns:
            Repository ObjectId if successful, None otherwise
        """
        try:
            owner = repo_data.get("owner")
            repo_name = repo_data.get("repo_name")
            if not owner or not repo_name:
                raise ValueError("Repository owner and repo_name are required for upsert")

            repo_schema = RepositorySchema(**repo_data)
            repo_dict = repo_schema.model_dump()

            update_doc = {
                "$set": {
                    **{k: v for k, v in repo_dict.items() if k != "created_at"},
                    "updated_at": datetime.now()
                },
                "$setOnInsert": {
                    "created_at": repo_dict.get("created_at", datetime.now())
                }
            }

            result = await self.repositories.update_one(
                {"owner": owner, "repo_name": repo_name},
                update_doc,
                upsert=True
            )

            # Get the repository _id (either from upserted_id or by querying)
            if result.upserted_id:
                repo_object_id = result.upserted_id
            else:
                # If it was an update, fetch the _id
                repo_doc = await self.repositories.find_one(
                    {"owner": owner, "repo_name": repo_name},
                    {"_id": 1}
                )
                repo_object_id = repo_doc["_id"] if repo_doc else None

            logger.info(
                "Upserted repository: %s (matched: %d, modified: %d, upserted: %s, _id: %s)",
                f"{owner}/{repo_name}",
                result.matched_count,
                result.modified_count,
                result.upserted_id is not None,
                str(repo_object_id) if repo_object_id else "None"
            )

            return repo_object_id


        except Exception as e:
            logger.error("Failed to upsert repository: %s", e, exc_info=True)
            return None

    async def update_repository_status(
        self, owner: str, repo_name: str, status: str
    ) -> bool:
        """
        Update repository status.

        Args:
            owner: Repository owner
            repo_name: Repository name
            status: New status

        Returns:
            True if successful, False otherwise
        """
        try:
            result = await self.repositories.update_one(
                {"owner": owner, "repo_name": repo_name},
                {
                    "$set": {
                        "status": status,
                        "updated_at": datetime.now()
                    }
                }
            )
            return result.matched_count > 0
        except Exception as e:
            logger.error("Failed to update repository status: %s", e, exc_info=True)
            return False

    async def list_chat_sessions(
        self, repo_id: str, skip: int = 0, limit: int = 10
    ) -> Tuple[List[Dict], int]:
        """
        List chat sessions for a repository with pagination.

        Args:
            repo_id: Repository identifier
            skip: Number of sessions to skip
            limit: Maximum number of sessions to return

        Returns:
            Tuple of (list of session dictionaries, total count)
        """
        try:
            repo_object_id = self._to_object_id(repo_id)

            # Get total count
            total = await self.chat_sessions.count_documents({"repo_id": repo_object_id})

            # Fetch sessions with pagination
            cursor = (
                self.chat_sessions.find(
                    {"repo_id": repo_object_id},
                    {
                        "_id": 0,
                        "session_id": 1,
                        "repo_id": 1,
                        "title": 1,
                        "created_at": 1,
                        "updated_at": 1,
                        "messages": {"$slice": -1},
                    },
                )
                .sort("updated_at", -1)
                .skip(skip)
                .limit(limit)
            )
            sessions = await cursor.to_list(length=limit)

            for session in sessions:
                session["repo_id"] = repo_id

            return sessions, total

        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to list chat sessions for repo %s: %s", repo_id, e, exc_info=True)
            return [], 0

    async def create_chat_session(
        self,
        repo_id: str,
        title: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict]:
        """Create a new chat session document."""
        try:
            repo_object_id = self._to_object_id(repo_id)
            session_id = str(int(datetime.now().timestamp() * 1000))

            session_schema = ChatSessionSchema(
                session_id=session_id,
                repo_id=repo_object_id,
                user_id=ObjectId(user_id) if user_id else None,
                title=title,
            )
            session_doc = session_schema.model_dump()
            await self.chat_sessions.insert_one(session_doc)
            session_doc["repo_id"] = repo_id
            return session_doc

        except Exception as e:  # pylint: disable=broad-except
            logger.error("Failed to create chat session for repo %s: %s", repo_id, e, exc_info=True)
            return None

    async def update_chat_session(
        self,
        repo_id: str,
        session_id: str,
        updates: Dict,
    ) -> bool:
        """Update chat session metadata."""
        try:
            repo_object_id = self._to_object_id(repo_id)
            updates["updated_at"] = datetime.now()
            result = await self.chat_sessions.update_one(
                {"repo_id": repo_object_id, "session_id": session_id},
                {"$set": updates},
            )
            return result.matched_count > 0
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to update chat session %s for repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return False

    async def delete_chat_session(self, repo_id: str, session_id: str) -> bool:
        """Delete a chat session and its messages."""
        try:
            repo_object_id = self._to_object_id(repo_id)
            result = await self.chat_sessions.delete_one(
                {"repo_id": repo_object_id, "session_id": session_id}
            )
            return result.deleted_count > 0
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to delete chat session %s for repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return False

    async def get_chat_session(self, repo_id: str, session_id: str) -> Optional[Dict]:
        """
        Fetch a chat session document by repository and session identifiers.
        """
        try:
            repo_object_id = self._to_object_id(repo_id)
            session_doc = await self.chat_sessions.find_one(
                {"repo_id": repo_object_id, "session_id": session_id}
            )
            if session_doc:
                session_doc.pop("_id", None)
                session_doc["repo_id"] = repo_id
            return session_doc
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to fetch chat session %s for repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return None

    async def append_chat_message(
        self,
        repo_id: str,
        session_id: str,
        message: Dict,
    ) -> bool:
        """
        Append a chat message to a session.
        """
        try:
            repo_object_id = self._to_object_id(repo_id)
            message_doc = ChatMessageDocument(**message).model_dump()
            result = await self.chat_sessions.update_one(
                {"repo_id": repo_object_id, "session_id": session_id},
                {
                    "$push": {"messages": message_doc},
                    "$set": {"updated_at": datetime.now()},
                },
            )
            if result.matched_count == 0:
                logger.warning(
                    "Chat session not found when appending message (repo=%s, session=%s)",
                    repo_id,
                    session_id,
                )
            return result.modified_count > 0
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to append chat message for session %s in repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return False

    async def fetch_recent_chat_messages(
        self,
        repo_id: str,
        session_id: str,
        limit: int = 10,
    ) -> List[Dict]:
        """
        Fetch the most recent chat messages for a session.
        """
        try:
            repo_object_id = self._to_object_id(repo_id)
            session_doc = await self.chat_sessions.find_one(
                {"repo_id": repo_object_id, "session_id": session_id},
                {"messages": {"$slice": -limit}},
            )
            if not session_doc:
                return []

            return session_doc.get("messages", [])
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to fetch recent messages for session %s in repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return []

    async def get_chat_messages(
        self, repo_id: str, session_id: str, skip: int = 0, limit: int = 50
    ) -> Tuple[List[Dict], int]:
        """
        Get chat messages for a session with pagination.

        Args:
            repo_id: Repository identifier
            session_id: Chat session identifier
            skip: Number of messages to skip
            limit: Maximum number of messages to return

        Returns:
            Tuple of (list of message dictionaries, total count)
        """
        try:
            repo_object_id = self._to_object_id(repo_id)

            # Find the session to get messages
            session_doc = await self.chat_sessions.find_one(
                {"repo_id": repo_object_id, "session_id": session_id},
                {"messages": 1}
            )

            if not session_doc:
                logger.debug("Chat session not found: %s", session_id)
                return [], 0

            messages = session_doc.get("messages", [])
            total = len(messages)

            # Apply pagination (messages are already in chronological order)
            paginated_messages = messages[skip : skip + limit]

            logger.debug(
                "Retrieved %d messages for session %s (skip: %d, limit: %d, total: %d)",
                len(paginated_messages),
                session_id,
                skip,
                limit,
                total,
            )

            return paginated_messages, total

        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to get chat messages for session %s in repo %s: %s",
                session_id,
                repo_id,
                e,
                exc_info=True,
            )
            return [], 0

    async def find_repository_by_identifier(
        self,
        owner: str,
        repo_name: str,
        branch: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Find a repository by owner and repo_name.

        Args:
            owner: Repository owner
            repo_name: Repository name

        Returns:
            Repository document if found, None otherwise
        """
        try:
            repo_doc = await self.repositories.find_one(
                {"owner": owner, "repo_name": repo_name, "branch": branch}
            )
            return repo_doc

        except Exception as e:
            logger.error(
                "Failed to find repository by owner, name and branch: %s",
                e,
                exc_info=True
            )
            return None

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
                    chunk_id = chunk_data.get("chunk_id")
                    if not chunk_id:
                        logger.warning("Skipping chunk without chunk_id: %s", chunk_data)
                        continue

                    repo_identifier = chunk_data.get("repo_id")
                    if not repo_identifier:
                        logger.warning("Skipping chunk %s without repo_id", chunk_id)
                        continue

                    normalized_chunk = dict(chunk_data)
                    normalized_chunk["repo_id"] = self._to_object_id(repo_identifier)

                    chunk_schema = ChunkSchema(**normalized_chunk)
                    chunk_dict = chunk_schema.model_dump()
                    operations.append(
                        UpdateOne(
                            {"chunk_id": chunk_id},
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
            repo_object_id = self._to_object_id(repo_id)
            result = await self.chunks.delete_many({"repo_id": repo_object_id})
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

    async def delete_repository(self, repo_id: str) -> bool:
        """
        Delete a repository document by ID.

        Args:
            repo_id: Repository identifier (MongoDB ObjectId as string)

        Returns:
            True if successful, False otherwise
        """
        try:
            repo_object_id = self._to_object_id(repo_id)
            result = await self.repositories.delete_one({"_id": repo_object_id})

            if result.deleted_count > 0:
                logger.info("Deleted repository: %s", repo_id)
                return True
            else:
                logger.warning("Repository not found for deletion: %s", repo_id)
                return False

        except Exception as e:
            logger.error(
                "Failed to delete repository %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return False

    async def delete_queries_by_repo(self, repo_id: str) -> bool:
        """
        Delete all queries associated with a repository.

        Args:
            repo_id: Repository identifier (MongoDB ObjectId as string)

        Returns:
            True if successful or if queries collection doesn't exist, False on error
        """
        try:
            # Check if queries collection exists
            collections = await mongodb.db.list_collection_names()
            if "csQueries" not in collections:
                logger.debug("Queries collection doesn't exist, skipping query deletion")
                return True

            queries_collection = mongodb.db["csQueries"]
            repo_object_id = self._to_object_id(repo_id)
            result = await queries_collection.delete_many({"repo_id": repo_object_id})

            logger.info(
                "Deleted %d queries for repository: %s",
                result.deleted_count,
                repo_id
            )
            return True

        except Exception as e:
            logger.error(
                "Failed to delete queries for repository %s: %s",
                repo_id,
                e,
                exc_info=True
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
            repo_object_id = self._to_object_id(repo_id)
            repo_doc = await self.repositories.find_one({"_id": repo_object_id})

            if not repo_doc:
                logger.debug("Repository not found: %s", repo_id)
                return None

            repo_doc["id"] = str(repo_doc["_id"])
            repo_doc.pop("_id", None)

            # Validate with schema
            try:
                repo_schema = RepositorySchema(**{k: v for k, v in repo_doc.items() if k != "id"})
                repo_dict = repo_schema.model_dump()
                repo_dict["id"] = str(repo_object_id)
                return repo_dict
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

            # Projection for lightweight summary fields (matching UI requirements)
            projection = {
                "repo_name": 1,
                "repository_url": 1,
                "status": 1,
                "files_processed": 1,
                "created_at": 1,
                "updated_at": 1,
                "_id": 1
            }

            # Fetch repositories with pagination and search, sorted by updated_at descending
            cursor = (
                self.repositories.find(search_filter, projection)
                .sort("updated_at", -1)
                .skip(skip)
                .limit(limit)
            )
            repo_docs = await cursor.to_list(length=limit)

            for repo_doc in repo_docs:
                repo_doc["id"] = str(repo_doc["_id"])
                repo_doc.pop("_id", None)

            logger.debug(
                "Listed %d repositories (skip: %d, limit: %d, total: %d, search: %s)",
                len(repo_docs),
                skip,
                limit,
                total,
                search or "none"
            )

            return repo_docs, total

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
            repo_object_id = self._to_object_id(repo_id)
            repo = await self.get_repository(repo_id)
            if not repo:
                logger.debug("Repository not found for stats: %s", repo_id)
                return None

            # Get total chunks count
            total_chunks = await self.chunks.count_documents({"repo_id": repo_object_id})

            # Get language distribution using find and manual counting
            # (more reliable than aggregate for async PyMongo)
            language_distribution = {}
            async for chunk in self.chunks.find({"repo_id": repo_object_id}, {"language": 1}):
                language = chunk.get("language", "unknown")
                language_distribution[language] = language_distribution.get(language, 0) + 1

            # Get chunk type distribution from metadata.type
            chunk_type_distribution = {}
            async for chunk in self.chunks.find({"repo_id": repo_object_id}, {"metadata.type": 1}):
                chunk_type = chunk.get("metadata", {}).get("type", "unknown")
                chunk_type_distribution[chunk_type] = chunk_type_distribution.get(chunk_type, 0) + 1

            # Get unique file count using distinct
            unique_files = await self.chunks.distinct("file_path", {"repo_id": repo_object_id})
            total_files = len(unique_files)

            stats = {
                "id": repo_id,
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

    async def create_ingestion_request(self, repo_data: Dict) -> Optional[ObjectId]:
        """
        Create a new ingestion request document.

        Args:
            repo_data: Repository data dictionary with owner, repo_name, repository_url, branch

        Returns:
            Request ObjectId if successful, None otherwise
        """
        try:
            request_schema = IngestionRequestSchema(**repo_data)
            request_dict = request_schema.model_dump(by_alias=True, exclude={"id", "_id"})

            # Remove _id if present (let MongoDB generate it)
            request_dict.pop("_id", None)
            request_dict.pop("id", None)

            result = await self.ingestion_requests.insert_one(request_dict)
            request_object_id = result.inserted_id

            logger.info(
                "Created ingestion request: %s (_id: %s)",
                f"{repo_data.get('owner')}/{repo_data.get('repo_name')}",
                str(request_object_id)
            )

            return request_object_id

        except Exception as e:
            logger.error("Failed to create ingestion request: %s", e, exc_info=True)
            return None

    async def get_ingestion_request_by_repo_id(
        self,
        repo_id: str
    ) -> Optional[Dict]:
        """
        Get the most recent ingestion request for a repository.

        Args:
            repo_id: Repository identifier (MongoDB ObjectId as string)

        Returns:
            Ingestion request dictionary or None if not found
        """
        try:
            repo_object_id = self._to_object_id(repo_id)

            request_doc = await self.ingestion_requests.find_one(
                {"repo_id": repo_object_id},
                sort=[("created_at", -1)]  # Get most recent
            )

            if not request_doc:
                return None

            request_doc["id"] = str(request_doc["_id"])
            request_doc.pop("_id", None)

            if "repo_id" in request_doc and request_doc["repo_id"]:
                request_doc["repo_id"] = str(request_doc["repo_id"])

            return request_doc

        except Exception as e:
            logger.error(
                "Failed to get ingestion request by repo_id %s: %s",
                repo_id,
                e,
                exc_info=True
            )
            return None

    async def get_ingestion_request_by_identifier(
        self,
        owner: str,
        repo_name: str,
        branch: Optional[str] = None
    ) -> Optional[Dict]:
        """
        Get the most recent ingestion request by repository identifier.

        Args:
            owner: Repository owner
            repo_name: Repository name
            branch: Repository branch (optional)

        Returns:
            Ingestion request dictionary or None if not found
        """
        try:
            query = {
                "owner": owner,
                "repo_name": repo_name
            }

            if branch:
                query["branch"] = branch

            request_doc = await self.ingestion_requests.find_one(
                query,
                sort=[("created_at", -1)]  # Get most recent
            )

            if not request_doc:
                return None

            request_doc["id"] = str(request_doc["_id"])
            request_doc.pop("_id", None)

            if "repo_id" in request_doc and request_doc["repo_id"]:
                request_doc["repo_id"] = str(request_doc["repo_id"])

            return request_doc

        except Exception as e:
            logger.error(
                "Failed to get ingestion request by identifier %s/%s: %s",
                owner,
                repo_name,
                e,
                exc_info=True
            )
            return None

    async def update_ingestion_request_status(
        self,
        request_id: str,
        status: str,
        error: Optional[str] = None,
        message: Optional[str] = None,
        repo_id: Optional[str] = None
    ) -> bool:
        """
        Update ingestion request status and related fields.

        Args:
            request_id: Request identifier
            status: New status (pending, processing, completed, failed)
            error: Optional error message
            message: Optional status message
            repo_id: Optional repository ObjectId to link

        Returns:
            True if successful, False otherwise
        """
        try:
            # Build update document
            update_doc = {
                "$set": {
                    "status": status,
                    "updated_at": datetime.now()
                }
            }

            # Handle error field
            if error is not None:
                update_doc["$set"]["error"] = error
            # Clear error when status changes to pending (for retry scenarios)
            elif status == "pending":
                if "$unset" not in update_doc:
                    update_doc["$unset"] = {}
                update_doc["$unset"]["error"] = ""
            if message is not None:
                update_doc["$set"]["message"] = message
            if repo_id is not None:
                update_doc["$set"]["repo_id"] = self._to_object_id(repo_id)

            # Set started_at when status changes to processing (only if not already set)
            if status == "processing":
                # Use $setOnInsert to only set if field doesn't exist
                if "$setOnInsert" not in update_doc:
                    update_doc["$setOnInsert"] = {}
                update_doc["$setOnInsert"]["started_at"] = datetime.now()

            # Set completed_at when status changes to completed or failed
            if status in ("completed", "failed"):
                update_doc["$set"]["completed_at"] = datetime.now()

            # Find request by ObjectId
            try:
                request_object_id = self._to_object_id(request_id)
                filter_query = {"_id": request_object_id}
            except ValueError as e:
                logger.warning("Invalid ObjectId format for request_id %s: %s", request_id, e)
                return False

            result = await self.ingestion_requests.update_one(filter_query, update_doc)

            if result.matched_count > 0:
                logger.debug(
                    "Updated ingestion request %s: status=%s",
                    request_id,
                    status
                )
                return True
            else:
                logger.warning("Ingestion request not found for update: %s", request_id)
                return False

        except Exception as e:
            logger.error(
                "Failed to update ingestion request status %s: %s",
                request_id,
                e,
                exc_info=True
            )
            return False

    async def check_retry_rate_limit(
        self,
        owner: str,
        repo_name: str,
        branch: Optional[str] = None,
        time_window_hours: int = 2,
        max_retries: int = 3
    ) -> Tuple[bool, int, Optional[datetime]]:
        """
        Check if retry is allowed based on rate limiting rules.

        Args:
            owner: Repository owner
            repo_name: Repository name
            branch: Repository branch
            time_window_hours: Time window in hours (default: 2)
            max_retries: Maximum retries allowed in time window (default: 3)

        Returns:
            Tuple of (allowed: bool, current_retry_count: int, retry_after: Optional[datetime])
        """
        try:

            time_window = datetime.now() - timedelta(hours=time_window_hours)

            # Query failed requests or requests with retry_count > 0 in the time window
            query = {
                "owner": owner,
                "repo_name": repo_name,
                "$or": [
                    {"status": "failed"},
                    {"retry_count": {"$gt": 0}}
                ],
                "created_at": {"$gte": time_window}
            }

            if branch:
                query["branch"] = branch

            cursor = self.ingestion_requests.find(query)
            failed_requests = await cursor.to_list(length=None)

            retry_count = len(failed_requests)

            if retry_count >= max_retries:
                # Find oldest failed request to calculate retry_after
                if failed_requests:
                    oldest_request = min(
                        failed_requests, key=lambda x: x.get("created_at", datetime.now()))
                    oldest_time = oldest_request.get("created_at", datetime.now())
                    retry_after = oldest_time + timedelta(hours=time_window_hours)
                    logger.warning(
                    "Rate limit exceeded for %s/%s: %d retries in last %d hours. Retry after: %s",
                        owner,
                        repo_name,
                        retry_count,
                        time_window_hours,
                        retry_after
                    )
                    return False, retry_count, retry_after

            return True, retry_count, None

        except Exception as e:
            logger.error(
                "Failed to check retry rate limit for %s/%s: %s",
                owner,
                repo_name,
                e,
                exc_info=True
            )
            # On error, allow retry (fail open)
            return True, 0, None

    async def get_active_ingestion_request(
        self,
        owner: str,
        repo_name: str,
        branch: Optional[str] = None) -> Optional[Dict]:
        """
        Get active (pending or processing) ingestion request for a repository.

        Args:
            owner: Repository owner
            repo_name: Repository name

        Returns:
            Active ingestion request dictionary or None if not found
        """
        try:

            query = {
                "owner": owner,
                "repo_name": repo_name,
                "status": {"$in": ["pending", "processing"]}
            }

            if branch:
                query["branch"] = branch

            request_doc = await self.ingestion_requests.find_one(
                query,
                sort=[("created_at", -1)]  # Get most recent active request
            )

            if not request_doc:
                return None

            request_doc["id"] = str(request_doc["_id"])
            request_doc.pop("_id", None)

            # Convert repo_id ObjectId to string if present
            if "repo_id" in request_doc and request_doc["repo_id"]:
                request_doc["repo_id"] = str(request_doc["repo_id"])

            return request_doc

        except Exception as e:
            logger.error(
                "Failed to get active ingestion request for %s/%s: %s",
                owner,
                repo_name,
                e,
                exc_info=True
            )
            return None

    async def increment_retry_count(self, request_id: str) -> bool:
        """
        Increment retry count for an ingestion request.

        Args:
            request_id: Request identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            # Find request by ObjectId
            try:
                request_object_id = self._to_object_id(request_id)
                filter_query = {"_id": request_object_id}
            except ValueError as e:
                logger.warning("Invalid ObjectId format for request_id %s: %s", request_id, e)
                return False

            result = await self.ingestion_requests.update_one(
                filter_query,
                {
                    "$inc": {"retry_count": 1},
                    "$set": {"updated_at": datetime.now()}
                }
            )

            return result.matched_count > 0

        except Exception as e:
            logger.error(
                "Failed to increment retry count for request %s: %s",
                request_id,
                e,
                exc_info=True
            )
            return False
