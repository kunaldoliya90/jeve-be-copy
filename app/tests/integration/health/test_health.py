"""Health endpoint and API routing, end to end through the application."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/api/jeve/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_not_served_outside_api_prefix(client: TestClient) -> None:
    assert client.get("/health/").status_code == 404


def test_docs_are_served_under_api_prefix_in_development(client: TestClient) -> None:
    assert client.get("/api/jeve/docs").status_code == 200
    assert client.get("/api/jeve/openapi.json").status_code == 200
