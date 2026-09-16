"""Every error response on this API is supposed to be {"detail": <string>} - but FastAPI's
own request validation (bad enum value, missing required header, etc.) puts a *list* of
{loc, msg, type} dicts under "detail" by default, while every hand-written HTTPException here
puts a plain string. app/main.py's RequestValidationError handler flattens that list to a
string so both cases match. These tests lock that shape in from both sides.
"""

from collections.abc import Callable

from fastapi.testclient import TestClient


def test_fastapi_validation_error_detail_is_a_string(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    # "bogus" isn't a valid Strategy literal - this is FastAPI's own request validation,
    # not a hand-rolled HTTPException, so it's the case that used to return a list.
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/evaluate",
        headers={"X-Quote-Token": token},
        json={"strategy": "bogus"},
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "strategy" in detail


def test_missing_admin_header_detail_is_a_string(client: TestClient) -> None:
    # missing X-Admin-Key never reaches app/admin.py's own check - FastAPI's request
    # validation rejects it first for being a required-but-absent header.
    resp = client.post("/products", json={"code": "X", "name": "X"})
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "X-Admin-Key" in detail


def test_hand_written_error_detail_is_also_a_string(client: TestClient) -> None:
    resp = client.get("/products/NOT_A_REAL_PRODUCT/questions")
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)
