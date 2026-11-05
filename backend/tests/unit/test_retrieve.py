from tests.unit.test_memory_vector_store import _point, _unit
from services.vector_store.ids import make_point_id
from services.vector_store.memory import MemoryVectorStore


async def test_retrieve_returns_vectors_and_omits_missing():
    store = MemoryVectorStore()
    vec = _unit(1, 0)
    point = _point(repo_id="r1", chunk_id="c1", file_path="a.py", vector=vec)
    await store.upsert([point])

    found = await store.retrieve([point.point_id, "missing-id"])
    assert list(found) == [point.point_id]
    assert found[point.point_id] == vec


async def test_retrieve_empty_ids():
    store = MemoryVectorStore()
    assert await store.retrieve([]) == {}


async def test_retrieve_after_delete():
    store = MemoryVectorStore()
    point = _point(repo_id="r1", chunk_id="c1", file_path="a.py", vector=_unit(1, 0))
    await store.upsert([point])
    await store.delete_by_repo("r1")
    assert await store.retrieve([make_point_id("r1", "c1")]) == {}
