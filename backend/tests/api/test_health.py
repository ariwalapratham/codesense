def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "service": "codesense-api"}
    assert response.headers.get("x-request-id")


def test_health_echoes_request_id(client):
    response = client.get("/health", headers={"X-Request-ID": "test-req-123"})
    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "test-req-123"


def test_health_deep_mongo_ok_qdrant_skipped(client):
    response = client.get("/health/deep")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["mongo"]["status"] == "ok"
    assert body["checks"]["qdrant"] == {
        "status": "skipped",
        "reason": "not_configured",
    }
    assert response.headers.get("x-request-id")
