"""Rate limiting is disabled for the rest of the suite (see conftest.py) since the other
tests fire many requests back-to-back and would trip it for reasons that have nothing to do
with abuse. This file re-enables it just for its own assertions, using a distinct
X-Forwarded-For per test so it doesn't share a bucket with anything else, then turns it back
off in a finally so the rest of the suite is unaffected regardless of pass/fail here.
"""

from contextlib import contextmanager
from typing import Iterator

from fastapi.testclient import TestClient

from app.main import fastapi_app


@contextmanager
def _rate_limiting_enabled(client: TestClient) -> Iterator[None]:
    # client.app is app.main's exported `app`, which is wrapped by MaxBodySizeMiddleware
    # (see app/body_limit.py) and no longer has FastAPI's .state directly - fastapi_app is
    # the underlying instance that still does.
    fastapi_app.state.limiter.enabled = True
    try:
        yield
    finally:
        fastapi_app.state.limiter.enabled = False


def test_quote_creation_is_rate_limited_per_ip(client: TestClient) -> None:
    headers = {"X-Forwarded-For": "203.0.113.5"}  # POST /quotes is limited to 10/minute

    with _rate_limiting_enabled(client):
        for _ in range(10):
            resp = client.post(
                "/quotes", headers=headers, json={"product_code": "LIFE_SIMPLE"}
            )
            assert resp.status_code == 201

        blocked = client.post(
            "/quotes", headers=headers, json={"product_code": "LIFE_SIMPLE"}
        )
        assert blocked.status_code == 429
        assert "Rate limit exceeded" in blocked.json()["detail"]
        assert "Retry-After" in blocked.headers


def test_rate_limit_is_scoped_per_ip_not_global(client: TestClient) -> None:
    with _rate_limiting_enabled(client):
        # a different IP gets its own bucket - not blocked by the previous test's requests
        resp = client.post(
            "/quotes",
            headers={"X-Forwarded-For": "203.0.113.99"},
            json={"product_code": "LIFE_SIMPLE"},
        )
        assert resp.status_code == 201
