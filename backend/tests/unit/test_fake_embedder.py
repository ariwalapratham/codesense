from tests.fakes.embedder import EMBEDDING_DIM, FakeEmbedder


def test_embed_is_768_dim_and_deterministic():
    embedder = FakeEmbedder()
    first = embedder.embed("def add(a, b): return a + b")
    second = embedder.embed("def add(a, b): return a + b")
    other = embedder.embed("def sub(a, b): return a - b")

    assert len(first) == EMBEDDING_DIM
    assert first == second
    assert first != other
