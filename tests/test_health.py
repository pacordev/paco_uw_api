from fastapi.testclient import TestClient


def test_health_round_trips_the_db(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
