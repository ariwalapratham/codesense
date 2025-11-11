"""Vector store service for managing FAISS indices and embeddings."""

import logging
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np

from config.settings import settings

# Setup logger
logger = logging.getLogger(__name__)


class VectorStoreService:
    """Service for managing FAISS vector stores and embeddings."""

    def __init__(self):
        """Initialize vector store service with configuration."""
        # Get vector_store_path from settings
        self.vector_store_path = Path(settings.vector_store_path)
        self.vector_store_path.mkdir(exist_ok=True, parents=True)
        
        # Cache for loaded indices (repo_id -> index)
        self._index_cache: Dict[str, faiss.Index] = {}
        
        # Cache for chunk_id mappings (repo_id -> {chunk_id: index_position})
        self._chunk_mappings: Dict[str, Dict[str, int]] = {}
        
        # Embedding dimension (Gemini embedding-001 produces 768-dimensional vectors)
        # This will be determined from the first embedding
        self.embedding_dim: Optional[int] = None
        
        # Expected embedding dimension for Gemini embedding-001
        self.expected_embedding_dim = 768
        
        logger.info("Vector store service initialized at: %s", self.vector_store_path)

    def _get_index_path(self, repo_id: str) -> Path:
        """
        Get the path to the FAISS index file for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            Path to the index file
        """
        return self.vector_store_path / f"{repo_id}.faiss"

    def _get_mapping_path(self, repo_id: str) -> Path:
        """
        Get the path to the chunk_id mapping file for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            Path to the mapping file
        """
        return self.vector_store_path / f"{repo_id}.pkl"

    def _create_index(self, embedding_dim: int, use_ivf: bool = False) -> faiss.Index:
        """
        Create a new FAISS index.

        Args:
            embedding_dim: Dimension of the embedding vectors
            use_ivf: Whether to use IndexIVFFlat (for large datasets) or IndexFlatL2 (default)

        Returns:
            FAISS index instance
        """
        if use_ivf:
            # IndexIVFFlat: More efficient for large datasets (>10k vectors)
            # Requires training before adding vectors
            quantizer = faiss.IndexFlatL2(embedding_dim)
            # nlist: number of clusters (typically sqrt of dataset size)
            nlist = min(100, max(10, int(np.sqrt(10000))))  # Adaptive based on expected size
            index = faiss.IndexIVFFlat(quantizer, embedding_dim, nlist)
            logger.debug("Created IndexIVFFlat with %d clusters", nlist)
        else:
            # IndexFlatL2: Simple, exact search, good for small-medium datasets
            index = faiss.IndexFlatL2(embedding_dim)
            logger.debug("Created IndexFlatL2")
        
        return index

    def _load_index(self, repo_id: str) -> Optional[faiss.Index]:
        """
        Load a FAISS index from disk.

        Args:
            repo_id: Repository identifier

        Returns:
            FAISS index if found, None otherwise
        """
        index_path = self._get_index_path(repo_id)
        
        if not index_path.exists():
            return None
        
        try:
            index = faiss.read_index(str(index_path))
            logger.debug("Loaded FAISS index for repository: %s", repo_id)
            return index
        except Exception as e:
            logger.error("Failed to load FAISS index for %s: %s", repo_id, e)
            return None

    def _save_index(self, repo_id: str, index: faiss.Index) -> bool:
        """
        Save a FAISS index to disk.

        Args:
            repo_id: Repository identifier
            index: FAISS index to save

        Returns:
            True if successful, False otherwise
        """
        index_path = self._get_index_path(repo_id)
        
        try:
            faiss.write_index(index, str(index_path))
            logger.debug("Saved FAISS index for repository: %s", repo_id)
            return True
        except Exception as e:
            logger.error("Failed to save FAISS index for %s: %s", repo_id, e)
            return False

    def _load_chunk_mapping(self, repo_id: str) -> Dict[str, int]:
        """
        Load chunk_id to index position mapping from disk.

        Args:
            repo_id: Repository identifier

        Returns:
            Dictionary mapping chunk_id to FAISS index position
        """
        mapping_path = self._get_mapping_path(repo_id)
        
        if not mapping_path.exists():
            return {}
        
        try:
            with open(mapping_path, "rb") as f:
                mapping = pickle.load(f)
            logger.debug("Loaded chunk mapping for repository: %s (%d entries)", repo_id, len(mapping))
            return mapping
        except Exception as e:
            logger.error("Failed to load chunk mapping for %s: %s", repo_id, e)
            return {}

    def _save_chunk_mapping(self, repo_id: str, mapping: Dict[str, int]) -> bool:
        """
        Save chunk_id to index position mapping to disk.

        Args:
            repo_id: Repository identifier
            mapping: Dictionary mapping chunk_id to FAISS index position

        Returns:
            True if successful, False otherwise
        """
        mapping_path = self._get_mapping_path(repo_id)
        
        try:
            with open(mapping_path, "wb") as f:
                pickle.dump(mapping, f)
            logger.debug("Saved chunk mapping for repository: %s (%d entries)", repo_id, len(mapping))
            return True
        except Exception as e:
            logger.error("Failed to save chunk mapping for %s: %s", repo_id, e)
            return False

    def _get_or_create_index(self, repo_id: str, embedding_dim: int) -> Tuple[faiss.Index, bool]:
        """
        Get existing index or create a new one.

        Args:
            repo_id: Repository identifier
            embedding_dim: Dimension of embedding vectors

        Returns:
            Tuple of (index, is_new_index)
        """
        # Check cache first
        if repo_id in self._index_cache:
            return self._index_cache[repo_id], False
        
        # Try to load from disk
        index = self._load_index(repo_id)
        if index is not None:
            self._index_cache[repo_id] = index
            return index, False
        
        # Create new index
        # Use IndexFlatL2 for simplicity (can switch to IndexIVFFlat for large datasets)
        # For solo dev, IndexFlatL2 is sufficient and simpler
        use_ivf = False  # Set to True if you have >10k vectors per repo
        index = self._create_index(embedding_dim, use_ivf=use_ivf)
        self._index_cache[repo_id] = index
        return index, True

    def save_embeddings(
        self,
        embedded_chunks: List[Dict],
        repo_id: str,
        overwrite: bool = False
    ) -> bool:
        """
        Save embeddings to FAISS index for a repository.

        Args:
            embedded_chunks: List of chunks with embeddings
            repo_id: Repository identifier
            overwrite: Whether to overwrite existing index (default: False, append)

        Returns:
            True if successful, False otherwise
        """
        if not embedded_chunks:
            logger.warning("No embedded chunks provided for repository: %s", repo_id)
            return False

        # Filter chunks with valid embeddings
        valid_chunks = [
            chunk for chunk in embedded_chunks
            if chunk.get("embedding") is not None
        ]

        if not valid_chunks:
            logger.warning("No chunks with valid embeddings for repository: %s", repo_id)
            return False

        # Determine embedding dimension from first chunk
        first_embedding = valid_chunks[0]["embedding"]
        if not isinstance(first_embedding, (list, np.ndarray)):
            logger.error("Invalid embedding format for repository: %s", repo_id)
            return False

        embedding_dim = len(first_embedding)
        
        # Validate embedding dimension
        if embedding_dim != self.expected_embedding_dim:
            logger.warning(
                "Unexpected embedding dimension: %d (expected %d for Gemini embedding-001). "
                "This may indicate a different model or API change.",
                embedding_dim, self.expected_embedding_dim
            )
        
        if self.embedding_dim is None:
            self.embedding_dim = embedding_dim
        elif self.embedding_dim != embedding_dim:
            logger.error(
                "Embedding dimension mismatch: expected %d, got %d",
                self.embedding_dim, embedding_dim
            )
            return False

        try:
            # Get or create index
            index, is_new = self._get_or_create_index(repo_id, embedding_dim)
            
            # Load existing chunk mapping
            if overwrite or is_new:
                chunk_mapping = {}
                if not is_new and overwrite:
                    # Clear existing index
                    index.reset()
            else:
                chunk_mapping = self._load_chunk_mapping(repo_id)

            # Prepare embeddings as numpy array
            embeddings = []
            chunk_ids = []
            
            for chunk in valid_chunks:
                embedding = chunk["embedding"]
                if isinstance(embedding, list):
                    embedding = np.array(embedding, dtype=np.float32)
                elif not isinstance(embedding, np.ndarray):
                    continue
                
                # Ensure correct shape and type
                embedding = embedding.reshape(1, -1).astype(np.float32)
                
                # Validate embedding (check for NaN/Inf)
                if np.any(np.isnan(embedding)) or np.any(np.isinf(embedding)):
                    logger.warning(
                        "Invalid embedding detected (NaN/Inf) for chunk %s, skipping",
                        chunk.get("chunk_id")
                    )
                    continue
                
                # Normalize embedding for better L2 distance performance
                # Normalization makes L2 distance behave more like cosine similarity
                norm = np.linalg.norm(embedding)
                if norm > 0:
                    embedding = embedding / norm
                else:
                    logger.warning(
                        "Zero-norm embedding detected for chunk %s, skipping",
                        chunk.get("chunk_id")
                    )
                    continue
                
                embeddings.append(embedding)
                chunk_ids.append(chunk["chunk_id"])

            if not embeddings:
                logger.error("No valid embeddings to save for repository: %s", repo_id)
                return False

            # Stack embeddings into single array
            embeddings_array = np.vstack(embeddings)

            # Train index if using IVFFlat and it's new
            if isinstance(index, faiss.IndexIVFFlat) and is_new:
                if len(embeddings_array) < index.nlist:
                    logger.warning(
                        "Not enough vectors (%d) to train IVFFlat index (needs %d). "
                        "Switching to IndexFlatL2.",
                        len(embeddings_array), index.nlist
                    )
                    # Switch to flat index
                    index = self._create_index(embedding_dim, use_ivf=False)
                    self._index_cache[repo_id] = index
                else:
                    logger.info("Training IVFFlat index with %d vectors", len(embeddings_array))
                    index.train(embeddings_array)

            # Add embeddings to index
            index.add(embeddings_array)
            
            # Update chunk mapping
            start_idx = index.ntotal - len(chunk_ids)
            for idx, chunk_id in enumerate(chunk_ids):
                chunk_mapping[chunk_id] = start_idx + idx

            # Save index and mapping
            index_saved = self._save_index(repo_id, index)
            mapping_saved = self._save_chunk_mapping(repo_id, chunk_mapping)
            
            if not (index_saved and mapping_saved):
                logger.error("Failed to save index or mapping for repository: %s", repo_id)
                return False

            # Update cache
            self._chunk_mappings[repo_id] = chunk_mapping

            logger.info(
                "Saved %d embeddings to FAISS index for repository: %s (total: %d)",
                len(embeddings_array), repo_id, index.ntotal
            )
            return True

        except Exception as e:
            logger.error("Failed to save embeddings for repository %s: %s", repo_id, e, exc_info=True)
            return False

    def search_similar(
        self,
        query_embedding: np.ndarray,
        repo_id: str,
        top_k: int = 10
    ) -> List[Tuple[int, float]]:
        """
        Search for similar vectors in a repository's index.

        Args:
            query_embedding: Query embedding vector
            repo_id: Repository identifier
            top_k: Number of top results to return

        Returns:
            List of tuples (index_position, distance) sorted by distance
        """
        # Get index
        index = self._load_index(repo_id)
        if index is None:
            logger.warning("Index not found for repository: %s", repo_id)
            return []

        # Ensure query embedding is correct format
        if isinstance(query_embedding, list):
            query_embedding = np.array(query_embedding, dtype=np.float32)
        
        query_embedding = query_embedding.reshape(1, -1).astype(np.float32)
        
        # Validate query embedding (check for NaN/Inf)
        if np.any(np.isnan(query_embedding)) or np.any(np.isinf(query_embedding)):
            logger.error("Invalid query embedding detected (NaN/Inf)")
            return []
        
        # Normalize query embedding (must match stored embeddings)
        norm = np.linalg.norm(query_embedding)
        if norm > 0:
            query_embedding = query_embedding / norm
        else:
            logger.error("Zero-norm query embedding detected")
            return []

        # Check dimension match
        if query_embedding.shape[1] != index.d:
            logger.error(
                "Embedding dimension mismatch: query %d, index %d",
                query_embedding.shape[1], index.d
            )
            return []

        try:
            # Search
            distances, indices = index.search(query_embedding, min(top_k, index.ntotal))
            
            # Convert to list of tuples
            results = [
                (int(idx), float(dist))
                for idx, dist in zip(indices[0], distances[0])
                if idx >= 0  # FAISS returns -1 for invalid results
            ]
            
            return results
        except Exception as e:
            logger.error("Failed to search in index for repository %s: %s", repo_id, e)
            return []

    def get_chunk_id_from_index(self, repo_id: str, index_position: int) -> Optional[str]:
        """
        Get chunk_id from FAISS index position.

        Args:
            repo_id: Repository identifier
            index_position: Position in FAISS index

        Returns:
            chunk_id if found, None otherwise
        """
        # Load mapping if not in cache
        if repo_id not in self._chunk_mappings:
            self._chunk_mappings[repo_id] = self._load_chunk_mapping(repo_id)
        
        mapping = self._chunk_mappings[repo_id]
        
        # Reverse lookup: find chunk_id by index_position
        for chunk_id, idx_pos in mapping.items():
            if idx_pos == index_position:
                return chunk_id
        
        return None

    def delete_index(self, repo_id: str) -> bool:
        """
        Delete FAISS index and mapping for a repository.

        Args:
            repo_id: Repository identifier

        Returns:
            True if successful, False otherwise
        """
        try:
            index_path = self._get_index_path(repo_id)
            mapping_path = self._get_mapping_path(repo_id)
            
            # Remove from cache
            if repo_id in self._index_cache:
                del self._index_cache[repo_id]
            if repo_id in self._chunk_mappings:
                del self._chunk_mappings[repo_id]
            
            # Delete files
            deleted = False
            if index_path.exists():
                index_path.unlink()
                deleted = True
            if mapping_path.exists():
                mapping_path.unlink()
                deleted = True
            
            if deleted:
                logger.info("Deleted index and mapping for repository: %s", repo_id)
            else:
                logger.warning("No index found to delete for repository: %s", repo_id)
            
            return deleted
        except Exception as e:
            logger.error("Failed to delete index for repository %s: %s", repo_id, e)
            return False

    def get_index_info(self, repo_id: str) -> Optional[Dict]:
        """
        Get information about a repository's index.

        Args:
            repo_id: Repository identifier

        Returns:
            Dictionary with index information or None if not found
        """
        index = self._load_index(repo_id)
        if index is None:
            return None
        
        mapping = self._load_chunk_mapping(repo_id)
        
        return {
            "repo_id": repo_id,
            "total_vectors": index.ntotal,
            "embedding_dim": index.d,
            "index_type": type(index).__name__,
            "mapped_chunks": len(mapping),
            "is_trained": index.is_trained if hasattr(index, "is_trained") else True
        }
