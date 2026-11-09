"""MongoDB service for upserting repositories and chunks."""

import base64
import json
import logging
from datetime import datetime, timedelta, timezone
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
    UserRepositorySchema,
)

# pylint: disable=broad-exception-caught

logger = logging.getLogger(__name__)


def _encode_repo_identifier(owner: str, repo_name: str, branch: str) -> str:
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


class MongoDBService:
    """Service for MongoDB operations on repositories and chunks."""

    def __init__(self):
        """Initialize MongoDB service with collections."""
        self.repositories = mongodb.db["csRepositories"]
        self.chunks = mongodb.db["csChunks"]
        self.chat_sessions = mongodb.db["csChatSessions"]
        self.ingestion_requests = mongodb.db["csIngestionRequests"]
        self.user_repositories = mongodb.db["csUserRepositories"]

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

    def _session_filter(
        self,
        repo_id: Union[str, ObjectId],
        session_id: str,
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> Dict:
        """Match a chat session, scoped to user_id when given."""
        filt: Dict = {
            "repo_id": self._to_object_id(repo_id),
            "session_id": session_id,
        }
        if user_id is not None:
            filt["user_id"] = self._to_object_id(user_id)
        return filt

    async def has_library_access(
        self,
        user_id: Union[str, ObjectId],
        repo_id: Union[str, ObjectId],
    ) -> bool:
        """True when csUserRepositories has (user_id, repo_id)."""
        try:
            doc = await self.user_repositories.find_one(
                {
                    "user_id": self._to_object_id(user_id),
                    "repo_id": self._to_object_id(repo_id),
                },
                {"_id": 1},
            )
            return doc is not None
        except PyMongoError:
            logger.error("Failed library access check", exc_info=True)
            raise
        except Exception as e:
            logger.error("Failed library access check: %s", e, exc_info=True)
            raise

    async def upsert_library_link(
        self,
        user_id: Union[str, ObjectId],
        repo_id: Union[str, ObjectId],
        *,
        role: str = "member",
    ) -> bool:
        """Add the repo to the user's library. Idempotent on (user_id, repo_id)."""
        try:
            user_oid = self._to_object_id(user_id)
            repo_oid = self._to_object_id(repo_id)
            link = UserRepositorySchema(user_id=user_oid, repo_id=repo_oid, role=role)
            dumped = link.model_dump()
            result = await self.user_repositories.update_one(
                {"user_id": user_oid, "repo_id": repo_oid},
                {
                    "$setOnInsert": {
                        "user_id": dumped["user_id"],
                        "repo_id": dumped["repo_id"],
                        "role": dumped["role"],
                        "added_at": dumped["added_at"],
                        "last_opened_at": dumped["last_opened_at"],
                    }
                },
                upsert=True,
            )
            return result.acknowledged
        except Exception as e:
            logger.error("Failed to upsert library link: %s", e, exc_info=True)
            return False

    async def unlink_library(
        self,
        user_id: Union[str, ObjectId],
        repo_id: Union[str, ObjectId],
    ) -> bool:
        """Remove the library row only. Indexed repo data stays."""
        try:
            result = await self.user_repositories.delete_one(
                {
                    "user_id": self._to_object_id(user_id),
                    "repo_id": self._to_object_id(repo_id),
                }
            )
            return result.deleted_count > 0
        except Exception as e:
            logger.error("Failed to unlink library row: %s", e, exc_info=True)
            return False

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
            branch = repo_dict.get("branch") or "main"
            owner_user_id = repo_dict.get("owner_user_id")
            repo_dict["branch"] = branch
            repo_dict["visibility"] = repo_dict.get("visibility") or "public"
            repo_dict["owner_user_id"] = owner_user_id
            filt = {
                "owner": owner,
                "repo_name": repo_name,
                "branch": branch,
                "owner_user_id": owner_user_id,
            }

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
                filt,
                update_doc,
                upsert=True
            )

            # Get the repository _id (either from upserted_id or by querying)
            if result.upserted_id:
                repo_object_id = result.upserted_id
            else:
                # If it was an update, fetch the _id
                repo_doc = await self.repositories.find_one(filt, {"_id": 1})
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
        self,
        repo_id: str,
        skip: int = 0,
        limit: int = 10,
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> Tuple[List[Dict], int]:
        """List chat sessions for a repository, scoped to user_id when given."""
        try:
            repo_object_id = self._to_object_id(repo_id)
            query: Dict = {"repo_id": repo_object_id}
            if user_id is not None:
                query["user_id"] = self._to_object_id(user_id)

            total = await self.chat_sessions.count_documents(query)

            cursor = (
                self.chat_sessions.find(
                    query,
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
                user_id=self._to_object_id(user_id) if user_id else None,
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
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> bool:
        """Update chat session metadata."""
        try:
            updates["updated_at"] = datetime.now()
            result = await self.chat_sessions.update_one(
                self._session_filter(repo_id, session_id, user_id),
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

    async def delete_chat_session(
        self,
        repo_id: str,
        session_id: str,
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> bool:
        """Delete a chat session and its messages."""
        try:
            result = await self.chat_sessions.delete_one(
                self._session_filter(repo_id, session_id, user_id)
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

    async def get_chat_session(
        self,
        repo_id: str,
        session_id: str,
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> Optional[Dict]:
        """Fetch a chat session. When user_id is set, other users' sessions 404."""
        try:
            session_doc = await self.chat_sessions.find_one(
                self._session_filter(repo_id, session_id, user_id)
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
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> bool:
        """Append a chat message to a session."""
        try:
            message_doc = ChatMessageDocument(**message).model_dump()
            result = await self.chat_sessions.update_one(
                self._session_filter(repo_id, session_id, user_id),
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

    async def fail_stale_streaming_messages(self, *, older_than_minutes: int = 10) -> int:
        """Mark streaming chat messages older than the cutoff as failed."""
        try:
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(minutes=older_than_minutes)
            result = await self.chat_sessions.update_many(
                {
                    "messages": {
                        "$elemMatch": {
                            "status": "streaming",
                            "timestamp": {"$lt": cutoff},
                        }
                    }
                },
                {
                    "$set": {
                        "messages.$[m].status": "failed",
                        "updated_at": now,
                    }
                },
                array_filters=[
                    {"m.status": "streaming", "m.timestamp": {"$lt": cutoff}},
                ],
            )
            if result.modified_count:
                logger.info(
                    "Marked stale streaming messages as failed in %s session(s)",
                    result.modified_count,
                )
            return result.modified_count
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to recover stale streaming messages: %s",
                e,
                exc_info=True,
            )
            return 0

    async def recover_stuck_processing_jobs(self) -> int:
        """Make lease-less processing jobs claimable after a process death."""
        try:
            now = datetime.now(timezone.utc)
            result = await self.ingestion_requests.update_many(
                {
                    "status": "processing",
                    "$or": [
                        {"lease": None},
                        {"lease.expires_at": {"$exists": False}},
                        {"lease.expires_at": None},
                    ],
                },
                {
                    "$set": {
                        "lease.expires_at": now - timedelta(seconds=1),
                        "updated_at": now,
                    }
                },
            )
            if result.modified_count:
                logger.info(
                    "Marked %s stuck processing job(s) as claimable",
                    result.modified_count,
                )
            return result.modified_count
        except Exception as e:  # pylint: disable=broad-except
            logger.error(
                "Failed to recover stuck processing jobs: %s",
                e,
                exc_info=True,
            )
            return 0

    async def release_repo_if_processing(self, repo_id: str) -> None:
        """Unstick a repo left in processing after its job was cancelled."""
        try:
            repo_oid = self._to_object_id(repo_id)
        except ValueError:
            return
        repo = await self.repositories.find_one(
            {"_id": repo_oid},
            {"status": 1, "last_ingested_at": 1},
        )
        if not repo or repo.get("status") != "processing":
            return
        new_status = "completed" if repo.get("last_ingested_at") else "failed"
        await self.repositories.update_one(
            {"_id": repo_oid, "status": "processing"},
            {"$set": {"status": new_status, "updated_at": datetime.now(timezone.utc)}},
        )

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
        self,
        repo_id: str,
        session_id: str,
        skip: int = 0,
        limit: int = 50,
        user_id: Optional[Union[str, ObjectId]] = None,
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
            session_doc = await self.chat_sessions.find_one(
                self._session_filter(repo_id, session_id, user_id),
                {"messages": 1},
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
        """Find the shared public repo for owner/name/branch (`owner_user_id` null)."""
        try:
            repo_doc = await self.repositories.find_one(
                {
                    "owner": owner,
                    "repo_name": repo_name,
                    "branch": branch or "main",
                    "owner_user_id": None,
                }
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

            if len(operations) != len(chunks_data):
                logger.error(
                    "Chunk validation dropped %d of %d documents",
                    len(chunks_data) - len(operations),
                    len(chunks_data),
                )
                return False

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
            return False

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

    async def list_embedded_hash_index(self, repo_id: str) -> Dict[str, str]:
        """content_hash → point_id for chunks in this repo that already have a vector."""
        try:
            repo_object_id = self._to_object_id(repo_id)
        except ValueError:
            return {}
        cursor = self.chunks.find(
            {
                "repo_id": repo_object_id,
                "embedded_at": {"$ne": None},
                "content_hash": {"$nin": [None, ""]},
                "point_id": {"$nin": [None, ""]},
            },
            {"content_hash": 1, "point_id": 1},
        )
        index: Dict[str, str] = {}
        async for doc in cursor:
            digest = doc.get("content_hash")
            point_id = doc.get("point_id")
            if digest and point_id:
                index[digest] = point_id
        return index

    async def list_chunk_point_ids(self, repo_id: str) -> List[str]:
        """All stored point_ids for a repo, used to prune leftover Qdrant points."""
        try:
            repo_object_id = self._to_object_id(repo_id)
        except ValueError:
            return []
        cursor = self.chunks.find(
            {"repo_id": repo_object_id, "point_id": {"$nin": [None, ""]}},
            {"point_id": 1},
        )
        ids: List[str] = []
        async for doc in cursor:
            point_id = doc.get("point_id")
            if point_id:
                ids.append(point_id)
        return ids

    async def list_chunks_missing_embeddings(
        self, repo_id: str, *, limit: int = 100
    ) -> List[Dict]:
        """Page of chunks with embedded_at null. Reembed loops until this is empty."""
        try:
            repo_object_id = self._to_object_id(repo_id)
        except ValueError:
            return []
        cursor = self.chunks.find(
            {"repo_id": repo_object_id, "embedded_at": None}
        ).limit(limit)
        return await cursor.to_list(length=limit)

    async def replace_repo_chunks(self, repo_id: str, chunks_data: List[Dict]) -> bool:
        """Upsert the new chunk set, then delete chunk_ids that are no longer present."""
        if not chunks_data:
            return False
        if not await self.upsert_chunks(chunks_data):
            return False
        keep_ids = [chunk["chunk_id"] for chunk in chunks_data if chunk.get("chunk_id")]
        if not keep_ids:
            return False
        try:
            await self.chunks.delete_many(
                {
                    "repo_id": self._to_object_id(repo_id),
                    "chunk_id": {"$nin": keep_ids},
                }
            )
            return True
        except Exception as e:
            logger.error(
                "Failed to delete stale chunks for repository %s: %s",
                repo_id,
                e,
                exc_info=True,
            )
            return False

    async def mark_chunks_embedded(self, chunks: List[Dict]) -> bool:
        """Set embedded_at / point_id / content_hash after a successful Qdrant upsert."""
        operations: List[UpdateOne] = []
        for chunk in chunks:
            chunk_id = chunk.get("chunk_id")
            if not chunk_id or not chunk.get("embedded_at"):
                continue
            operations.append(
                UpdateOne(
                    {"chunk_id": chunk_id},
                    {
                        "$set": {
                            "embedded_at": chunk["embedded_at"],
                            "embedding_model": chunk.get("embedding_model"),
                            "point_id": chunk.get("point_id"),
                            "content_hash": chunk.get("content_hash"),
                            "schema_version": chunk.get("schema_version", 2),
                        }
                    },
                )
            )
        if not operations:
            return True
        try:
            await self.chunks.bulk_write(operations, ordered=False)
            return True
        except Exception as e:
            logger.error("Failed to mark chunks embedded: %s", e, exc_info=True)
            return False

    async def recount_embedded_chunks(self, repo_id: str) -> int:
        """Set csRepositories.chunks_with_embeddings from chunks with embedded_at."""
        repo_object_id = self._to_object_id(repo_id)
        count = await self.chunks.count_documents(
            {"repo_id": repo_object_id, "embedded_at": {"$ne": None}}
        )
        await self.repositories.update_one(
            {"_id": repo_object_id},
            {
                "$set": {
                    "chunks_with_embeddings": count,
                    "updated_at": datetime.now(),
                }
            },
        )
        return count

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
        search: Optional[str] = None,
        user_id: Optional[Union[str, ObjectId]] = None,
    ) -> Tuple[List[Dict], int]:
        """List the caller's library repos, newest link first."""
        try:
            if user_id is None:
                return [], 0

            user_oid = self._to_object_id(user_id)
            links = await self.user_repositories.find(
                {"user_id": user_oid},
                {"repo_id": 1, "added_at": 1},
            ).sort("added_at", -1).to_list(length=None)
            if not links:
                return [], 0

            order = {link["repo_id"]: index for index, link in enumerate(links)}
            repo_filter: Dict = {"_id": {"$in": list(order.keys())}}
            if search:
                search_regex = {"$regex": search, "$options": "i"}
                repo_filter = {
                    "$and": [
                        repo_filter,
                        {
                            "$or": [
                                {"repo_name": search_regex},
                                {"owner": search_regex},
                                {"repository_url": search_regex},
                            ]
                        },
                    ]
                }

            repo_projection = {
                "repo_name": 1,
                "repository_url": 1,
                "status": 1,
                "files_processed": 1,
                "created_at": 1,
                "updated_at": 1,
                "_id": 1,
            }
            repo_docs = await self.repositories.find(
                repo_filter, repo_projection
            ).to_list(length=None)

            summaries = []
            for repo_doc in repo_docs:
                summaries.append(
                    {
                        "id": str(repo_doc["_id"]),
                        "repo_name": repo_doc.get("repo_name"),
                        "repository_url": repo_doc.get("repository_url"),
                        "status": repo_doc.get("status", "completed"),
                        "files_processed": repo_doc.get("files_processed", 0),
                        "created_at": repo_doc.get("created_at"),
                        "updated_at": repo_doc.get("updated_at"),
                        "_order": order.get(repo_doc["_id"], 0),
                    }
                )
            summaries.sort(key=lambda item: item["_order"])
            total = len(summaries)
            page = summaries[skip:skip + limit]
            for repo in page:
                repo.pop("_order", None)
            return page, total

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

    async def get_ingestion_request(
        self,
        request_id: str
    ) -> Optional[Dict]:
        """
        Get an ingestion request by its ID.

        Args:
            request_id: Ingestion request identifier (MongoDB ObjectId as string)

        Returns:
            Ingestion request dictionary or None if not found
        """
        try:
            request_object_id = self._to_object_id(request_id)

            request_doc = await self.ingestion_requests.find_one(
                {"_id": request_object_id}
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
                "Failed to get ingestion request %s: %s",
                request_id, e, exc_info=True
            )
            return None

    async def list_ingestion_requests(
        self,
        user_id: Union[str, ObjectId],
        skip: int = 0,
        limit: int = 20,
    ) -> Tuple[List[Dict], int]:
        """List ingestion jobs created by this user, newest first."""
        try:
            filt = {"requested_by": self._to_object_id(user_id)}
            total = await self.ingestion_requests.count_documents(filt)
            cursor = (
                self.ingestion_requests.find(filt)
                .sort("created_at", -1)
                .skip(skip)
                .limit(limit)
            )
            docs = await cursor.to_list(length=limit)
            for doc in docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                if doc.get("repo_id"):
                    doc["repo_id"] = str(doc["repo_id"])
            return docs, total
        except Exception as e:
            logger.error("Failed to list ingestion requests: %s", e, exc_info=True)
            return [], 0

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
        branch: Optional[str] = None,
        requested_by: Optional[Union[str, ObjectId]] = None,
    ) -> Optional[Dict]:
        """Get the most recent ingestion request for this identifier, optionally owned by the caller."""
        try:
            query: Dict = {
                "owner": owner,
                "repo_name": repo_name,
            }

            if branch:
                query["branch"] = branch
            if requested_by is not None:
                query["requested_by"] = self._to_object_id(requested_by)

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
                # Clear completed_at and started_at on retry to track new attempt
                update_doc["$unset"]["completed_at"] = ""
                update_doc["$unset"]["started_at"] = ""
            if message is not None:
                update_doc["$set"]["message"] = message
            if repo_id is not None:
                update_doc["$set"]["repo_id"] = self._to_object_id(repo_id)

            # Set started_at when status changes to processing
            # If started_at was cleared (retry scenario), set it again
            if status == "processing":
                # Always set started_at when processing starts (handles both new and retry scenarios)
                update_doc["$set"]["started_at"] = datetime.now()

            # Set completed_at when status changes to completed, failed, or cancelled
            if status in ("completed", "failed", "cancelled"):
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
