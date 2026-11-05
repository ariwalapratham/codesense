"""Retrieval: embed query, search VectorStore, load chunk text from Mongo."""

import logging
import re
from typing import Dict, List, Optional, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from services.embeddings.base import Embedder
from services.mongodb_service import MongoDBService
from services.vector_store.base import VectorStore

logger = logging.getLogger(__name__)

MIN_SIMILARITY = 0.15


class RetrievalService:
    """Embed a query, search by Mongo repo_id, join chunk text from Mongo."""

    def __init__(self, *, embedder: Embedder, vector_store: VectorStore):
        """Use the shared embedder and vector store from app lifespan."""
        self.mongodb_service = MongoDBService()
        self.embedder = embedder
        self.vector_store = vector_store

    async def generate_query_embedding(self, query: str) -> List[float]:
        """Embed a normalized user query. Upstream failures become RuntimeError."""
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            raise ValueError("Query text is empty after normalization.")
        try:
            vectors = await self.embedder.embed([normalized_query])
        except RuntimeError:
            raise
        except Exception as exc:
            logger.error("Embedder failed: %s", exc)
            raise RuntimeError("Embedder unavailable") from exc
        if not vectors:
            raise RuntimeError("Embedder returned no vector")
        return vectors[0]

    async def search_repository_chunks(
        self,
        repo_id: str,
        embedding: List[float],
        top_k: int = 15,
        *,
        path_prefix: Optional[str] = None,
        language: Optional[str] = None,
    ) -> List[Tuple[str, float]]:
        """Return (chunk_id, cosine score) for one repo. Does not load code_text."""
        if not embedding:
            return []
        try:
            hits = await self.vector_store.search(
                embedding,
                repo_id=repo_id,
                top_k=top_k,
                path_prefix=path_prefix,
                language=language,
            )
        except Exception as exc:
            logger.error("Vector store search failed: %s", exc)
            raise RuntimeError("Vector store unavailable") from exc
        return [(hit.chunk_id, hit.score) for hit in hits]

    async def search_with_text(
        self,
        repo_id: str,
        query: str,
        *,
        top_k: int = 10,
        path_prefix: Optional[str] = None,
        language: Optional[str] = None,
        min_score: float = MIN_SIMILARITY,
    ) -> List[Dict]:
        """Search then attach Mongo code_text. Used by GET /repository/{id}/search."""
        embedding = await self.generate_query_embedding(query)
        hits = await self.search_repository_chunks(
            repo_id,
            embedding,
            top_k=top_k,
            path_prefix=path_prefix,
            language=language,
        )
        filtered = [(chunk_id, score) for chunk_id, score in hits if score >= min_score]
        docs = await self.fetch_chunk_metadata(
            [chunk_id for chunk_id, _ in filtered], repo_id
        )
        by_id = {doc.get("chunk_id"): doc for doc in docs}
        results: List[Dict] = []
        for chunk_id, score in filtered:
            doc = by_id.get(chunk_id)
            if not doc:
                continue
            results.append(
                {
                    "chunk_id": chunk_id,
                    "file_path": doc.get("file_path", ""),
                    "function_name": doc.get("function_name"),
                    "class_name": doc.get("class_name"),
                    "language": doc.get("language", ""),
                    "start_line": doc.get("start_line", 0),
                    "end_line": doc.get("end_line", 0),
                    "score": score,
                    "code_text": doc.get("code_text", ""),
                }
            )
        return results

    async def fetch_chunk_metadata(
        self,
        chunk_ids: List[str],
        repo_id: str,
    ) -> List[Dict]:
        """Load chunk docs for ids, scoped to repo_id, preserving hit order."""
        if not chunk_ids:
            return []

        try:
            repo_object_id = ObjectId(repo_id)
        except (InvalidId, TypeError):
            logger.error("Invalid repo_id provided for chunk metadata: %s", repo_id)
            return []
        cursor = self.mongodb_service.chunks.find(
            {
                "repo_id": repo_object_id,
                "chunk_id": {"$in": chunk_ids},
            }
        )
        chunk_docs = await cursor.to_list(length=len(chunk_ids))
        chunk_map = {doc.get("chunk_id"): doc for doc in chunk_docs}
        return [chunk_map[chunk_id] for chunk_id in chunk_ids if chunk_id in chunk_map]

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Strip fences and collapse whitespace. Cap at 4000 chars."""
        if not query:
            return ""
        text = query.strip()
        text = re.sub(r"```[\s\S]*?```", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text[:4000]
