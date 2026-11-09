from scripts.create_indexes import (
    DuplicateGroup,
    DuplicateReport,
    _DUPLICATE_CHECKS,
    _DUPLICATE_MATCHES,
    _INDEXES_TO_DROP,
    _NON_UNIQUE_INDEXES,
    _UNIQUE_INDEXES,
    format_duplicate_reports,
)


def test_format_duplicate_reports_lists_groups():
    reports = [
        DuplicateReport(
            collection="csChunks",
            fields=("chunk_id",),
            groups=[DuplicateGroup(key={"chunk_id": "abc"}, count=3)],
        ),
        DuplicateReport(collection="csRepositories", fields=("owner", "repo_name")),
    ]
    text = format_duplicate_reports(reports)
    assert "csChunks (chunk_id): 1 group(s)" in text
    assert "{'chunk_id': 'abc'} x3" in text
    assert "csRepositories" not in text
    assert "Fix duplicates by hand" in text


def test_chunk_v2_non_unique_indexes_present():
    names = {model.document["name"] for model in _NON_UNIQUE_INDEXES["csChunks"]}
    assert names >= {
        "idx_chunks_repo_id_file_path",
        "idx_chunks_repo_id_content_hash",
        "idx_chunks_repo_id_function_name",
        "idx_chunks_repo_id_class_name",
    }
    sparse = {
        model.document["name"]: model.document.get("sparse")
        for model in _NON_UNIQUE_INDEXES["csChunks"]
    }
    assert sparse["idx_chunks_repo_id_function_name"] is True
    assert sparse["idx_chunks_repo_id_class_name"] is True
    unique_names = {
        model.document["name"]
        for models in _UNIQUE_INDEXES.values()
        for model in models
    }
    assert "ux_chunks_point_id" not in unique_names


def test_day6_repo_and_library_indexes():
    unique = {
        model.document["name"]: model.document
        for models in _UNIQUE_INDEXES.values()
        for model in models
    }
    assert "ux_repositories_owner_repo_name" not in unique
    assert "ux_chat_sessions_repo_id_session_id" in unique
    repo_ux = unique["ux_repositories_owner_repo_name_branch_owner_user_id"]
    assert repo_ux.get("unique") is True
    chat_ux = unique["ux_chat_sessions_user_id_repo_id_session_id"]
    assert chat_ux.get("unique") is True
    assert chat_ux.get("partialFilterExpression") == {"user_id": {"$type": "objectId"}}
    assert "ux_user_repositories_user_id_repo_id" in unique
    assert _INDEXES_TO_DROP == {
        "csRepositories": ("ux_repositories_owner_repo_name",),
    }
    assert "csChatSessions" not in _INDEXES_TO_DROP
    assert ("csRepositories", ("owner", "repo_name", "branch", "owner_user_id")) in _DUPLICATE_CHECKS
    assert ("csChatSessions", ("user_id", "repo_id", "session_id")) in _DUPLICATE_CHECKS
    assert _DUPLICATE_MATCHES["csChatSessions"] == {"user_id": {"$type": "objectId"}}
    ingest_names = {
        model.document["name"] for model in _NON_UNIQUE_INDEXES["csIngestionRequests"]
    }
    assert "idx_ingestion_requests_requested_by_created_at" in ingest_names
    user_repo_non_unique = {
        model.document["name"] for model in _NON_UNIQUE_INDEXES["csUserRepositories"]
    }
    assert "idx_user_repositories_repo_id" in user_repo_non_unique


def test_auth_indexes_are_declared():
    unique_names = {
        model.document["name"]
        for models in _UNIQUE_INDEXES.values()
        for model in models
    }
    assert "ux_users_github_id" in unique_names
    assert "ux_users_github_login" in unique_names
    assert "ux_refresh_tokens_token_hash" in unique_names
    refresh_non_unique = {
        model.document["name"]: model.document
        for model in _NON_UNIQUE_INDEXES["csRefreshTokens"]
    }
    assert "idx_refresh_tokens_family_id" in refresh_non_unique
    assert "idx_refresh_tokens_user_id" in refresh_non_unique
    ttl = refresh_non_unique["ttl_refresh_tokens_expires_at"]
    assert ttl.get("expireAfterSeconds") == 0
