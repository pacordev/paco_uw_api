"""FastAPI app for the underwriting rules engine - a thin layer over the SQL in the
`underwritting` repo. No business logic here, just HTTP in/out around the DB functions.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app import db
from app.admin import router as admin_router
from app.body_limit import MaxBodySizeMiddleware, max_body_bytes_from_env
from app.limiter import limiter, rate_limit_exceeded_handler
from app.models import ErrorOut
from app.products import router as products_router
from app.quotes import router as quotes_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.connect()
    yield
    await db.disconnect()


# Interactive docs (/docs, /redoc) and the raw schema (/openapi.json) hand anyone the full
# API shape - fine for local dev, not something a public prod deployment needs exposed.
# EXPOSE_API_DOCS defaults on so local dev/tests need no extra config; render.yaml sets it
# to "false" for the deployed service.
_docs_enabled = os.environ.get("EXPOSE_API_DOCS", "true").lower() not in ("false", "0")

app = FastAPI(
    title="Underwriting Rules Engine API",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# No wildcard fallback on purpose - an unset ALLOWED_ORIGINS should fail loudly at startup
# instead of silently opening CORS to every origin. Set it explicitly per environment (see
# .env.example for local dev, render.yaml for prod).
_origins_raw = os.environ["ALLOWED_ORIGINS"]
_origins = [o.strip() for o in _origins_raw.split(",") if o.strip()]
assert _origins, "ALLOWED_ORIGINS is set but empty - need at least one allowed origin"

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Quote-Token", "X-Admin-Key"],
)

# added after CORSMiddleware so CORS ends up as the outer layer - a 429 response still
# needs CORS headers on it, or a browser client just sees an opaque CORS failure instead
# of the real rate-limit error
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


# FastAPI's own request validation (bad/missing body field, missing header, etc.) puts a
# *list* of {loc, msg, type} dicts under "detail" by default - every hand-written error on
# this API puts a plain *string* there instead. Flattening it to a string here means every
# error response, no matter where it comes from, has the exact same {"detail": <string>} shape.
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    messages = [f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})


# overrides FastAPI's auto-added 422 doc (HTTPValidationError, a list under detail) with the
# real shape - applied to every router so no route is left pointing at the stale schema
# (FastAPI adds a 422 to any route with declared parameters, even a path param that can't
# actually fail validation, like products_router's `code: str`)
_error_responses = {422: {"model": ErrorOut}}
app.include_router(products_router, responses=_error_responses)
app.include_router(quotes_router, responses=_error_responses)
app.include_router(admin_router, responses=_error_responses)


@app.get("/health")
async def health() -> dict[str, str]:
    # round-trips the DB so Render's health check actually catches a dead connection,
    # not just "the process is up"
    pool = db.get_pool()
    await pool.fetchval("SELECT 1")
    return {"status": "ok"}


# Kept under its own name before wrapping below, so tests/tooling that need the actual
# FastAPI instance (e.g. its .state) still have a way to reach it - see
# tests/test_rate_limiting.py.
fastapi_app = app

# Wraps the finished FastAPI app directly (not app.add_middleware) so this is the true
# outermost ASGI layer - an oversized body gets rejected before even CORS/rate-limit
# middleware runs. Reassigns the module-level `app` uvicorn imports; see app/body_limit.py
# for why a 413 from here doesn't carry CORS headers, unlike the 429 handler above.
app = MaxBodySizeMiddleware(app, max_bytes=max_body_bytes_from_env())
