import math

from services.embeddings.base import EMBEDDING_DIM
from services.embeddings.fake import FakeEmbedder


async def test_embed_is_768_dim_deterministic_and_unit():
    embedder = FakeEmbedder()
    first = (await embedder.embed(["def add(a, b): return a + b"]))[0]
    second = (await embedder.embed(["def add(a, b): return a + b"]))[0]
    other = (await embedder.embed(["def sub(a, b): return a - b"]))[0]

    assert len(first) == EMBEDDING_DIM
    assert first == second
    assert first != other
    assert abs(math.sqrt(sum(x * x for x in first)) - 1.0) < 1e-9


async def test_embed_empty_input():
    assert await FakeEmbedder().embed([]) == []
