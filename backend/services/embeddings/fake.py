"""Deterministic embedder for tests. No Gemini calls."""

from __future__ import annotations

import hashlib
from typing import Sequence

from services.embeddings.base import EMBEDDING_DIM, l2_normalize


class FakeEmbedder:
    model_name = "fake"
    dim = EMBEDDING_DIM

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [l2_normalize(self._raw(text)) for text in texts]

    def _raw(self, text: str) -> list[float]:
        values: list[float] = []
        block = hashlib.sha256(text.encode("utf-8")).digest()
        while len(values) < self.dim:
            for offset in range(0, len(block), 4):
                n = int.from_bytes(block[offset : offset + 4], "big")
                values.append((n / 0xFFFFFFFF) * 2.0 - 1.0)
                if len(values) >= self.dim:
                    break
            block = hashlib.sha256(block).digest()
        return values[: self.dim]
