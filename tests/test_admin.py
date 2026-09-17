"""GET /products/{code}/rules - the read side of app/admin.py's rule catalog, added
alongside underwritting_web's Browse catalog feature. Admin-only, same X-Admin-Key model
as the POST endpoints, since a rule's conditions are the exact thresholds ("BMI > 32 ->
decline") that would otherwise hand an applicant the answer key.

The rest of app/admin.py (POST /products, .../questions, .../rules) is still only covered
by the Postman collection, not pytest - see README.md's "Gap" note under "Running the
test suite".
"""

from fastapi.testclient import TestClient

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-key"}


def test_list_product_rules_happy_path(client: TestClient) -> None:
    resp = client.get("/products/LIFE_SIMPLE/rules", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    rules = resp.json()

    # the 7 rules seeded in sql/phase6_seed_data.sql for LIFE_SIMPLE
    assert len(rules) == 7
    names = {r["name"] for r in rules}
    assert names == {
        "Smoker",
        "Cancer history",
        "BMI too low",
        "BMI too high",
        "Recent hospitalization",
        "High risk sports",
        "Smoker with high BMI",
    }

    compound = next(r for r in rules if r["name"] == "Smoker with high BMI")
    assert compound["outcome"] == "decline"
    assert compound["stop_evaluation"] is False
    assert compound["product_code"] == "LIFE_SIMPLE"
    assert {c["question_code"] for c in compound["conditions"]} == {"Q_SMOKER", "Q_BMI"}

    stop_rule = next(r for r in rules if r["name"] == "Cancer history")
    assert stop_rule["stop_evaluation"] is True


def test_list_product_rules_unknown_product_404(client: TestClient) -> None:
    resp = client.get("/products/NOT_A_REAL_PRODUCT/rules", headers=ADMIN_HEADERS)
    assert resp.status_code == 404
    assert isinstance(resp.json()["detail"], str)


def test_list_product_rules_wrong_key_401(client: TestClient) -> None:
    resp = client.get("/products/LIFE_SIMPLE/rules", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 401


def test_list_product_rules_missing_key_422(client: TestClient) -> None:
    resp = client.get("/products/LIFE_SIMPLE/rules")
    assert resp.status_code == 422
    assert isinstance(resp.json()["detail"], str)
