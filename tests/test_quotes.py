import uuid
from collections.abc import Callable

import asyncpg
from fastapi.testclient import TestClient


def test_create_quote_returns_id_and_token(create_quote: Callable[..., tuple[int, str]]) -> None:
    quote_id, token = create_quote()
    assert isinstance(quote_id, int)
    uuid.UUID(token)  # raises if it isn't a real UUID


def test_create_quote_unknown_product_code_404(client: TestClient) -> None:
    resp = client.post("/quotes", json={"product_code": "BOGUS"})
    assert resp.status_code == 404


def test_submit_answers_happy_path(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/answers",
        headers={"X-Quote-Token": token},
        json={
            "answers": [
                {"question_code": "Q_SMOKER", "answer_text": "false"},
                {"question_code": "Q_BMI", "answer_text": "24"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["quote_id"] == quote_id
    assert {a["question_code"]: a["answer_text"] for a in body["answers"]} == {
        "Q_SMOKER": "false",
        "Q_BMI": "24",
    }


def test_submit_answers_upsert_corrects_value(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    headers = {"X-Quote-Token": token}
    client.post(
        f"/quotes/{quote_id}/answers",
        headers=headers,
        json={"answers": [{"question_code": "Q_SPORTS", "answer_text": "true"}]},
    )
    resp = client.post(
        f"/quotes/{quote_id}/answers",
        headers=headers,
        json={"answers": [{"question_code": "Q_SPORTS", "answer_text": "false"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["answers"][0]["answer_text"] == "false"


def test_submit_answers_unknown_quote_id_404(client: TestClient, bogus_token: str) -> None:
    resp = client.post(
        "/quotes/999999/answers",
        headers={"X-Quote-Token": bogus_token},
        json={"answers": [{"question_code": "Q_SMOKER", "answer_text": "true"}]},
    )
    assert resp.status_code == 404


def test_submit_answers_wrong_token_gives_same_404_as_unknown_quote(
    client: TestClient, create_quote: Callable[..., tuple[int, str]], bogus_token: str
) -> None:
    quote_id, _real_token = create_quote()
    body = {"answers": [{"question_code": "Q_SMOKER", "answer_text": "true"}]}

    wrong_token = client.post(
        f"/quotes/{quote_id}/answers", headers={"X-Quote-Token": bogus_token}, json=body
    )
    unknown_quote = client.post(
        "/quotes/999999/answers", headers={"X-Quote-Token": bogus_token}, json=body
    )

    assert wrong_token.status_code == unknown_quote.status_code == 404
    # same message on purpose - a wrong token shouldn't confirm the quote_id is real
    assert wrong_token.json()["detail"] == f"Unknown quote_id: {quote_id}"
    assert unknown_quote.json()["detail"] == "Unknown quote_id: 999999"


def test_submit_answers_unknown_question_code_404(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/answers",
        headers={"X-Quote-Token": token},
        json={"answers": [{"question_code": "Q_NOPE", "answer_text": "x"}]},
    )
    assert resp.status_code == 404


def test_submit_answers_invalid_answer_text_422(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/answers",
        headers={"X-Quote-Token": token},
        json={"answers": [{"question_code": "Q_SMOKER", "answer_text": "maybe"}]},
    )
    assert resp.status_code == 422
    assert "not a valid boolean" in resp.json()["detail"]


def test_submit_answers_empty_batch_400(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/answers", headers={"X-Quote-Token": token}, json={"answers": []}
    )
    assert resp.status_code == 400


def test_submit_answers_atomic_rollback_on_mixed_batch(
    client: TestClient,
    create_quote: Callable[..., tuple[int, str]],
    db_fetch: Callable[..., list[asyncpg.Record]],
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/answers",
        headers={"X-Quote-Token": token},
        json={
            "answers": [
                {"question_code": "Q_BMI", "answer_text": "30"},  # valid on its own
                {"question_code": "Q_HOSP", "answer_text": "maybe"},  # invalid boolean
            ]
        },
    )
    assert resp.status_code == 422

    rows = db_fetch("SELECT 1 FROM quote_answer WHERE quote_id = $1", quote_id)
    assert rows == []  # neither answer made it in, not even the valid one
