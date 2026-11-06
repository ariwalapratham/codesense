"""In-process worker: one ingest job at a time, with a 60s lease."""

import asyncio
import logging
import os
import socket
from uuid import uuid4

from services.ingestion_service import IngestionService
from services.jobs.queue import JobCancelled, JobQueue

logger = logging.getLogger(__name__)

POLL_SECONDS = 2
HEARTBEAT_SECONDS = 15


class IngestionWorker:
    """Poll Mongo, claim a job, run ingest, heartbeat until it finishes."""

    def __init__(self, queue: JobQueue, ingestion_service: IngestionService) -> None:
        self._queue = queue
        self._ingest = ingestion_service
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}-{uuid4().hex[:8]}"
        self._stop = asyncio.Event()

    async def run(self) -> None:
        """Claim and run jobs until stop() is called."""
        logger.info("Ingestion worker started (%s)", self._worker_id)
        while not self._stop.is_set():
            try:
                job = await self._queue.claim(self._worker_id)
            except Exception:
                logger.exception("Job claim failed")
                await self._idle()
                continue
            if not job:
                await self._idle()
                continue
            await self._run_job(job)
        logger.info("Ingestion worker stopped (%s)", self._worker_id)

    async def stop(self) -> None:
        """Signal the poll loop to exit after the current idle wait."""
        self._stop.set()

    async def _idle(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
        except asyncio.TimeoutError:
            return

    async def _run_job(self, job: dict) -> None:
        request_id = job["id"]
        heartbeat = asyncio.create_task(self._heartbeat_loop(request_id))
        try:
            ok, message = await self._ingest.ingest_repository(
                repo_url=job["repository_url"],
                branch=job.get("branch"),
                cleanup=True if job.get("cleanup") is None else bool(job.get("cleanup")),
                request_id=request_id,
                target_commit=job.get("target_commit"),
            )
            if await self._queue.is_cancelled(request_id):
                return
            if ok:
                await self._queue.complete(request_id, message=message)
            else:
                await self._queue.fail(request_id, error=message)
        except JobCancelled:
            logger.info("Ingestion cancelled (request_id=%s)", request_id)
        except Exception:
            logger.exception("Ingestion failed (request_id=%s)", request_id)
            if not await self._queue.is_cancelled(request_id):
                await self._queue.fail(
                    request_id,
                    error="Unexpected error during ingestion",
                )
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, request_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            alive = await self._queue.heartbeat(request_id, self._worker_id)
            if not alive:
                return
