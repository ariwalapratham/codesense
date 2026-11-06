import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import AsyncMongoClient

from config.settings import settings
from services.jobs.queue import MAX_CLAIM_COUNT, JobQueue
from services.mongodb_service import MongoDBService

WORKER_A = "worker-a"
WORKER_B = "worker-b"


async def _require_mongo():
    client = AsyncMongoClient(
        settings.mongodb_url,
        serverSelectionTimeoutMS=1000,
        appName="CodeSenseJobQueueTests",
    )
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("MongoDB is required for job queue tests in CI")
        pytest.skip("MongoDB is required for job queue tests")
    return client


@pytest.fixture
async def jobs():
    client = await _require_mongo()
    collection = client[settings.db_name]["csIngestionRequestsJobQueueTest"]
    ids = []

    async def insert(**fields):
        now = datetime.now(timezone.utc)
        doc = {
            "owner": f"cs-jobq-{uuid4().hex[:8]}",
            "repo_name": "repo",
            "repository_url": "https://github.com/cs-jobq/repo",
            "branch": "main",
            "status": "pending",
            "retry_count": 0,
            "claim_count": 0,
            "created_at": now,
            "updated_at": now,
            "type": "full",
            "trigger": "user",
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
        result = await collection.insert_one(doc)
        ids.append(result.inserted_id)
        return result.inserted_id

    try:
        yield collection, insert
    finally:
        if ids:
            await collection.delete_many({"_id": {"$in": ids}})
        await client.close()


async def test_claim_oldest_pending(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    older = datetime.now(timezone.utc) - timedelta(minutes=5)
    newer = datetime.now(timezone.utc) - timedelta(minutes=1)
    first_id = await insert(created_at=older, repo_name="older")
    await insert(created_at=newer, repo_name="newer")

    claimed = await queue.claim(WORKER_A)
    assert claimed is not None
    assert claimed["id"] == str(first_id)
    assert claimed["status"] == "processing"
    assert claimed["lease"]["worker_id"] == WORKER_A
    assert claimed["claim_count"] == 1


async def test_live_lease_is_not_stolen(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    now = datetime.now(timezone.utc)
    await insert(
        status="processing",
        claim_count=1,
        lease={
            "worker_id": WORKER_A,
            "heartbeat_at": now,
            "expires_at": now + timedelta(seconds=60),
        },
    )

    assert await queue.claim(WORKER_B) is None


async def test_expired_lease_is_reclaimed(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    now = datetime.now(timezone.utc)
    job_id = await insert(
        status="processing",
        claim_count=1,
        lease={
            "worker_id": WORKER_A,
            "heartbeat_at": now - timedelta(seconds=90),
            "expires_at": now - timedelta(seconds=30),
        },
    )

    claimed = await queue.claim(WORKER_B)
    assert claimed is not None
    assert claimed["id"] == str(job_id)
    assert claimed["lease"]["worker_id"] == WORKER_B
    assert claimed["claim_count"] == 2


async def test_cancel_pending_excludes_job_from_claim(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    job_id = await insert()

    cancelled = await queue.cancel(str(job_id))
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed_at"] is not None
    assert await queue.claim(WORKER_A) is None


async def test_claim_count_increments_and_trips_max(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    now = datetime.now(timezone.utc)
    job_id = await insert()

    first = await queue.claim(WORKER_A)
    assert first["claim_count"] == 1

    await collection.update_one(
        {"_id": job_id},
        {
            "$set": {
                "lease.expires_at": now - timedelta(seconds=1),
                "claim_count": MAX_CLAIM_COUNT,
            }
        },
    )
    assert await queue.claim(WORKER_B) is None
    stored = await collection.find_one({"_id": job_id})
    assert stored["status"] == "failed"
    assert stored["claim_count"] == MAX_CLAIM_COUNT + 1


async def test_started_at_is_preserved_on_reclaim(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    first = await queue.claim(WORKER_A)
    started_at = first["started_at"]
    await collection.update_one(
        {"_id": ObjectId(first["id"])},
        {"$set": {"lease.expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    claimed = await queue.claim(WORKER_B)
    assert claimed["id"] == first["id"]
    assert claimed["started_at"] == started_at
    assert claimed["claim_count"] == 2


async def test_cancel_processing_job(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    job_id = await insert()
    claimed = await queue.claim(WORKER_A)
    assert claimed["id"] == str(job_id)
    cancelled = await queue.cancel(str(job_id))
    assert cancelled["status"] == "cancelled"
    assert await queue.is_cancelled(str(job_id))
    assert await queue.complete(str(job_id), message="nope") is False


async def test_prepare_retry_increments_retry_count(jobs):
    collection, insert = jobs
    queue = JobQueue(collection)
    job_id = await insert(status="failed", retry_count=1)

    retried = await queue.prepare_retry(str(job_id))
    assert retried is not None
    assert retried["status"] == "pending"
    assert retried["retry_count"] == 2
    assert retried["claim_count"] == 0


async def test_stale_streaming_messages_are_marked_failed():
    client = await _require_mongo()
    sessions = client[settings.db_name]["csChatSessions"]
    session_id = f"cs-jobq-{uuid4().hex}"
    repo_id = ObjectId()
    now = datetime.now(timezone.utc)
    await sessions.insert_one(
        {
            "session_id": session_id,
            "repo_id": repo_id,
            "created_at": now,
            "updated_at": now,
            "messages": [
                {
                    "message_id": "old",
                    "role": "assistant",
                    "content": "partial",
                    "timestamp": now - timedelta(minutes=11),
                    "status": "streaming",
                },
                {
                    "message_id": "fresh",
                    "role": "assistant",
                    "content": "still going",
                    "timestamp": now - timedelta(minutes=1),
                    "status": "streaming",
                },
            ],
        }
    )
    try:
        service = MongoDBService()
        service.chat_sessions = sessions
        await service.fail_stale_streaming_messages()
        stored = await sessions.find_one({"session_id": session_id, "repo_id": repo_id})
        by_id = {msg["message_id"]: msg["status"] for msg in stored["messages"]}
        assert by_id["old"] == "failed"
        assert by_id["fresh"] == "streaming"
    finally:
        await sessions.delete_one({"session_id": session_id, "repo_id": repo_id})
        await client.close()
