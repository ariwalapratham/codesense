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


class Embedder(Protocol):
    model_name: str
    dim: int

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one L2-normalized vector per text. Empty input yields []."""
        ...
