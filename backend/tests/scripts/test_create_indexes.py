from scripts.create_indexes import (
    DuplicateGroup,
    DuplicateReport,
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
