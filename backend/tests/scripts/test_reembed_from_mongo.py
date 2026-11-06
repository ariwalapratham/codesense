from datetime import datetime

from scripts.reembed_from_mongo import reembed_repo
from services.embeddings.fake import FakeEmbedder
from services.ingestion_service import index_chunks
from services.vector_store.memory import MemoryVectorStore
from tests.unit.test_hash_reuse import TrackingEmbedder, _chunk


class _FakeMongo:
    def __init__(self, repo_id: str, chunks: list[dict]) -> None:
        self.repo_id = repo_id
        self.repo = {
            "id": repo_id,
            "_id": repo_id,
            "commit_hash": "abc1234",
        }
        self.chunks = {c["chunk_id"]: dict(c) for c in chunks}

    async def get_repository(self, repo_id: str):
        return self.repo if repo_id == self.repo_id else None

    async def list_chunks_missing_embeddings(self, repo_id: str, *, limit: int = 100):
        missing = [
            dict(c)
            for c in self.chunks.values()
            if c.get("embedded_at") is None
        ]
        return missing[:limit]

    async def list_embedded_hash_index(self, repo_id: str):
        return {
            c["content_hash"]: c["point_id"]
            for c in self.chunks.values()
            if c.get("embedded_at") and c.get("content_hash") and c.get("point_id")
        }

    async def mark_chunks_embedded(self, chunks: list[dict]) -> bool:
        for chunk in chunks:
            if chunk.get("embedded_at"):
                stored = self.chunks[chunk["chunk_id"]]
                stored.update(
                    {
                        "embedded_at": chunk["embedded_at"],
                        "embedding_model": chunk.get("embedding_model"),
                        "point_id": chunk.get("point_id"),
                        "content_hash": chunk.get("content_hash"),
                    }
                )
        return True

    async def recount_embedded_chunks(self, repo_id: str) -> int:
        return sum(1 for c in self.chunks.values() if c.get("embedded_at"))


async def test_reembed_sets_embedded_at_and_skips_already_embedded():
    embedder = TrackingEmbedder()
    store = MemoryVectorStore()
    repo_id = "r1"
    already_text = "def keep():\n    return 0\n"
    missing_text = "def add():\n    return 1\n"

    already, _ = await index_chunks(
        [_chunk(chunk_id="keep", text=already_text)],
        repo_id=repo_id,
        embedder=embedder,
        vector_store=store,
        hash_to_point={},
        commit_hash="abc1234",
    )
    missing = _chunk(chunk_id="add", text=missing_text)
    mongo = _FakeMongo(
        repo_id,
        [
            {**already[0], "embedded_at": datetime.now()},
            missing,
        ],
    )
    calls_before = embedder.calls
    result = await reembed_repo(
        repo_id, embedder=embedder, vector_store=store, mongodb=mongo
    )
    assert embedder.calls == calls_before + 1
    assert result["embedded"] == 1
    assert mongo.chunks["add"]["embedded_at"] is not None
    assert mongo.chunks["keep"]["embedded_at"] is not None

    calls = embedder.calls
    again = await reembed_repo(
        repo_id, embedder=embedder, vector_store=store, mongodb=mongo
    )
    assert embedder.calls == calls
    assert again["embedded"] == 0


async def test_reembed_aborts_when_mark_fails():
    class _FailMongo(_FakeMongo):
        async def mark_chunks_embedded(self, chunks):
            return False

    embedder = TrackingEmbedder()
    store = MemoryVectorStore()
    missing = _chunk(chunk_id="add", text="def add():\n    return 1\n")
    mongo = _FailMongo("r1", [missing])
    try:
        await reembed_repo("r1", embedder=embedder, vector_store=store, mongodb=mongo)
    except RuntimeError as exc:
        assert "embedded_at" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


async def test_reembed_unknown_repo():
    try:
        await reembed_repo(
            "missing",
            embedder=FakeEmbedder(),
            vector_store=MemoryVectorStore(),
            mongodb=_FakeMongo("other", []),
        )
    except ValueError as exc:
        assert "not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")
