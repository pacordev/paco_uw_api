"""Structured logging for rate-limit abuse - one JSON line per 429, so "who's getting
throttled repeatedly" is queryable (grep/jq locally, Render's log search in prod) instead of
invisible. See uw_plan.md's hardening backlog for why this exists.

A dedicated logger with its own handler/formatter, not just logging.warning(...), so the
output is a bare JSON line with nothing else on it - no "WARNING:app.abuse_log:" prefix from
the default format getting in the way of parsing it as JSON.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from starlette.requests import Request

_logger = logging.getLogger("abuse")
_logger.setLevel(logging.INFO)
_logger.propagate = False  # don't also emit through the root logger's default formatting

_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)


def log_rate_limit_exceeded(request: Request, ip: str, limit: str) -> None:
    _logger.info(
        json.dumps(
            {
                "event": "rate_limit_exceeded",
                "ip": ip,
                "method": request.method,
                "path": request.url.path,
                "limit": limit,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
    )
