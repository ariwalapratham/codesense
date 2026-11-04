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
