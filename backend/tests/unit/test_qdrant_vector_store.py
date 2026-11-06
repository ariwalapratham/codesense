import pytest
from qdrant_client import AsyncQdrantClient

from services.embeddings.base import EMBEDDING_DIM
from services.vector_store.qdrant import DENSE_VECTOR, SPARSE_VECTOR, QdrantVectorStore
from tests.unit.test_memory_vector_store import _point, _unit


@pytest.fixture
async def store():
    client = AsyncQdrantClient(":memory:")
    vector_store = QdrantVectorStore(client=client, collection="codesense_chunks")
    await vector_store.ensure_collection()
    try:
        yield vector_store
    finally:
        await client.close()


async def test_collection_has_dense_and_sparse_bm25(store):
    info = await store._client.get_collection(store.collection)
    dense = info.config.params.vectors[DENSE_VECTOR]
    assert dense.size == EMBEDDING_DIM
    assert "Cosine" in str(dense.distance)
    assert SPARSE_VECTOR in info.config.params.sparse_vectors
    await store.ensure_collection()


async def test_upsert_and_search_returns_cosine_hit(store):
    await store.upsert(
        [_point(repo_id="r1", chunk_id="c1", file_path="a.py", vector=_unit(1, 0))]
    )
    hits = await store.search(_unit(1, 0), repo_id="r1", top_k=15)
    assert len(hits) == 1
    assert hits[0].chunk_id == "c1"
    assert hits[0].file_path == "a.py"
    assert hits[0].score >= 0.15


async def test_search_isolates_repo_id(store):
    shared = _unit(1, 0)
    await store.upsert(
        [
            _point(repo_id="r1", chunk_id="a", file_path="a.py", vector=shared),
            _point(repo_id="r2", chunk_id="b", file_path="b.py", vector=shared),
        ]
    )
    hits = await store.search(shared, repo_id="r1")
    assert [h.chunk_id for h in hits] == ["a"]


async def test_delete_by_path(store):
    await store.upsert(
        [
            _point(repo_id="r1", chunk_id="keep", file_path="keep.py", vector=_unit(1, 0)),
            _point(repo_id="r1", chunk_id="drop", file_path="drop.py", vector=_unit(0, 1)),
        ]
    )
    await store.delete_by_path("r1", "drop.py")
    hits = await store.search(_unit(1, 0), repo_id="r1", top_k=15)
    assert [h.chunk_id for h in hits] == ["keep"]


async def test_delete_by_repo(store):
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


async def test_delete_ids_leaves_other_points(store):
    keep = _point(repo_id="r1", chunk_id="keep", file_path="a.py", vector=_unit(1, 0))
    drop = _point(repo_id="r1", chunk_id="drop", file_path="b.py", vector=_unit(0, 1))
    await store.upsert([keep, drop])
    await store.delete_ids([drop.point_id])
    found = await store.retrieve([keep.point_id, drop.point_id])
    assert list(found) == [keep.point_id]


async def test_retrieve_named_dense_only(store):
    vec = _unit(1, 0)
    point = _point(repo_id="r1", chunk_id="c1", file_path="a.py", vector=vec)
    await store.upsert([point])
    found = await store.retrieve([point.point_id, "00000000-0000-0000-0000-000000000000"])
    assert list(found) == [point.point_id]
    assert len(found[point.point_id]) == EMBEDDING_DIM
    assert found[point.point_id][0] == pytest.approx(vec[0], abs=1e-5)


async def test_search_filters_path_prefix_and_language(store):
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
    by_path = await store.search(vec, repo_id="r1", path_prefix="backend/services")
    by_lang = await store.search(vec, repo_id="r1", language="typescript")
    assert [h.chunk_id for h in by_path] == ["py"]
    assert [h.chunk_id for h in by_lang] == ["ts"]


@pytest.mark.parametrize(
    "bad",
    [[1.0, 0.0], [0.0] * EMBEDDING_DIM, [float("nan")] + [0.0] * (EMBEDDING_DIM - 1)],
)
async def test_rejects_invalid_vectors(store, bad):
    with pytest.raises(ValueError):
        await store.upsert(
            [_point(repo_id="r1", chunk_id="x", file_path="a.py", vector=bad)]
        )
    with pytest.raises(ValueError):
        await store.search(bad, repo_id="r1")
