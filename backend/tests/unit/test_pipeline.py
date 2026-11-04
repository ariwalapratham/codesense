from services.embeddings.fake import FakeEmbedder
from services.vector_store.base import VectorPoint
from services.vector_store.ids import make_point_id, path_prefixes
from services.vector_store.memory import MemoryVectorStore

REPO_ID = "6720abc"


async def test_fake_embedder_and_memory_store_retrieve_matching_chunk():
    embedder = FakeEmbedder()
    store = MemoryVectorStore()
    await store.ensure_collection()
    await store.ping()

    chunks = [
        ("add", "math/add.py", "def add(a, b):\n    return a + b\n"),
        ("http", "api/routes.py", "async def ingest(payload):\n    return payload\n"),
        ("parse", "parser/tree.py", "def parse_file(path):\n    return []\n"),
    ]
    vectors = await embedder.embed([text for _, _, text in chunks])
    points = [
        VectorPoint(
            point_id=make_point_id(REPO_ID, chunk_id),
            vector=tuple(vector),
            repo_id=REPO_ID,
            chunk_id=chunk_id,
            file_path=file_path,
            path_prefixes=tuple(path_prefixes(file_path)),
            language="python",
            function_name=chunk_id,
            start_line=1,
            end_line=2,
            commit_hash="abc1234",
        )
        for (chunk_id, file_path, _), vector in zip(chunks, vectors)
    ]
    await store.upsert(points)

    query = (await embedder.embed([chunks[0][2]]))[0]
    hits = await store.search(query, repo_id=REPO_ID, top_k=15)

    assert hits
    assert hits[0].chunk_id == "add"
    assert hits[0].file_path == "math/add.py"
    assert hits[0].score >= 0.15
    assert hits[0].point_id == make_point_id(REPO_ID, "add")
