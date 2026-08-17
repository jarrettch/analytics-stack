"""Shared bearer-token auth for the GitHub Actions cron endpoints.

The cron routes trigger paid provider fetches and append-only Supabase writes,
so they are the one part of this API that must not be publicly callable.
"""
from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

CRON_SECRET_ENV = "CRON_SECRET"

# Every collector supports exactly today and tomorrow, so bound it here rather
# than in five routers where the range could drift apart. Annotated (not a bare
# Query default) keeps 0 as the real Python default, so the collector tests can
# still call cron_fetch_odds() directly.
CronOffset = Annotated[
    int,
    Query(ge=0, le=1, description="ET day to collect: 0 = today, 1 = tomorrow."),
]

# auto_error=False so a missing or malformed header reaches us as None and we
# can answer 401 rather than HTTPBearer's default 403.
_cron_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="CronSecret",
    description="Shared secret from the CRON_SECRET environment variable.",
)

_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Invalid or missing cron credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def require_cron_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_cron_bearer),
) -> None:
    """Reject cron requests that don't carry the shared bearer token.

    Fails closed: an unset CRON_SECRET answers 503 instead of waving traffic
    through, so forgetting the Render env var can't silently reopen the routes.
    """
    expected = os.getenv(CRON_SECRET_ENV)
    if not expected:
        raise HTTPException(
            status_code=503, detail="Cron authentication is not configured"
        )

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _UNAUTHORIZED

    # Compare as bytes; compare_digest rejects str containing non-ASCII.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise _UNAUTHORIZED
