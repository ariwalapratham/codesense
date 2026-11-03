"""Deterministic 768-dim embedder for tests. No Gemini calls."""

from __future__ import annotations

import hashlib

EMBEDDING_DIM = 768


class FakeEmbedder:
    """Hash text into a stable 768-dim vector. Not L2-normalized."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
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
