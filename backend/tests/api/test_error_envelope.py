def test_unknown_repository_returns_not_found_envelope(client):
    response = client.get("/repository/not-an-object-id")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"]
    assert error["request_id"] == response.headers.get("x-request-id")
    assert "details" in error


def test_invalid_ingest_body_returns_validation_envelope(client):
    response = client.post("/repository/ingest", json={})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["message"] == "Request validation failed"
    assert error["request_id"] == response.headers.get("x-request-id")
    assert error["details"]["errors"]
