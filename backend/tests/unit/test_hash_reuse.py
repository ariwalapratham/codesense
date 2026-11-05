from services.embeddings.fake import FakeEmbedder
from services.embeddings.hashing import content_hash
from services.ingestion_service import (
    chunks_to_points,
    index_chunks,
    load_reused_vectors,
    reuse_or_embed,
)
from services.vector_store.memory import MemoryVectorStore


class TrackingEmbedder(FakeEmbedder):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.texts: list[str] = []

    async def embed(self, texts):
        self.calls += 1
        self.texts.extend(texts)
        return await super().embed(texts)


def _chunk(*, chunk_id: str, text: str, start_line: int = 1, end_line: int = 2) -> dict:
    return {
        "chunk_id": chunk_id,
        "code_text": text,
        "file_path": "a.py",
        "language": "python",
        "start_line": start_line,
        "end_line": end_line,
        "function_name": "f",
        "class_name": None,
    }


async def test_same_hash_skips_embed():
    embedder = TrackingEmbedder()
    text = "def f():\n    return 1\n"
    first = await reuse_or_embed(
        [_chunk(chunk_id="c1", text=text, start_line=1, end_line=2)],
        repo_id="r1",
        embedder=embedder,
        hash_to_vector={},
    )
    assert embedder.calls == 1
    reused = {first[0]["content_hash"]: first[0]["embedding"]}
    second = await reuse_or_embed(
        [_chunk(chunk_id="c2", text=text, start_line=10, end_line=11)],
        repo_id="r1",
        embedder=embedder,
        hash_to_vector=dict(reused),
    )
    assert embedder.calls == 1
    assert second[0]["embedding"] == first[0]["embedding"]
    assert second[0]["start_line"] == 10
    assert second[0]["point_id"] != first[0]["point_id"]
    assert second[0]["content_hash"] == content_hash(text)


async def test_line_change_updates_payload_only():
    embedder = TrackingEmbedder()
    store = MemoryVectorStore()
    text = "def f():\n    return 1\n"
    first, _ = await index_chunks(
        [_chunk(chunk_id="c1", text=text, start_line=1, end_line=2)],
        repo_id="r1",
        embedder=embedder,
        vector_store=store,
        hash_to_point={},
        commit_hash="abc1234",
    )
    hash_to_point = {first[0]["content_hash"]: first[0]["point_id"]}
    second, embedded = await index_chunks(
        [_chunk(chunk_id="c2", text=text, start_line=10, end_line=11)],
        repo_id="r1",
        embedder=embedder,
        vector_store=store,
        hash_to_point=hash_to_point,
        commit_hash="abc1234",
        stale_point_ids=[first[0]["point_id"]],
    )
    assert embedder.calls == 1
    assert embedded == 1
    hits = await store.search(second[0]["embedding"], repo_id="r1", top_k=5)
    assert len(hits) == 1
    assert hits[0].chunk_id == "c2"
    assert hits[0].start_line == 10
    assert await store.retrieve([first[0]["point_id"]]) == {}


async def test_load_reused_vectors_omits_missing_points():
    store = MemoryVectorStore()
    embedder = FakeEmbedder()
    chunk = _chunk(chunk_id="c1", text="x = 1\n")
    filled = await reuse_or_embed(
        [chunk], repo_id="r1", embedder=embedder, hash_to_vector={}
    )
    await store.upsert(chunks_to_points(filled, repo_id="r1", commit_hash="abc"))
    found = await load_reused_vectors(
        store,
        {
            filled[0]["content_hash"]: filled[0]["point_id"],
            "dead": "00000000-0000-4000-8000-000000000000",
        },
    )
    assert list(found) == [filled[0]["content_hash"]]


async def test_reuse_or_embed_rejects_short_batch():
    class _Short(FakeEmbedder):
        async def embed(self, texts):
            return []

    try:
        await reuse_or_embed(
            [_chunk(chunk_id="c1", text="x = 1\n")],
            repo_id="r1",
            embedder=_Short(),
            hash_to_vector={},
        )
    except RuntimeError as exc:
        assert "vectors" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
