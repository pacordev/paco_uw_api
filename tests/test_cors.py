from fastapi.testclient import TestClient

# conftest.py sets ALLOWED_ORIGINS=http://localhost:3000 for the test session


def test_cors_preflight_allows_the_configured_origin(client: TestClient) -> None:
    resp = client.options(
        "/quotes",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_cors_preflight_rejects_an_unlisted_origin(client: TestClient) -> None:
    resp = client.options(
        "/quotes",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert "access-control-allow-origin" not in resp.headers
