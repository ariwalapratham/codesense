from datetime import datetime
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient

from config.settings import settings

_OWNER = f"cs-ingest-jobs-{uuid4().hex[:8]}"


def _collection():
    client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    return client, client[settings.db_name]["csIngestionRequests"]


def _insert_job(coll, **fields):
    now = datetime.now()
    doc = {
        "owner": _OWNER,
        "repo_name": "repo",
        "repository_url": "https://github.com/cs-ingest-jobs/repo",
        "branch": "main",
        "status": "pending",
        "retry_count": 0,
        "created_at": now,
        "updated_at": now,
        "type": "full",
        "trigger": "user",
        "target_commit": "a1b2c3d",
        "cleanup": True,
        "progress": {
            "stage": "queued",
            "files_total": 0,
            "files_done": 0,
            "chunks_total": 0,
            "chunks_embedded": 0,
        },
    }
    doc.update(fields)
    return str(coll.insert_one(doc).inserted_id)


def test_status_includes_progress(client):
    mongo_client, coll = _collection()
    request_id = None
    try:
        request_id = _insert_job(
            coll,
            progress={
                "stage": "queued",
                "files_total": 12,
                "files_done": 0,
                "chunks_total": 0,
                "chunks_embedded": 0,
            },
        )
        response = client.get(f"/repository/ingest/status/{request_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["request_id"] == request_id
        assert body["status"] == "pending"
        assert body["type"] == "full"
        assert body["progress"] == {
            "stage": "queued",
            "files_total": 12,
            "files_done": 0,
            "chunks_total": 0,
            "chunks_embedded": 0,
        }
    finally:
        if request_id:
            coll.delete_one({"_id": ObjectId(request_id)})
        mongo_client.close()


def test_cancel_pending_job(client):
    mongo_client, coll = _collection()
    request_id = None
    try:
        request_id = _insert_job(coll)
        response = client.post(f"/repository/ingest/{request_id}/cancel")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "cancelled"
        assert body["progress"]["stage"] == "queued"
        stored = coll.find_one({"_id": ObjectId(request_id)})
        assert stored["status"] == "cancelled"
    finally:
        if request_id:
            coll.delete_one({"_id": ObjectId(request_id)})
        mongo_client.close()


def test_cancel_processing_job_returns_conflict(client):
    mongo_client, coll = _collection()
    request_id = None
    try:
        request_id = _insert_job(coll, status="processing")
        response = client.post(f"/repository/ingest/{request_id}/cancel")
        assert response.status_code == 409
        error = response.json()["error"]
        assert error["code"] == "conflict"
        stored = coll.find_one({"_id": ObjectId(request_id)})
        assert stored["status"] == "processing"
    finally:
        if request_id:
            coll.delete_one({"_id": ObjectId(request_id)})
        mongo_client.close()


def test_cancel_completed_job_returns_conflict(client):
    mongo_client, coll = _collection()
    request_id = None
    try:
        request_id = _insert_job(coll, status="completed")
        response = client.post(f"/repository/ingest/{request_id}/cancel")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "conflict"
    finally:
        if request_id:
            coll.delete_one({"_id": ObjectId(request_id)})
        mongo_client.close()


def test_cancel_unknown_job_returns_not_found(client):
    response = client.post(f"/repository/ingest/{ObjectId()}/cancel")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
