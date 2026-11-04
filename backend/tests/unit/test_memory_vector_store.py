import pytest

from services.embeddings.base import EMBEDDING_DIM
from services.vector_store.base import VectorPoint
from services.vector_store.ids import make_point_id, path_prefixes
from services.vector_store.memory import MemoryVectorStore


def _point(
    *,
    repo_id: str,
    chunk_id: str,
    file_path: str,
    vector: list[float],
    language: str = "python",
) -> VectorPoint:
    return VectorPoint(
        point_id=make_point_id(repo_id, chunk_id),
        vector=tuple(vector),
        repo_id=repo_id,
        chunk_id=chunk_id,
        file_path=file_path,
        path_prefixes=tuple(path_prefixes(file_path)),
        language=language,
        start_line=1,
        end_line=2,
        commit_hash="abc1234",
    )


def _unit(x: float, y: float) -> list[float]:
    norm = (x * x + y * y) ** 0.5
    return [x / norm, y / norm] + [0.0] * (EMBEDDING_DIM - 2)


async def test_upsert_and_search_returns_cosine_hit():
    store = MemoryVectorStore()
    await store.ensure_collection()
    await store.upsert(
        [_point(repo_id="r1", chunk_id="c1", file_path="a.py", vector=_unit(1, 0))]
    )
    hits = await store.search(_unit(1, 0), repo_id="r1", top_k=15)
    assert len(hits) == 1
    assert hits[0].chunk_id == "c1"
    assert hits[0].file_path == "a.py"
    assert hits[0].score >= 0.15


async def test_search_isolates_repo_id():
    store = MemoryVectorStore()
    shared = _unit(1, 0)
    await store.upsert(
        [
            _point(repo_id="r1", chunk_id="a", file_path="a.py", vector=shared),
            _point(repo_id="r2", chunk_id="b", file_path="b.py", vector=shared),
        ]
    )
    hits = await store.search(shared, repo_id="r1")
    assert [h.chunk_id for h in hits] == ["a"]


async def test_delete_by_path():
    store = MemoryVectorStore()
    await store.upsert(
        [
            _point(repo_id="r1", chunk_id="keep", file_path="keep.py", vector=_unit(1, 0)),
            _point(repo_id="r1", chunk_id="drop", file_path="drop.py", vector=_unit(0, 1)),
        ]
    )
    await store.delete_by_path("r1", "drop.py")
    hits = await store.search(_unit(1, 0), repo_id="r1", top_k=15)
    assert [h.chunk_id for h in hits] == ["keep"]


async def test_delete_by_repo():
    store = MemoryVectorStore()
    vec = _unit(1, 0)
    await store.upsert(
        [
            _point(repo_id="r1", chunk_id="gone", file_path="a.py", vector=vec),
            _point(repo_id="r2", chunk_id="stay", file_path="a.py", vector=vec),
        ]
    )
    await store.delete_by_repo("r1")
    assert await store.search(vec, repo_id="r1") == []
    assert [h.chunk_id for h in await store.search(vec, repo_id="r2")] == ["stay"]


async def test_search_filters_path_prefix_and_language():
    store = MemoryVectorStore()
    vec = _unit(1, 0)
    await store.upsert(
        [
            _point(
                repo_id="r1",
                chunk_id="py",
                file_path="backend/services/rag.py",
                vector=vec,
                language="python",
            ),
            _point(
                repo_id="r1",
                chunk_id="ts",
                file_path="frontend/app.ts",
                vector=vec,
                language="typescript",
            ),
        ]
    )
    by_path = await store.search(
        vec, repo_id="r1", path_prefix="backend/services"
    )
    by_lang = await store.search(vec, repo_id="r1", language="typescript")
    assert [h.chunk_id for h in by_path] == ["py"]
    assert [h.chunk_id for h in by_lang] == ["ts"]


@pytest.mark.parametrize(
    "bad",
    [[1.0, 0.0], [0.0] * EMBEDDING_DIM, [float("nan")] + [0.0] * (EMBEDDING_DIM - 1)],
)
async def test_rejects_invalid_vectors(bad):
    store = MemoryVectorStore()
    with pytest.raises(ValueError):
        await store.upsert([_point(repo_id="r1", chunk_id="x", file_path="a.py", vector=bad)])
    with pytest.raises(ValueError):
        await store.search(bad, repo_id="r1")
