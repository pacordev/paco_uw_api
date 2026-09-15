"""Covers app/body_limit.py's two layers: the Content-Length fast path, and the
byte-counting backstop for a body that never declares its length up front (e.g. chunked
transfer-encoding). See that module's docstring for why both exist.
"""

from fastapi.testclient import TestClient


def test_normal_small_request_is_unaffected(client: TestClient) -> None:
    resp = client.get("/products")
    assert resp.status_code == 200


def test_oversized_body_with_content_length_413(client: TestClient) -> None:
    # httpx computes Content-Length up front for a plain json= payload, so this exercises
    # the fast path - the request is rejected without the body ever being read.
    resp = client.post("/quotes", json={"product_code": "x" * 200_000})
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"]


def test_oversized_streamed_body_without_content_length_413(client: TestClient) -> None:
    # content=<generator> makes httpx stream the body with no Content-Length header at all
    # (chunked transfer-encoding) - only the backstop byte-counter can catch this one.
    def stream():
        chunk = b"x" * 20_000
        for _ in range(10):  # 200,000 bytes total, well over the 100,000 default limit
            yield chunk

    resp = client.post(
        "/quotes", content=stream(), headers={"Content-Type": "application/json"}
    )
    assert resp.status_code == 413
    assert "too large" in resp.json()["detail"]


def test_body_within_limit_is_not_rejected(client: TestClient) -> None:
    # a bad product_code still reaches the real handler and 404s - proving this request
    # was never touched by the size limit at all
    resp = client.post("/quotes", json={"product_code": "BOGUS_PRODUCT"})
    assert resp.status_code == 404
