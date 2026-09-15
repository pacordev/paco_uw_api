from collections.abc import Callable

import asyncpg
from fastapi.testclient import TestClient

# LIFE_SIMPLE has a 'Smoker with high BMI' rule -> decline (see sql/phase6_seed_data.sql),
# which is the highest-severity outcome, so both models should agree on it.
_DECLINE_ANSWERS = {
    "answers": [
        {"question_code": "Q_SMOKER", "answer_text": "true"},
        {"question_code": "Q_CANCER", "answer_text": "false"},
        {"question_code": "Q_BMI", "answer_text": "35"},
        {"question_code": "Q_HOSP", "answer_text": "false"},
        {"question_code": "Q_SPORTS", "answer_text": "false"},
    ]
}


def test_evaluate_before_any_answers_defaults_to_accept(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/evaluate",
        headers={"X-Quote-Token": token},
        json={"strategy": "full"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "accept"
    assert body["quote_id"] == quote_id
    assert body["strategy"] == "full"


def test_evaluate_decline_rule_fires_under_both_strategies(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    headers = {"X-Quote-Token": token}
    client.post(f"/quotes/{quote_id}/answers", headers=headers, json=_DECLINE_ANSWERS)

    full = client.post(f"/quotes/{quote_id}/evaluate", headers=headers, json={"strategy": "full"})
    short_circuit = client.post(
        f"/quotes/{quote_id}/evaluate", headers=headers, json={"strategy": "short_circuit"}
    )

    assert full.status_code == short_circuit.status_code == 200
    assert full.json()["outcome"] == "decline"
    assert short_circuit.json()["outcome"] == "decline"


def test_evaluate_keeps_every_run_as_history(
    client: TestClient,
    create_quote: Callable[..., tuple[int, str]],
    db_fetch: Callable[..., list[asyncpg.Record]],
) -> None:
    quote_id, token = create_quote()
    headers = {"X-Quote-Token": token}

    client.post(f"/quotes/{quote_id}/evaluate", headers=headers, json={"strategy": "full"})
    client.post(f"/quotes/{quote_id}/evaluate", headers=headers, json={"strategy": "full"})

    rows = db_fetch(
        "SELECT id FROM quote_evaluation WHERE quote_id = $1 AND strategy = 'full'", quote_id
    )
    assert len(rows) == 2  # re-running the same strategy adds a row, doesn't overwrite

    latest = db_fetch("SELECT outcome FROM quote_latest_evaluation WHERE quote_id = $1", quote_id)
    assert len(latest) == 1  # the view still only surfaces the newest per strategy


def test_evaluate_unknown_quote_id_404(client: TestClient, bogus_token: str) -> None:
    resp = client.post(
        "/quotes/999999/evaluate",
        headers={"X-Quote-Token": bogus_token},
        json={"strategy": "full"},
    )
    assert resp.status_code == 404


def test_evaluate_wrong_token_gives_same_404_as_unknown_quote(
    client: TestClient, create_quote: Callable[..., tuple[int, str]], bogus_token: str
) -> None:
    quote_id, _real_token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/evaluate",
        headers={"X-Quote-Token": bogus_token},
        json={"strategy": "full"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == f"Unknown quote_id: {quote_id}"


def test_evaluate_invalid_strategy_422(
    client: TestClient, create_quote: Callable[..., tuple[int, str]]
) -> None:
    quote_id, token = create_quote()
    resp = client.post(
        f"/quotes/{quote_id}/evaluate",
        headers={"X-Quote-Token": token},
        json={"strategy": "bogus"},
    )
    assert resp.status_code == 422
