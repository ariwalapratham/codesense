import pytest

from services.embeddings.base import EMBEDDING_DIM
from services.embeddings.gemini import GeminiEmbedder


def _embedder(monkeypatch, batch):
    embedder = GeminiEmbedder()
    embedder.client = object()

    async def fake_batch(texts, retry_count=0):
        return batch

    monkeypatch.setattr(embedder, "_embed_batch", fake_batch)
    return embedder


async def test_embed_normalizes_valid_vectors(monkeypatch):
    embedder = _embedder(monkeypatch, [[2.0] + [0.0] * (EMBEDDING_DIM - 1)])
    (vector,) = await embedder.embed(["x"])
    assert vector[0] == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [[], [1.0, 2.0], [0.0] * EMBEDDING_DIM])
async def test_embed_rejects_invalid_vectors(monkeypatch, bad):
    embedder = _embedder(monkeypatch, [bad])
    with pytest.raises(ValueError):
        await embedder.embed(["x"])
