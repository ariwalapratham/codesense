"""Bearer is required on repository/chat. Health and OAuth stay public."""

import pytest

_REPO = "507f1f77bcf86cd799439011"
_SESSION = "session-1"


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/repository/"),
        ("GET", f"/repository/{_REPO}"),
        ("GET", f"/repository/{_REPO}/stats"),
        ("GET", f"/repository/{_REPO}/search?q=def"),
        ("DELETE", f"/repository/{_REPO}"),
        ("POST", "/repository/ingest"),
        ("GET", "/repository/ingest/requests"),
        ("GET", "/repository/ingest/status/not-an-id"),
        ("POST", "/repository/ingest/507f1f77bcf86cd799439011/retry"),
        ("POST", "/repository/ingest/507f1f77bcf86cd799439011/cancel"),
        ("GET", f"/repository/{_REPO}/chat-sessions/"),
        ("POST", f"/repository/{_REPO}/chat-sessions/"),
        ("GET", f"/repository/{_REPO}/chat-sessions/{_SESSION}/messages"),
        ("POST", f"/repository/{_REPO}/chat-sessions/{_SESSION}/messages"),
        ("POST", f"/repository/{_REPO}/chat-sessions/{_SESSION}/messages/stream"),
        ("PATCH", f"/repository/{_REPO}/chat-sessions/{_SESSION}"),
        ("DELETE", f"/repository/{_REPO}/chat-sessions/{_SESSION}"),
    ],
)
def test_protected_routes_require_bearer(client, method, path):
    response = client.request(method, path)
    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "unauthorized"
    assert error["request_id"] == response.headers.get("x-request-id")


def test_protected_route_rejects_invalid_bearer(client):
    response = client.get(
        "/repository/",
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_health_stays_public(client):
    assert client.get("/health").status_code == 200
    assert client.get("/health/deep").status_code == 200


def test_oauth_login_stays_public(client):
    response = client.get("/auth/github/login", follow_redirects=False)
    assert response.status_code == 302


def test_authenticated_unknown_repo_is_still_not_found(client, auth_headers):
    response = client.get("/repository/not-an-object-id", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
