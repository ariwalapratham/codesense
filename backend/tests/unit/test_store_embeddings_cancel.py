from unittest.mock import AsyncMock
from bson import ObjectId

from services.embeddings.fake import FakeEmbedder
from services.ingestion_service import IngestionService
from services.jobs.queue import JobCancelled
from services.vector_store.memory import MemoryVectorStore


class _Queue:
    def __init__(self) -> None:
        self.cancelled = False
        self.repo_id = None

    async def set_repo_id(self, request_id: str, repo_id: str) -> bool:
        self.repo_id = repo_id
        return True

    async def is_cancelled(self, request_id: str) -> bool:
        return self.cancelled


def _chunk(chunk_id: str, text: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "code_text": text,
        "file_path": "a.py",
        "language": "python",
        "start_line": 1,
        "end_line": 2,
        "function_name": "f",
        "class_name": None,
    }


async def test_store_embeddings_links_repo_and_skips_completed_on_cancel():
    queue = _Queue()
    repo_oid = ObjectId()
    upserts = []

    async def upsert(repo_data):
        upserts.append(repo_data)
        queue.cancelled = True
        return repo_oid

    service = IngestionService(
        embedder=FakeEmbedder(),
        vector_store=MemoryVectorStore(),
        job_queue=queue,
    )
    service.mongodb_service.find_repository_by_identifier = AsyncMock(return_value=None)
    service.mongodb_service.upsert_repository = upsert
    service.mongodb_service.list_embedded_hash_index = AsyncMock(return_value={})
    service.mongodb_service.list_chunk_point_ids = AsyncMock(return_value=[])
    service.mongodb_service.replace_repo_chunks = AsyncMock(return_value=True)
    service.mongodb_service.release_repo_if_processing = AsyncMock()

    try:
        await service._store_embeddings(
            chunks=[_chunk("c1", "def f():\n    return 1\n")],
            owner="acme",
            repo_name="demo",
            repo_url="https://github.com/acme/demo",
            branch="main",
            commit_hash="abc1234",
            files_processed=1,
            parsing_stats=None,
            request_id=str(ObjectId()),
        )
    except JobCancelled:
        pass
    else:
        raise AssertionError("expected JobCancelled")

    assert queue.repo_id == str(repo_oid)
    assert [row["status"] for row in upserts] == ["processing"]
    service.mongodb_service.replace_repo_chunks.assert_not_awaited()
    service.mongodb_service.release_repo_if_processing.assert_awaited_once_with(str(repo_oid))
