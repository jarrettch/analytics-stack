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

# Every collector's shared offset supports exactly today and tomorrow. Sports
# with different schedules can expose a separate bounded lookahead. Annotated
# (not a bare Query default) keeps 0 as the real Python default, so collector
# tests can still call cron_fetch_odds() directly.
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


def _require_bearer(
    env_var: str,
    credentials: HTTPAuthorizationCredentials | None,
    unconfigured: str,
    unauthorized: HTTPException,
) -> None:
    # Compare against the raw value, but treat whitespace-only as unconfigured:
    # a fat-fingered secret should error, not become a guessable one.
    expected = os.getenv(env_var)
    if not expected or not expected.strip():
        raise HTTPException(status_code=503, detail=unconfigured)

    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized

    # Compare as bytes; compare_digest rejects str containing non-ASCII.
    if not secrets.compare_digest(
        credentials.credentials.encode("utf-8"), expected.encode("utf-8")
    ):
        raise unauthorized


def require_cron_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_cron_bearer),
) -> None:
    """Reject cron requests that don't carry the shared bearer token.

    Fails closed: an unset CRON_SECRET answers 503 instead of waving traffic
    through, so forgetting the Render env var can't silently reopen the routes.
    """
    _require_bearer(
        CRON_SECRET_ENV, credentials, "Cron authentication is not configured", _UNAUTHORIZED
    )


# A separate secret from CRON_SECRET: this one is typed into a browser.
PREP_UPLOAD_SECRET_ENV = "PREP_UPLOAD_SECRET"

_prep_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="PrepUploadSecret",
    description="Shared secret from the PREP_UPLOAD_SECRET environment variable.",
)

_PREP_UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="Invalid or missing upload credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def require_prep_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_prep_bearer),
) -> None:
    """Reject board uploads that don't carry the upload token. Fails closed like the cron check."""
    _require_bearer(
        PREP_UPLOAD_SECRET_ENV, credentials, "Upload authentication is not configured",
        _PREP_UNAUTHORIZED,
    )
