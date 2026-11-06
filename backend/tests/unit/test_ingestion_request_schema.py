from schemas.ingestion_request_schema import (
    IngestionRequestSchema,
    IngestionStage,
    IngestionStatus,
)


def test_target_commit_survives_dump_and_legacy_hash_is_dropped():
    schema = IngestionRequestSchema(
        owner="owner",
        repo_name="repo",
        repository_url="https://github.com/owner/repo.git",
        branch="main",
        target_commit="a1b2c3d",
        expected_commit_hash="should-not-be-stored",
        cleanup=False,
        progress={
            "stage": "embedding",
            "files_total": 10,
            "files_done": 4,
            "chunks_total": 20,
            "chunks_embedded": 8,
        },
    )
    dumped = schema.model_dump(mode="json")
    assert dumped["target_commit"] == "a1b2c3d"
    assert "expected_commit_hash" not in dumped
    assert dumped["cleanup"] is False
    assert dumped["type"] == "full"
    assert dumped["trigger"] == "user"
    assert dumped["claim_count"] == 0
    assert dumped["progress"]["stage"] == "embedding"
    assert dumped["progress"]["chunks_embedded"] == 8
    assert dumped["status"] == "pending"


def test_cancelled_is_a_status_and_progress_defaults_to_queued():
    schema = IngestionRequestSchema(
        owner="owner",
        repo_name="repo",
        repository_url="https://github.com/owner/repo.git",
        status=IngestionStatus.CANCELLED,
    )
    dumped = schema.model_dump(mode="json")
    assert dumped["status"] == "cancelled"
    assert dumped["progress"]["stage"] == IngestionStage.QUEUED.value
    assert dumped["progress"]["files_total"] == 0
    assert dumped["lease"] is None
