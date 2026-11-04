"""Embedder protocol and shared vector helpers."""

from typing import Protocol, Sequence

import numpy as np

EMBEDDING_DIM = 768


def l2_normalize(vector: Sequence[float]) -> list[float]:
    """Return an L2-unit vector. Zero input is returned unchanged."""
    if not vector:
        return []
    arr = np.asarray(vector, dtype=np.float64)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return arr.tolist()
    return (arr / norm).tolist()


def check_vector(vector: Sequence[float], dim: int = EMBEDDING_DIM) -> np.ndarray:
    """Return the vector as an array, or raise ValueError if it is not a finite nonzero `dim`-vector."""
    arr = np.asarray(vector, dtype=np.float64)
    if arr.shape != (dim,) or not np.isfinite(arr).all() or not arr.any():
        raise ValueError(f"Invalid embedding: expected a finite nonzero {dim}-d vector")
    return arr


class Embedder(Protocol):
    model_name: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one L2-normalized vector per text. Empty input yields []."""
        ...
