"""Retrieval service for embeddings + chunk lookup."""

from typing import Dict, List, Tuple

from services.mongodb_service import MongoDBService
from services.vector_store_service import VectorStoreService


class RetrievalService:
    """Coordinates embedding generation, FAISS search, and chunk fetching."""

    def __init__(self):
        self.mongodb_service = MongoDBService()
        self.vector_store_service = VectorStoreService()
        # TODO: Inject embedding provider (Gemini embeddings client) when ready.
        self.embedding_client = None

    async def generate_query_embedding(self, query: str) -> List[float]:
        """
        Generate an embedding for the user query.

        Args:
            query: Raw user question.

        Returns:
            Embedding vector as a list of floats.
        """
        # TODO: Call Gemini Embeddings API and return the vector.
        raise NotImplementedError(
            "RetrievalService.generate_query_embedding is not implemented yet."
        )

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
        # TODO:
        #   * Use VectorStoreService to load/search FAISS index.
        #   * Normalize/convert similarity scores if needed.
        raise NotImplementedError(
            "RetrievalService.search_repository_chunks is not implemented yet."
        )

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
        # TODO:
        #   * Query MongoDB for the provided chunk_ids.
        #   * Ensure each chunk belongs to repo_id (safety filter).
        #   * Preserve order based on similarity scores if needed.
        raise NotImplementedError(
            "RetrievalService.fetch_chunk_metadata is not implemented yet."
        )
