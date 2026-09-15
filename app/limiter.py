"""Shared slowapi Limiter, in its own module rather than app.main so route modules
(app/quotes.py) can import it for @limiter.limit(...) without circularly importing
app.main, which in turn imports those route modules for their routers.

In-memory storage (slowapi's default, no storage_uri set) is the right call for
now: Render's free tier runs a single instance, so there's no state to share
across processes. If this ever moves to a paid tier with multiple instances,
in-memory limits stop being accurate (each instance would enforce its own count)
and this would need a shared backend (e.g. Redis) instead.
"""

import os

from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from starlette.requests import Request
from starlette.responses import JSONResponse


def _client_ip(request: Request) -> str:
    # Render terminates the connection at its edge proxy, so request.client.host
    # would be Render's proxy for every single caller, not the real client - has
    # to key off X-Forwarded-For (which Render sets) or everyone shares one bucket.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# default_limits applies to every route unless a route sets its own @limiter.limit(...)
# (which overrides rather than stacks, per slowapi's override_defaults=True default) -
# this catches GET /products and GET /products/{code}/questions too, cheap scraping
# protection with a generous enough ceiling that no real client hits it.
#
# RATE_LIMIT_ENABLED off by default in tests (tests/conftest.py sets it "false") - the
# contract tests in Phase F create a lot of quotes back-to-back through TestClient, which
# all share one IP bucket ("testclient", since there's no real proxy setting
# X-Forwarded-For), and that's testing something completely different from rate limiting.
_enabled = os.environ.get("RATE_LIMIT_ENABLED", "true").lower() not in ("false", "0")
limiter = Limiter(
    key_func=_client_ip, default_limits=["60/minute"], headers_enabled=True, enabled=_enabled
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    # match the {"detail": ...} shape every other error response on this API uses,
    # instead of slowapi's default {"error": ...}. _inject_headers is "private" but
    # it's the pattern slowapi's own docs point to for a custom handler - it's what
    # adds Retry-After/X-RateLimit-* instead of reimplementing that logic here.
    response = JSONResponse(status_code=429, content={"detail": f"Rate limit exceeded: {exc.detail}"})
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
