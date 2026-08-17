"""Redacted, cached usage data for external API providers."""
from __future__ import annotations

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, HTTPException

from routers.utils import get_http_client

router = APIRouter(prefix="/api/usage", tags=["usage"])

SGO_USAGE_URL = "https://api.sportsgameodds.com/v2/account/usage"
USAGE_CACHE_TTL = 60
# Forced refreshes still reuse a cache younger than this, so the public
# endpoint can't be used to hammer SGO's shared per-minute request limit.
USAGE_REFRESH_FLOOR = 10
UTC = ZoneInfo("UTC")


def _billing_cycle_day() -> int:
    """Day of month the SGO cycle resets; re-anchor via env on plan changes."""
    try:
        day = int(os.getenv("SGO_BILLING_CYCLE_DAY", "8"))
    except ValueError:
        return 8
    return day if 1 <= day <= 28 else 8

_usage_cache: dict | None = None
_usage_cache_time = 0.0


def _value(data: dict, kebab_key: str, camel_key: str):
    """Read either the current or legacy SGO rate-limit field name."""
    return data.get(camel_key, data.get(kebab_key))


def _normalize_number(value):
    if value in (None, "n/a", "unlimited"):
        return value
    try:
        number = float(value)
        return int(number) if number.is_integer() else number
    except (TypeError, ValueError):
        return None


def normalize_sgo_usage(payload: dict) -> dict:
    """Return only display-safe account data in a stable frontend shape."""
    data = payload.get("data")
    if not payload.get("success") or not isinstance(data, dict):
        raise ValueError(payload.get("error") or "Invalid usage response")

    rate_limits = data.get("rateLimits", {})
    intervals = []
    for key in ("per-minute", "per-hour", "per-day", "per-month"):
        values = rate_limits.get(key, {})
        if not isinstance(values, dict):
            values = {}
        intervals.append(
            {
                "key": key,
                "label": key.removeprefix("per-").capitalize(),
                "requests": {
                    "current": _normalize_number(
                        _value(values, "current-requests", "currentIntervalRequests")
                    ),
                    "max": _normalize_number(
                        _value(values, "max-requests", "maxRequestsPerInterval")
                    ),
                },
                "entities": {
                    "current": _normalize_number(
                        _value(values, "current-entities", "currentIntervalEntities")
                    ),
                    "max": _normalize_number(
                        _value(values, "max-entities", "maxEntitiesPerInterval")
                    ),
                },
                "resetsAt": _value(
                    values, "current-interval-end-time", "currentIntervalEndTime"
                ),
            }
        )

    return {
        "provider": "SportsGameOdds",
        "tier": data.get("tier", "unknown"),
        "active": bool(data.get("isActive", False)),
        "billingCycleDay": _billing_cycle_day(),
        "fetchedAt": datetime.now(UTC).isoformat(),
        "intervals": intervals,
    }


@router.get("/sports-game-odds")
async def sports_game_odds_usage(refresh: bool = False):
    """Fetch SGO quota usage without exposing account or credential fields."""
    global _usage_cache, _usage_cache_time

    now = time.monotonic()
    max_age = USAGE_REFRESH_FLOOR if refresh else USAGE_CACHE_TTL
    if _usage_cache and now - _usage_cache_time < max_age:
        return {**_usage_cache, "cached": True}

    api_key = os.getenv("SPORTS_GAME_ODDS_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="SportsGameOdds is not configured")

    try:
        response = await get_http_client().get(
            SGO_USAGE_URL,
            headers={"x-api-key": api_key},
        )
        response.raise_for_status()
        normalized = normalize_sgo_usage(response.json())
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="SportsGameOdds usage is temporarily unavailable",
        ) from exc

    _usage_cache = normalized
    _usage_cache_time = now
    return {**normalized, "cached": False}
