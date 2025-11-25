"""Retrieval service for embeddings + chunk lookup."""

import asyncio
import logging
import re
from typing import Dict, List, Tuple

from bson import ObjectId
from bson.errors import InvalidId

from services.embedding_service import EmbeddingService
from services.mongodb_service import MongoDBService
from services.vector_store_service import VectorStoreService

logger = logging.getLogger(__name__)


class RetrievalService:
    """Coordinates embedding generation, FAISS search, and chunk fetching."""

    def __init__(self):
        self.mongodb_service = MongoDBService()
        self.vector_store_service = VectorStoreService()
        self.embedding_service = EmbeddingService()

    async def generate_query_embedding(self, query: str) -> List[float]:
        """
        Generate an embedding for the user query.

        Args:
            query: Raw user question.

        Returns:
            Embedding vector as a list of floats.
        """
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            raise ValueError("Query text is empty after normalization.")

        return await self.embedding_service.generate_text_embedding(normalized_query)

    async def search_repository_chunks(
        self,
        repo_vector_id: str,
        embedding: List[float],
        top_k: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        Search FAISS for the most relevant chunks for a repository.

        Args:
            repo_vector_id: Identifier used for FAISS index filenames (owner_repo).
            embedding: Query embedding vector.
            top_k: Number of results to return.

        Returns:
            List of (chunk_id, score) tuples ordered by similarity.
        """
        if not embedding:
            return []

        loop = asyncio.get_running_loop()
        faiss_results = await loop.run_in_executor(
            None,
            lambda: self.vector_store_service.search_similar(
                query_embedding=embedding,
                repo_id=repo_vector_id,
                top_k=top_k,
            ),
        )

        hits: List[Tuple[str, float]] = []
        for index_position, distance in faiss_results:
            chunk_id = self.vector_store_service.get_chunk_id_from_index(
                repo_vector_id, index_position
            )
            if not chunk_id:
                continue
            score = self._distance_to_similarity(distance)
            hits.append((chunk_id, score))

        return hits

    async def fetch_chunk_metadata(
        self,
        chunk_ids: List[str],
        repo_id: str,
    ) -> List[Dict]:
        """
        Fetch chunk metadata/content from MongoDB and filter by repo_id.

        Args:
            chunk_ids: List of chunk identifiers.
            repo_id: Repository identifier (Mongo ObjectId as string).

        Returns:
            List of chunk documents ready for prompt construction.
        """
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

        # Preserve original order from FAISS results
        ordered_chunks = [
            chunk_map[chunk_id] for chunk_id in chunk_ids if chunk_id in chunk_map
        ]

        return ordered_chunks

    @staticmethod
    def _normalize_query(query: str) -> str:
        """Lightweight normalization to improve embedding quality."""
        if not query:
            return ""
        text = query.strip()
        # Remove code fences
        text = re.sub(r"```[\s\S]*?```", " ", text)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text)
        return text[:4000]

    @staticmethod
    def _distance_to_similarity(distance: float) -> float:
        """
        Convert FAISS L2 distance into a pseudo similarity score in [0, 1].
        """
        # Small epsilon to avoid divide-by-zero
        return 1.0 / (1.0 + max(distance, 1e-8))
