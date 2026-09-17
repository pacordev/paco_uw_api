"""asyncpg connection pool, created once at startup and reused for every request.

We connect to Neon's *direct* (unpooled) connection string, not the PgBouncer-backed
pooled one - Render runs us as a long-lived process, not serverless functions, so we
don't need PgBouncer's connection-churn protection, and skipping it avoids the
transaction-mode-pooling vs. asyncpg-prepared-statements headache entirely.
"""

import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def connect() -> None:
    global _pool
    database_url = os.environ["DATABASE_URL"]
    # schema is `myins`, not `public` - see sql/phase1_core_schema.sql in the underwritting
    # repo. Sent as a startup-packet param here as a harmless belt-and-suspenders, but the
    # real guarantee is server-side: `ALTER DATABASE ... SET search_path TO myins`, run once
    # directly on the database itself. That's what makes this survive asyncpg's default
    # RESET ALL on every connection release back to the pool - a client-set search_path gets
    # wiped by that reset, but the database's own configured default does not. It's also the
    # only thing that works at all against Neon, whose connection proxy silently drops this
    # server_settings startup param instead of forwarding it to Postgres.
    _pool = await asyncpg.create_pool(
        database_url, min_size=1, max_size=10, server_settings={"search_path": "myins"}
    )


async def disconnect() -> None:
    if _pool is not None:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    # will only be None if called before startup/lifespan has run - that's a bug, not
    # a normal runtime condition, so let it blow up loudly instead of hiding it
    assert _pool is not None, "DB pool not initialized - is the app lifespan running?"
    return _pool
