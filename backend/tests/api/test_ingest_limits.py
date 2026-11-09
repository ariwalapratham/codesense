import pytest

from config.settings import settings
from services.repository_service import RepositoryService


@pytest.fixture
def ingest_github_mocks(monkeypatch):
    async def default_branch(repo_url, branch):
        return branch or "main"

    async def commit_hash(self, repo_url, branch=None):
        return "a1b2c3d"

    monkeypatch.setattr(
        "api.routes.repository.resolve_github_default_branch",
        default_branch,
    )
    monkeypatch.setattr(RepositoryService, "get_remote_commit_hash", commit_hash)


def test_oversized_repo_returns_413(client, auth_headers, ingest_github_mocks, monkeypatch):
    async def huge(self, repo_url):
        return (settings.max_repo_size_mb * 1024) + 1

    monkeypatch.setattr(RepositoryService, "get_repo_size_kb", huge)
    response = client.post(
        "/repository/ingest",
        json={"repo_url": "https://github.com/codesense-test/too-large"},
        headers=auth_headers,
    )
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "repo_too_large"
    assert error["details"]["limit_mb"] == settings.max_repo_size_mb


def test_unknown_repo_size_returns_413(client, auth_headers, ingest_github_mocks, monkeypatch):
    async def unknown(self, repo_url):
        return None

    monkeypatch.setattr(RepositoryService, "get_repo_size_kb", unknown)
    response = client.post(
        "/repository/ingest",
        json={"repo_url": "https://github.com/codesense-test/unknown-size"},
        headers=auth_headers,
    )
    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "repo_too_large"
    assert error["details"]["size_kb"] is None
