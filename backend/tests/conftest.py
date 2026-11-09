"""API test fixtures. Env must be set before importing the app."""

from __future__ import annotations

import os

import pytest

os.environ["FRONTEND_ORIGIN"] = "http://localhost:3000"
os.environ["MONGODB_URL"] = os.environ.get(
    "TEST_MONGODB_URL", "mongodb://127.0.0.1:27017"
)
os.environ["DB_NAME"] = "codesense_test"
os.environ["GEMINI_API_KEY"] = ""
os.environ["QDRANT_URL"] = ""
os.environ["QDRANT_API_KEY"] = ""
os.environ["INGESTION_WORKER_ENABLED"] = "false"
os.environ["JWT_SECRET"] = "codesense-test-jwt-secret-not-for-prod"
os.environ["GITHUB_OAUTH_CLIENT_ID"] = "test-oauth-client-id"
os.environ["GITHUB_OAUTH_CLIENT_SECRET"] = "test-oauth-client-secret"


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as test_client:
        deep = test_client.get("/health/deep")
        if deep.status_code != 200:
            if os.environ.get("GITHUB_ACTIONS"):
                pytest.fail("MongoDB is required for API tests in CI")
            pytest.skip("MongoDB is required for API tests")
        yield test_client


@pytest.fixture(scope="session")
def auth_headers(client):
    """Bearer header for a persisted test user. Repository/chat routes require this."""
    from datetime import datetime, timezone

    from bson import ObjectId
    from pymongo import MongoClient

    from config.settings import settings
    from core.security import create_access_token

    now = datetime.now(timezone.utc)
    mongo = MongoClient(settings.mongodb_url, serverSelectionTimeoutMS=5000)
    try:
        users = mongo[settings.db_name]["csUsers"]
        users.update_one(
            {"github_id": 900000001},
            {
                "$set": {
                    "github_login": "pytest-auth-user",
                    "status": "active",
                    "role": "user",
                    "last_login_at": now,
                },
                "$setOnInsert": {
                    "_id": ObjectId(),
                    "github_id": 900000001,
                    "created_at": now,
                    "quota_overrides": {
                        "repos": None,
                        "ingests_per_day": None,
                        "chat_per_day": None,
                    },
                },
            },
            upsert=True,
        )
        doc = users.find_one({"github_id": 900000001})
        token = create_access_token(user_id=str(doc["_id"]), role="user")
        yield {"Authorization": f"Bearer {token}"}
    finally:
        mongo.close()
