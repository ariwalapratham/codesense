import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient

from config.settings import settings
from services.jobs.queue import JobCancelled, JobQueue
from services.jobs.worker import IngestionWorker
from services.mongodb_service import MongoDBService


async def _require_mongo():
    client = AsyncMongoClient(
        settings.mongodb_url,
        serverSelectionTimeoutMS=1000,
        appName="CodeSenseJobWorkerTests",
    )
    try:
        await client.admin.command("ping")
    except Exception:
        await client.close()
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("MongoDB is required for job worker tests in CI")
        pytest.skip("MongoDB is required for job worker tests")
    return client


class _FakeIngest:
    def __init__(self, queue: JobQueue, hold: asyncio.Event) -> None:
        self.queue = queue
        self.hold = hold
        self.calls = 0

    async def ingest_repository(self, **kwargs):
        self.calls += 1
        request_id = kwargs["request_id"]
        await self.hold.wait()
        if await self.queue.is_cancelled(request_id):
            raise JobCancelled()
        return True, "ok"


@pytest.fixture
async def queue_col():
    client = await _require_mongo()
    collection = client[settings.db_name]["csIngestionRequestsJobWorkerTest"]
    ids = []
    yield collection, ids
    if ids:
        await collection.delete_many({"_id": {"$in": ids}})
    await client.close()


async def test_worker_stops_when_job_cancelled(queue_col):
    collection, ids = queue_col
    queue = JobQueue(collection)
    hold = asyncio.Event()
    ingest = _FakeIngest(queue, hold)
    worker = IngestionWorker(queue, ingest)
    now = datetime.now(timezone.utc)
    result = await collection.insert_one(
        {
            "owner": f"cs-worker-{uuid4().hex[:8]}",
            "repo_name": "repo",
            "repository_url": "https://github.com/cs-worker/repo",
            "branch": "main",
            "status": "pending",
            "claim_count": 0,
            "created_at": now,
            "updated_at": now,
            "cleanup": True,
        }
    )
    ids.append(result.inserted_id)
    task = asyncio.create_task(worker.run())
    for _ in range(50):
        stored = await collection.find_one({"_id": result.inserted_id})
        if stored and stored.get("status") == "processing":
            break
        await asyncio.sleep(0.05)
    cancelled = await queue.cancel(str(result.inserted_id))
    assert cancelled["status"] == "cancelled"
    hold.set()
    await asyncio.sleep(0.2)
    await worker.stop()
    await asyncio.wait_for(task, timeout=2)
    stored = await collection.find_one({"_id": result.inserted_id})
    assert stored["status"] == "cancelled"
    assert ingest.calls == 1


async def test_recover_stuck_processing_jobs_makes_job_claimable(queue_col):
    collection, ids = queue_col
    now = datetime.now(timezone.utc)
    result = await collection.insert_one(
        {
            "owner": f"cs-worker-{uuid4().hex[:8]}",
            "repo_name": "repo",
            "repository_url": "https://github.com/cs-worker/repo",
            "branch": "main",
            "status": "processing",
            "claim_count": 1,
            "created_at": now - timedelta(minutes=5),
            "updated_at": now - timedelta(minutes=5),
        }
    )
    ids.append(result.inserted_id)
    service = MongoDBService()
    service.ingestion_requests = collection
    modified = await service.recover_stuck_processing_jobs()
    assert modified >= 1
    queue = JobQueue(collection)
    claimed = await queue.claim("worker-recover")
    assert claimed is not None
    assert claimed["id"] == str(result.inserted_id)
