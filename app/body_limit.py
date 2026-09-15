"""Caps request body size at the raw ASGI level - the one thing nothing else in this app
protects against. A public, unauthenticated POST endpoint with no size cap lets anyone send
an arbitrarily large body and make the app spend memory/CPU parsing it before any of our own
validation (or even FastAPI's) gets a chance to reject it.

Two layers, both described in uw_plan.md's hardening backlog:
1. Content-Length fast path - reject immediately if the client honestly declares an
   oversized body, without reading a single byte of it.
2. A real backstop that counts bytes as they actually arrive and aborts the moment the
   running total crosses the limit - catches a missing/lying Content-Length (e.g. chunked
   transfer-encoding), and unlike Starlette's BaseHTTPMiddleware (which buffers the *entire*
   body before your code ever sees it, no matter how large), this never holds more than
   `max_bytes` plus one chunk in memory.

Wraps the whole ASGI app directly (see app/main.py) rather than going through
`app.add_middleware(...)`, so it's the outermost layer - a huge body gets rejected before
even FastAPI's CORS handling runs. That's a deliberate trade-off: unlike the 429 rate-limit
handler, a 413 from this layer does NOT carry CORS headers, so a browser client sees a
network/CORS-shaped error rather than a readable 413 body. Acceptable here since this is a
DoS-shaped protection against abuse, not a normal application error a legitimate frontend
should ever hit in practice.
"""

import json
import os

DEFAULT_MAX_BODY_BYTES = 100_000  # ~100 KB - generous for a 5-8 question answer batch


class MaxBodySizeMiddleware:
    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                pass  # malformed header - let the backstop below catch it instead

        # Buffer only as much as it takes to prove the body is too big, then stop reading
        # from the client entirely - bounded memory regardless of how large the real body is.
        buffered = []
        total = 0
        more_body = True
        while more_body:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            total += len(message.get("body", b""))
            more_body = message.get("more_body", False)
            if total > self.max_bytes:
                await self._reject(send)
                return

        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    async def _reject(self, send) -> None:
        body = json.dumps({"detail": f"Request body too large (max {self.max_bytes} bytes)"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def max_body_bytes_from_env() -> int:
    raw = os.environ.get("MAX_REQUEST_BODY_BYTES")
    return int(raw) if raw else DEFAULT_MAX_BODY_BYTES
