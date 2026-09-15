from fastapi.testclient import TestClient


def test_list_products_includes_seeded_products(client: TestClient) -> None:
    resp = client.get("/products")
    assert resp.status_code == 200
    codes = {p["code"] for p in resp.json()}
    # from sql/phase6_seed_data.sql - just spot-check a couple, not all 8
    assert {"LIFE_SIMPLE", "AUTO_BASIC"} <= codes


def test_get_product_questions_ordered_and_shaped(client: TestClient) -> None:
    resp = client.get("/products/LIFE_SIMPLE/questions")
    assert resp.status_code == 200
    questions = resp.json()

    assert [q["question_code"] for q in questions] == [
        "Q_SMOKER",
        "Q_CANCER",
        "Q_BMI",
        "Q_HOSP",
        "Q_SPORTS",
    ]
    assert [q["sequence"] for q in questions] == [1, 2, 3, 4, 5]

    # applicant-facing endpoint - expected_answer must never show up here
    assert all("expected_answer" not in q for q in questions)


def test_get_product_questions_unknown_product_404(client: TestClient) -> None:
    resp = client.get("/products/NOPE/questions")
    assert resp.status_code == 404
