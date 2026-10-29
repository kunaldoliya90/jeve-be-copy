"""CORS headers, end to end through the application.

The browser client is served from another origin, so a missing header here is a frontend
that cannot read any answer the API gives.
"""

from fastapi.testclient import TestClient

ALLOWED_ORIGIN = "http://localhost:5173"
OTHER_ORIGIN = "https://not-the-frontend.example.com"


def test_preflight_for_an_upload_is_allowed_from_a_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/jeve/reviews/",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_preflight_for_a_delete_is_allowed(client: TestClient) -> None:
    response = client.options(
        "/api/jeve/reviews/abc/",
        headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "DELETE"},
    )

    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


def test_listing_reviews_carries_the_allow_origin_header(client: TestClient) -> None:
    response = client.get("/api/jeve/reviews/", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN


def test_an_unconfigured_origin_is_not_allowed(client: TestClient) -> None:
    response = client.get("/api/jeve/reviews/", headers={"Origin": OTHER_ORIGIN})

    assert "access-control-allow-origin" not in response.headers
