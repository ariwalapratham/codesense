import hashlib
import uuid

from services.embeddings.hashing import content_hash
from services.vector_store.ids import POINT_NAMESPACE, make_point_id, path_prefixes


def test_path_prefixes_ancestors_only():
    assert path_prefixes("a/b/c.py") == ["a", "a/b"]
    assert path_prefixes("backend/services/rag_service.py") == [
        "backend",
        "backend/services",
    ]
    assert path_prefixes("main.py") == []
    assert path_prefixes("") == []


def test_point_id_is_deterministic_uuid5():
    first = make_point_id("repo-a", "chunk-1")
    assert first == make_point_id("repo-a", "chunk-1")
    assert first != make_point_id("repo-b", "chunk-1")
    assert first != make_point_id("repo-a", "chunk-2")
    assert first == str(uuid.uuid5(POINT_NAMESPACE, "repo-a:chunk-1"))


def test_content_hash_is_sha256_of_exact_text():
    assert content_hash("abc") == hashlib.sha256(b"abc").hexdigest()
    assert content_hash("abc") != content_hash("abd")
