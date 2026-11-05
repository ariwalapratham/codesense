from bson import ObjectId

from scripts.backfill_v2 import (
    LEGACY_USER_ID,
    SCHEMA_VERSION,
    chunk_patch,
    ingestion_patch,
    repo_patch,
    session_patch,
)
from services.embeddings.hashing import content_hash
from services.vector_store.ids import make_point_id


def test_repo_patch_fills_missing_and_is_idempotent():
    doc = {"owner": "acme", "repo_name": "demo"}
    patch = repo_patch(doc, embedding_model="gemini-embedding-001", embedding_dim=768)
    assert patch == {
        "branch": "main",
        "visibility": "public",
        "owner_user_id": None,
        "embedding_model": "gemini-embedding-001",
        "embedding_dim": 768,
        "schema_version": SCHEMA_VERSION,
    }
    filled = {**doc, **patch}
    assert repo_patch(
        filled, embedding_model="other", embedding_dim=1
    ) == {}


def test_chunk_patch_hashes_text_and_keeps_embedded_at_null():
    repo_id = ObjectId("669999999999999999999999")
    doc = {"chunk_id": "src/main.py_func_0", "repo_id": repo_id, "code_text": "def add():\n    return 1\n"}
    patch = chunk_patch(doc)
    assert patch["content_hash"] == content_hash(doc["code_text"])
    assert patch["point_id"] == make_point_id(str(repo_id), doc["chunk_id"])
    assert patch["embedded_at"] is None
    assert patch["schema_version"] == SCHEMA_VERSION
    assert chunk_patch({**doc, **patch}) == {}


def test_chunk_patch_does_not_clear_existing_embedded_at():
    doc = {
        "chunk_id": "c1",
        "repo_id": "r1",
        "code_text": "x",
        "content_hash": content_hash("x"),
        "point_id": make_point_id("r1", "c1"),
        "embedded_at": "already-set",
        "schema_version": SCHEMA_VERSION,
    }
    assert chunk_patch(doc) == {}


def test_session_and_ingestion_patches():
    assert session_patch({}) == {
        "user_id": LEGACY_USER_ID,
        "schema_version": SCHEMA_VERSION,
    }
    assert session_patch(
        {"user_id": LEGACY_USER_ID, "schema_version": SCHEMA_VERSION}
    ) == {}
    assert ingestion_patch({}) == {"type": "full", "trigger": "user"}
    assert ingestion_patch({"type": "full", "trigger": "user"}) == {}
