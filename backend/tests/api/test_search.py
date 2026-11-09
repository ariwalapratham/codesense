from bson import ObjectId

from services.embeddings.fake import FakeEmbedder
from services.ingestion_service import index_chunks
from services.retrieval_service import RetrievalService
from services.vector_store.memory import MemoryVectorStore


def test_search_unknown_repo_returns_not_found(client, auth_headers):
    response = client.get(
        "/repository/507f1f77bcf86cd799439011/search",
        params={"q": "def add"},
        headers=auth_headers,
    )
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["request_id"] == response.headers.get("x-request-id")


def test_search_empty_query_returns_400(client, auth_headers, auth_user):
    from datetime import datetime, timezone

    from pymongo import MongoClient

    from config.settings import settings

    repo_id = ObjectId()
    mongo = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    db = mongo[settings.db_name]
    try:
        db["csRepositories"].insert_one(
            {
                "_id": repo_id,
                "owner": "acme",
                "repo_name": "search-empty",
                "repository_url": "https://github.com/acme/search-empty.git",
                "branch": "main",
                "status": "completed",
                "owner_user_id": None,
                "visibility": "public",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        db["csUserRepositories"].insert_one(
            {"user_id": auth_user["_id"], "repo_id": repo_id, "role": "member"}
        )
        response = client.get(
            f"/repository/{repo_id}/search",
            params={"q": "   "},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation_error"
    finally:
        db["csUserRepositories"].delete_many({"repo_id": repo_id})
        db["csRepositories"].delete_one({"_id": repo_id})
        mongo.close()


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length):
        return self._docs


class _Chunks:
    def __init__(self, docs):
        self._docs = docs

    def find(self, query):
        ids = set(query.get("chunk_id", {}).get("$in", []))
        return _Cursor([doc for doc in self._docs if doc["chunk_id"] in ids])


class _Mongo:
    def __init__(self, docs):
        self.chunks = _Chunks(docs)


async def test_search_with_text_uses_mongo_code_text():
    repo_id = str(ObjectId())
    text = "def add(a, b): return a + b"
    store = MemoryVectorStore()
    embedder = FakeEmbedder()
    chunk = {
        "chunk_id": "math/add.py_func_0",
        "code_text": text,
        "file_path": "math/add.py",
        "language": "python",
        "start_line": 1,
        "end_line": 2,
        "function_name": "add",
        "class_name": None,
    }
    filled, _ = await index_chunks(
        [chunk],
        repo_id=repo_id,
        embedder=embedder,
        vector_store=store,
        hash_to_point={},
        commit_hash="abc1234",
    )
    mongo_doc = {
        "chunk_id": filled[0]["chunk_id"],
        "repo_id": ObjectId(repo_id),
        "file_path": "math/add.py",
        "function_name": "add",
        "class_name": None,
        "language": "python",
        "start_line": 1,
        "end_line": 2,
        "code_text": text,
    }
    retrieval = RetrievalService(embedder=embedder, vector_store=store)
    retrieval.mongodb_service = _Mongo([mongo_doc])

    results = await retrieval.search_with_text(repo_id, text, top_k=10)
    assert results
    hit = results[0]
    assert hit["chunk_id"] == "math/add.py_func_0"
    assert hit["code_text"] == text
    assert hit["file_path"] == "math/add.py"
    assert hit["score"] >= 0.15
    assert "vector" not in hit
