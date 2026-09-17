"""Shared fixtures for the API-level contract tests (Phase F).

These hit real HTTP endpoints on a real Postgres database - a dedicated
`underwriting_test` database, rebuilt from scratch once per test session using the
same sql/phase1..7*.sql files (schema + seed data) the `underwritting` repo's own
docker-compose Postgres uses for local dev. No mocking the DB: the whole point of
this phase is to mirror the coverage the SQL test suite already has, but through
the HTTP surface instead of calling the SQL functions directly.

Env vars are set at module load time (before any test file can import app.main) -
app/main.py reads ALLOWED_ORIGINS at *import* time with no wildcard fallback, so it
has to be set before that import happens anywhere.
"""

import asyncio
import os
import uuid
from collections.abc import Callable
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

TEST_DB_NAME = "underwriting_test"
ADMIN_URL = "postgresql://uw_admin:uw_dev_password@localhost:5432/postgres"
TEST_DB_URL = f"postgresql://uw_admin:uw_dev_password@localhost:5432/{TEST_DB_NAME}"

# assumes the sibling repo layout described in README.md ("Project structure")
SQL_DIR = Path(__file__).resolve().parents[2] / "underwritting" / "sql"
SQL_FILES = [
    "phase1_core_schema.sql",
    "phase2_expected_answer.sql",
    "phase3_rules_engine.sql",
    "phase4_evaluation_history.sql",
    "phase5_hardening.sql",
    "phase6_seed_data.sql",
    "phase7_quote_access_token.sql",
    "phase8_evaluation_trigger.sql",
]

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3000"
os.environ["ADMIN_API_KEY"] = "test-admin-key"
# off by default here - these contract tests fire many requests back-to-back through
# TestClient, all sharing one IP bucket. test_rate_limiting.py re-enables it for its own
# assertions and turns it back off afterward, so it doesn't leak into the rest of the suite.
os.environ["RATE_LIMIT_ENABLED"] = "false"


async def _rebuild_test_database() -> None:
    # drop-and-recreate is simpler and more reliable than trying to TRUNCATE
    # everything back to a known state between runs
    admin_conn = await asyncpg.connect(ADMIN_URL)
    try:
        await admin_conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME} WITH (FORCE)")
        await admin_conn.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    finally:
        await admin_conn.close()

    test_conn = await asyncpg.connect(TEST_DB_URL)
    try:
        for filename in SQL_FILES:
            await test_conn.execute((SQL_DIR / filename).read_text())
    finally:
        await test_conn.close()


@pytest.fixture(scope="session", autouse=True)
def test_database() -> None:
    asyncio.run(_rebuild_test_database())


@pytest.fixture(scope="session")
def client(test_database: None):
    from app.main import app  # deferred so DATABASE_URL/ALLOWED_ORIGINS are set first

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_fetch() -> Callable[..., list[asyncpg.Record]]:
    """Sync helper for tests that need to check persisted state directly, e.g.
    proving a rejected batch left nothing behind. TestClient runs the ASGI app in
    its own thread, so a plain asyncio.run() here doesn't collide with it.
    """

    async def _fetch(sql: str, *args: object) -> list[asyncpg.Record]:
        conn = await asyncpg.connect(TEST_DB_URL)
        try:
            await conn.execute("SET search_path TO myins")
            return await conn.fetch(sql, *args)
        finally:
            await conn.close()

    def _fetch_sync(sql: str, *args: object) -> list[asyncpg.Record]:
        return asyncio.run(_fetch(sql, *args))

    return _fetch_sync


@pytest.fixture
def create_quote(client: TestClient) -> Callable[[str], tuple[int, str]]:
    """Factory fixture - each call creates a fresh quote and returns (quote_id, access_token)."""

    def _create(product_code: str = "LIFE_SIMPLE") -> tuple[int, str]:
        resp = client.post("/quotes", json={"product_code": product_code})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        return body["quote_id"], body["access_token"]

    return _create


@pytest.fixture
def bogus_token() -> str:
    """A well-formed but definitely-wrong X-Quote-Token, for negative tests."""
    return str(uuid.uuid4())
