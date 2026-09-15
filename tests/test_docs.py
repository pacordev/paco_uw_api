"""EXPOSE_API_DOCS defaults to enabled (see app/main.py), which is what conftest.py leaves
it at for this whole session - matches real local dev, where docs should just work. The
disabled branch (used in prod, render.yaml sets EXPOSE_API_DOCS=false) is verified separately
by hand, since toggling it here would mean re-importing app.main with a different env var
mid-session rather than a simple runtime flag flip - not worth the complexity for one flag
that's this easy to eyeball live (curl /docs against a server started with it set to false).
"""

from fastapi.testclient import TestClient


def test_docs_are_reachable_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200
    assert client.get("/openapi.json").status_code == 200
