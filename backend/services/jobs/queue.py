"""Atomic claim, heartbeat, progress, and cancel for csIngestionRequests."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Union

from bson import ObjectId
from pymongo import ReturnDocument

LEASE_SECONDS = 60
MAX_CLAIM_COUNT = 3


class JobCancelled(Exception):
    """Raised when ingest should stop because the job row is cancelled."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_object_id(value: Union[str, ObjectId]) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError(f"Invalid ObjectId value: {value}")


def _public_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(doc)
    public["id"] = str(public.pop("_id"))
    repo_id = public.get("repo_id")
    if repo_id:
        public["repo_id"] = str(repo_id)
    return public


def _claim_filter(now: datetime) -> Dict[str, Any]:
    return {
        "$or": [
            {
                "status": "pending",
                "$or": [{"retry_after": None}, {"retry_after": {"$lte": now}}],
            },
            {"status": "processing", "lease.expires_at": {"$lt": now}},
        ]
    }


class JobQueue:
    """Mongo-backed ingestion queue. One document is one job."""

    def __init__(self, collection: Any) -> None:
        self._jobs = collection

    async def claim(self, worker_id: str, *, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Atomically claim the oldest pending or lease-expired job."""
        claimed_at = now or _now()
        expires_at = claimed_at + timedelta(seconds=LEASE_SECONDS)
        doc = await self._jobs.find_one_and_update(
            _claim_filter(claimed_at),
            [
                {
                    "$set": {
                        "status": "processing",
                        "lease": {
                            "worker_id": worker_id,
                            "heartbeat_at": claimed_at,
                            "expires_at": expires_at,
                        },
                        "claim_count": {"$add": [{"$ifNull": ["$claim_count", 0]}, 1]},
                        "started_at": {"$ifNull": ["$started_at", claimed_at]},
                        "updated_at": claimed_at,
                        "message": "Processing repository...",
                    }
                }
            ],
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None

        if int(doc.get("claim_count") or 0) > MAX_CLAIM_COUNT:
            request_id = str(doc["_id"])
            await self.fail(
                request_id,
                error="Job exceeded 3 claims without finishing",
                message="Job exceeded 3 claims without finishing",
                now=claimed_at,
            )
            return None

        return _public_doc(doc)

    async def heartbeat(
        self,
        request_id: str,
        worker_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> bool:
        """Extend the lease if this worker still owns a processing job."""
        beat_at = now or _now()
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return False

        result = await self._jobs.update_one(
            {
                "_id": job_id,
                "status": "processing",
                "lease.worker_id": worker_id,
            },
            {
                "$set": {
                    "lease.heartbeat_at": beat_at,
                    "lease.expires_at": beat_at + timedelta(seconds=LEASE_SECONDS),
                    "updated_at": beat_at,
                }
            },
        )
        return result.matched_count > 0

    async def set_progress(
        self,
        request_id: str,
        *,
        stage: Optional[str] = None,
        files_total: Optional[int] = None,
        files_done: Optional[int] = None,
        chunks_total: Optional[int] = None,
        chunks_embedded: Optional[int] = None,
        message: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """Write progress fields on an active job. No-ops if cancelled."""
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return False

        fields: Dict[str, Any] = {"updated_at": now or _now()}
        if stage is not None:
            fields["progress.stage"] = stage
        if files_total is not None:
            fields["progress.files_total"] = files_total
        if files_done is not None:
            fields["progress.files_done"] = files_done
        if chunks_total is not None:
            fields["progress.chunks_total"] = chunks_total
        if chunks_embedded is not None:
            fields["progress.chunks_embedded"] = chunks_embedded
        if message is not None:
            fields["message"] = message

        result = await self._jobs.update_one(
            {"_id": job_id, "status": "processing"},
            {"$set": fields},
        )
        return result.matched_count > 0

    async def set_repo_id(self, request_id: str, repo_id: str) -> bool:
        """Link the job to a repository while it is still processing."""
        try:
            job_id = _to_object_id(request_id)
            repo_oid = _to_object_id(repo_id)
        except ValueError:
            return False
        result = await self._jobs.update_one(
            {"_id": job_id, "status": "processing"},
            {"$set": {"repo_id": repo_oid, "updated_at": _now()}},
        )
        return result.matched_count > 0

    async def complete(
        self,
        request_id: str,
        *,
        message: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """Mark a processing job completed and drop the lease."""
        return await self._finish(
            request_id,
            status="completed",
            message=message or "Ingestion completed",
            now=now,
        )

    async def fail(
        self,
        request_id: str,
        *,
        error: str,
        message: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        """Mark a processing job failed and drop the lease."""
        return await self._finish(
            request_id,
            status="failed",
            message=message or error,
            error=error,
            now=now,
        )

    async def cancel(
        self,
        request_id: str,
        *,
        now: Optional[datetime] = None,
    ) -> Optional[Dict[str, Any]]:
        """Cancel a pending or processing job. Returns None if it was already terminal."""
        cancelled_at = now or _now()
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return None

        doc = await self._jobs.find_one_and_update(
            {"_id": job_id, "status": {"$in": ["pending", "processing"]}},
            {
                "$set": {
                    "status": "cancelled",
                    "completed_at": cancelled_at,
                    "updated_at": cancelled_at,
                    "message": "Cancelled",
                    "lease": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None
        return _public_doc(doc)

    async def is_cancelled(self, request_id: str) -> bool:
        """Return True if the job exists and is cancelled."""
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return False
        doc = await self._jobs.find_one({"_id": job_id}, {"status": 1})
        return bool(doc and doc.get("status") == "cancelled")

    async def prepare_retry(self, request_id: str) -> Optional[Dict[str, Any]]:
        """Requeue a failed job as pending with a fresh claim_count."""
        now = _now()
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return None
        doc = await self._jobs.find_one_and_update(
            {"_id": job_id, "status": "failed"},
            {
                "$set": {
                    "status": "pending",
                    "claim_count": 0,
                    "trigger": "retry",
                    "progress.stage": "queued",
                    "lease": None,
                    "message": "Retrying ingestion...",
                    "updated_at": now,
                },
                "$inc": {"retry_count": 1},
                "$unset": {"error": "", "completed_at": "", "started_at": ""},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not doc:
            return None
        return _public_doc(doc)

    async def _finish(
        self,
        request_id: str,
        *,
        status: str,
        message: str,
        error: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> bool:
        finished_at = now or _now()
        try:
            job_id = _to_object_id(request_id)
        except ValueError:
            return False

        fields: Dict[str, Any] = {
            "status": status,
            "message": message,
            "completed_at": finished_at,
            "updated_at": finished_at,
            "lease": None,
        }
        unset: Dict[str, str] = {}
        if error is not None:
            fields["error"] = error
        else:
            unset["error"] = ""

        update: Dict[str, Any] = {"$set": fields}
        if unset:
            update["$unset"] = unset

        result = await self._jobs.update_one(
            {"_id": job_id, "status": "processing"},
            update,
        )
        return result.matched_count > 0
