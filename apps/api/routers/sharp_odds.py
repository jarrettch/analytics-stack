"""Complete, paced SharpAPI snapshots. No conditional requests or cached rows."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

import httpx

from .utils import get_http_client

logger = logging.getLogger(__name__)
SHARP_ODDS_URL = "https://api.sharpapi.io/api/v1/odds"
MAX_PAGES = 40
MAX_ATTEMPTS = 4
REQUEST_INTERVAL = 5.0

# Shared by every caller in this API process, including future UFC collection.
_request_lock = asyncio.Lock()
_next_request_at = 0.0


class SharpOddsError(ValueError):
    """The provider did not return a complete observation."""


@dataclass
class SharpSweep:
    pages_fetched: int = 0
    requests_made: int = 0
    rate_limit: dict = field(default_factory=lambda: {
        "remaining": None, "reset_s": None, "throttled_waits": 0,
    })


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _retry_after(value: str | None) -> float:
    seconds = _number(value)
    if seconds is not None:
        return max(0.0, seconds)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return 0.0


async def _request_page(params: dict, sweep: SharpSweep) -> dict:
    global _next_request_at
    key = os.getenv("SHARP_API_KEY") or os.getenv("SHARPAPI_KEY")
    if not key or not key.strip():
        raise SharpOddsError("SHARP_API_KEY is not configured")

    for attempt in range(MAX_ATTEMPTS):
        async with _request_lock:
            delay = max(0.0, _next_request_at - time.monotonic())
            if delay:
                sweep.rate_limit["throttled_waits"] += 1
                await asyncio.sleep(delay)
            started = time.monotonic()
            _next_request_at = started + REQUEST_INTERVAL
            sweep.requests_made += 1
            try:
                response = await get_http_client().get(
                    SHARP_ODDS_URL, params=params,
                    headers={"X-API-Key": key}, timeout=20.0,
                    follow_redirects=False,
                )
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS - 1:
                    raise SharpOddsError("SharpAPI request failed after retries") from exc
                _next_request_at = max(_next_request_at, time.monotonic() + 5 * 2**attempt)
                logger.warning("SharpAPI transport retry market=%s attempt=%s", params["market"], attempt + 1)
                continue

            remaining = _number(response.headers.get("X-RateLimit-Remaining"))
            reset = _number(response.headers.get("X-RateLimit-Reset"))
            reset_s = max(0.0, reset - time.time()) if reset is not None else None
            sweep.rate_limit.update(remaining=remaining, reset_s=reset_s)
            # Spread the remaining requests through the current window. Never
            # burst faster than the free-tier fallback when headers are absent.
            if remaining is not None and reset_s is not None:
                delay = reset_s / max(1.0, remaining)
                _next_request_at = max(_next_request_at, time.monotonic() + delay)
            logger.info(
                "SharpAPI market=%s status=%s limit=%s remaining=%s reset_s=%s",
                params["market"], response.status_code,
                response.headers.get("X-RateLimit-Limit"), remaining, reset_s,
            )
            if response.status_code == 304:
                raise SharpOddsError("Unexpected 304: archive sweeps require full responses")
            retry = response.status_code == 429 or response.status_code >= 500
            payload = None
            if not retry:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    # Preserve the provider error code, especially for rejected
                    # cursors, without logging an entire opaque cursor URL.
                    try:
                        error = response.json().get("error", {})
                        code = error.get("code", "unknown") if isinstance(error, dict) else "unknown"
                    except (ValueError, AttributeError):
                        code = "unknown"
                    raise SharpOddsError(
                        f"SharpAPI {params['market']} HTTP {response.status_code}: {code}"
                    ) from exc
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                    raise SharpOddsError("Invalid SharpAPI data envelope")
                store = (payload.get("meta") or {}).get("store") or {}
                if not payload["data"]:
                    reason = store.get("reason")
                    retry = reason in {"warming", "store_empty"}
                    if retry:
                        logger.warning("SharpAPI empty store retry: %s", store)
                    elif reason != "no_match":
                        raise SharpOddsError("Empty SharpAPI page without authoritative no_match")
            if not retry:
                return payload
            if attempt == MAX_ATTEMPTS - 1:
                raise SharpOddsError("SharpAPI page unavailable after retries")
            backoff = max(5 * 2**attempt, _retry_after(response.headers.get("Retry-After")))
            _next_request_at = max(_next_request_at, time.monotonic() + backoff)

    raise SharpOddsError("SharpAPI retry budget exhausted")


async def fetch_sharp_odds(league: str, market: str, *, sweep: SharpSweep) -> list[dict]:
    """Return every cursor page, or raise without exposing a partial market."""
    params = {
        "league": league, "market": market, "sportsbook": "draftkings,fanduel",
        "is_live": "false", "is_main_line": "true", "limit": 200,
    }
    rows: list[dict] = []
    cursors: set[str] = set()
    for _ in range(MAX_PAGES):
        payload = await _request_page(params, sweep)
        sweep.pages_fetched += 1
        pagination = payload.get("pagination")
        if not isinstance(pagination, dict) or type(pagination.get("has_more")) is not bool:
            raise SharpOddsError("SharpAPI pagination is missing has_more")
        rows.extend(payload["data"])
        if not pagination["has_more"]:
            return rows
        cursor = pagination.get("next_cursor")
        if not isinstance(cursor, str) or not cursor or cursor in cursors or not payload["data"]:
            raise SharpOddsError("SharpAPI pagination has a missing or repeated cursor")
        cursors.add(cursor)
        params["cursor"] = cursor
    logger.error("SharpAPI page cap reached: league=%s market=%s pages=%s", league, market, MAX_PAGES)
    raise SharpOddsError("SharpAPI page cap reached; collection incomplete")
