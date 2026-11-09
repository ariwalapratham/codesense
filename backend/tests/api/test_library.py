"""Day 6b: library access, ingest-as-link, job ownership, chat scope."""

from datetime import datetime, timezone
from uuid import uuid4

from bson import ObjectId
from pymongo import MongoClient

from config.settings import settings
from services.repository_service import RepositoryService

_NOW = datetime.now(timezone.utc)


def _mongo():
    client = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    return client, client[settings.db_name]


def _insert_repo(db, **fields):
    doc = {
        "_id": ObjectId(),
        "owner": "acme",
        "repo_name": f"lib-{uuid4().hex[:8]}",
        "repository_url": "https://github.com/acme/lib.git",
        "branch": "main",
        "status": "completed",
        "commit_hash": "a1b2c3d",
        "visibility": "public",
        "owner_user_id": None,
        "files_processed": 1,
        "chunks_count": 1,
        "chunks_with_embeddings": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    doc.update(fields)
    db["csRepositories"].insert_one(doc)
    return doc


def _link(db, user_id, repo_id):
    db["csUserRepositories"].insert_one(
        {
            "user_id": user_id,
            "repo_id": repo_id,
            "role": "member",
            "added_at": _NOW,
        }
    )


def test_list_is_library_only(client, auth_headers, auth_user, other_auth_headers):
    mongo, db = _mongo()
    mine = other = None
    try:
        mine = _insert_repo(db, repo_name="mine")
        other = _insert_repo(db, repo_name="theirs")
        _link(db, auth_user["_id"], mine["_id"])
        mine_list = client.get("/repository/", headers=auth_headers)
        assert mine_list.status_code == 200
        ids = {row["id"] for row in mine_list.json()["repositories"]}
        assert str(mine["_id"]) in ids
        assert str(other["_id"]) not in ids
        other_list = client.get("/repository/", headers=other_auth_headers)
        assert other_list.status_code == 200
        assert str(mine["_id"]) not in {
            row["id"] for row in other_list.json()["repositories"]
        }
    finally:
        if mine:
            db["csRepositories"].delete_one({"_id": mine["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": mine["_id"]})
        if other:
            db["csRepositories"].delete_one({"_id": other["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": other["_id"]})
        mongo.close()


def test_foreign_repo_is_not_found(client, auth_headers, other_auth_headers, auth_user):
    mongo, db = _mongo()
    repo = None
    try:
        repo = _insert_repo(db)
        _link(db, auth_user["_id"], repo["_id"])
        repo_id = str(repo["_id"])
        assert client.get(f"/repository/{repo_id}", headers=auth_headers).status_code == 200
        foreign = client.get(f"/repository/{repo_id}", headers=other_auth_headers)
        assert foreign.status_code == 404
        assert foreign.json()["error"]["code"] == "not_found"
        search = client.get(
            f"/repository/{repo_id}/search",
            params={"q": "def add"},
            headers=other_auth_headers,
        )
        assert search.status_code == 404
    finally:
        if repo:
            db["csRepositories"].delete_one({"_id": repo["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": repo["_id"]})
        mongo.close()


def test_delete_unlinks_only(client, auth_headers, auth_user):
    mongo, db = _mongo()
    repo = None
    try:
        repo = _insert_repo(db)
        _link(db, auth_user["_id"], repo["_id"])
        repo_id = str(repo["_id"])
        response = client.delete(f"/repository/{repo_id}", headers=auth_headers)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["details"]["unlinked"] is True
        assert body["details"]["repository_deleted"] is False
        assert db["csRepositories"].find_one({"_id": repo["_id"]}) is not None
        assert db["csUserRepositories"].find_one(
            {"user_id": auth_user["_id"], "repo_id": repo["_id"]}
        ) is None
        assert client.get(f"/repository/{repo_id}", headers=auth_headers).status_code == 404
    finally:
        if repo:
            db["csRepositories"].delete_one({"_id": repo["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": repo["_id"]})
        mongo.close()


def test_ingest_links_completed_repo_with_200(
    client, auth_headers, auth_user, monkeypatch
):
    mongo, db = _mongo()
    repo = None
    try:
        repo = _insert_repo(db, owner="codesense-test", repo_name="already-there")

        async def default_branch(repo_url, branch):
            return branch or "main"

        async def commit_hash(self, repo_url, branch=None):
            return "a1b2c3d"

        monkeypatch.setattr(
            "api.routes.repository.resolve_github_default_branch",
            default_branch,
        )
        monkeypatch.setattr(RepositoryService, "get_remote_commit_hash", commit_hash)

        response = client.post(
            "/repository/ingest",
            json={"repo_url": "https://github.com/codesense-test/already-there"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        body = response.json()
        assert body["linked"] is True
        assert body["status"] == "completed"
        assert body["repo_id"] == str(repo["_id"])
        assert db["csUserRepositories"].find_one(
            {"user_id": auth_user["_id"], "repo_id": repo["_id"]}
        )
    finally:
        if repo:
            db["csRepositories"].delete_one({"_id": repo["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": repo["_id"]})
        mongo.close()


def test_library_member_can_poll_ingest_status_not_cancel(
    client, auth_headers, other_auth_headers, auth_user, other_auth_user
):
    mongo, db = _mongo()
    repo = None
    request_id = None
    try:
        repo = _insert_repo(db)
        _link(db, other_auth_user["_id"], repo["_id"])
        request_id = db["csIngestionRequests"].insert_one(
            {
                "owner": repo["owner"],
                "repo_name": repo["repo_name"],
                "repository_url": repo["repository_url"],
                "branch": repo["branch"],
                "repo_id": repo["_id"],
                "status": "processing",
                "requested_by": auth_user["_id"],
                "type": "full",
                "trigger": "user",
                "created_at": _NOW,
                "updated_at": _NOW,
                "retry_count": 0,
                "progress": {
                    "stage": "embedding",
                    "files_total": 1,
                    "files_done": 1,
                    "chunks_total": 1,
                    "chunks_embedded": 0,
                },
            }
        ).inserted_id
        status = client.get(
            f"/repository/ingest/status/{request_id}",
            headers=other_auth_headers,
        )
        assert status.status_code == 200
        assert status.json()["request_id"] == str(request_id)
        cancel = client.post(
            f"/repository/ingest/{request_id}/cancel",
            headers=other_auth_headers,
        )
        assert cancel.status_code == 404
        retry = client.post(
            f"/repository/ingest/{request_id}/retry",
            headers=other_auth_headers,
        )
        assert retry.status_code == 404
    finally:
        if request_id:
            db["csIngestionRequests"].delete_one({"_id": request_id})
        if repo:
            db["csRepositories"].delete_one({"_id": repo["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": repo["_id"]})
        mongo.close()


def test_foreign_job_is_not_found(client, auth_headers, other_auth_headers, auth_user):
    mongo, db = _mongo()
    request_id = None
    try:
        request_id = db["csIngestionRequests"].insert_one(
            {
                "owner": "acme",
                "repo_name": "job",
                "repository_url": "https://github.com/acme/job",
                "branch": "main",
                "status": "pending",
                "requested_by": auth_user["_id"],
                "type": "full",
                "trigger": "user",
                "created_at": _NOW,
                "updated_at": _NOW,
                "retry_count": 0,
                "progress": {
                    "stage": "queued",
                    "files_total": 0,
                    "files_done": 0,
                    "chunks_total": 0,
                    "chunks_embedded": 0,
                },
            }
        ).inserted_id
        mine = client.get(
            f"/repository/ingest/status/{request_id}",
            headers=auth_headers,
        )
        assert mine.status_code == 200
        foreign = client.get(
            f"/repository/ingest/status/{request_id}",
            headers=other_auth_headers,
        )
        assert foreign.status_code == 404
        cancel = client.post(
            f"/repository/ingest/{request_id}/cancel",
            headers=other_auth_headers,
        )
        assert cancel.status_code == 404
    finally:
        if request_id:
            db["csIngestionRequests"].delete_one({"_id": request_id})
        mongo.close()


def test_chat_sessions_are_scoped_to_caller(
    client, auth_headers, other_auth_headers, auth_user, other_auth_user
):
    mongo, db = _mongo()
    repo = None
    try:
        repo = _insert_repo(db)
        _link(db, auth_user["_id"], repo["_id"])
        _link(db, other_auth_user["_id"], repo["_id"])
        repo_id = str(repo["_id"])
        created = client.post(
            f"/repository/{repo_id}/chat-sessions/",
            json={"title": "mine"},
            headers=auth_headers,
        )
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        mine_list = client.get(
            f"/repository/{repo_id}/chat-sessions/",
            headers=auth_headers,
        )
        assert mine_list.status_code == 200
        assert {row["session_id"] for row in mine_list.json()["sessions"]} == {session_id}
        other_list = client.get(
            f"/repository/{repo_id}/chat-sessions/",
            headers=other_auth_headers,
        )
        assert other_list.status_code == 200
        assert other_list.json()["sessions"] == []
        foreign = client.get(
            f"/repository/{repo_id}/chat-sessions/{session_id}/messages",
            headers=other_auth_headers,
        )
        assert foreign.status_code == 404
    finally:
        if repo:
            db["csChatSessions"].delete_many({"repo_id": repo["_id"]})
            db["csUserRepositories"].delete_many({"repo_id": repo["_id"]})
            db["csRepositories"].delete_one({"_id": repo["_id"]})
        mongo.close()
