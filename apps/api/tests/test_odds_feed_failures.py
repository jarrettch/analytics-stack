from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from routers import mlb, wnba
from routers.utils import CACHE_TTL_DEFAULT, CACHE_TTL_ODDS, Cache


def _mlb_cache() -> Cache:
    return Cache(
        {
            "schedule": CACHE_TTL_DEFAULT,
            "standings": 300,
            "stats": 600,
            "odds": CACHE_TTL_ODDS,
            "pitcher_records": 600,
            "assembled": CACHE_TTL_DEFAULT,
        }
    )


def _wnba_cache() -> Cache:
    return Cache(
        {
            "scoreboard": CACHE_TTL_DEFAULT,
            "standings": 300,
            "advanced_stats": 600,
            "odds": CACHE_TTL_ODDS,
            "assembled": CACHE_TTL_DEFAULT,
        }
    )


def _failed_odds_load(*_args, **_kwargs):
    raise RuntimeError("Failed to load persisted odds")


@pytest.mark.parametrize("with_stale_odds", [False, True])
def test_mlb_today_returns_503_when_odds_read_fails(with_stale_odds: bool):
    cache = _mlb_cache()
    if with_stale_odds:
        cache.set("odds", {("2026-09-22", "AWAY@HOME"): {"homeMoneyline": -110}})
        cache._entries["odds"]["last_fetch"] = 0

    with (
        patch.object(mlb, "cache", cache),
        patch.object(mlb, "get_schedule", AsyncMock(return_value=[])),
        patch.object(mlb, "get_standings_map", AsyncMock(return_value={})),
        patch.object(mlb, "get_stats_map", AsyncMock(return_value={})),
        patch.object(mlb, "load_persisted_odds", _failed_odds_load),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(mlb.get_todays_games())

    assert error.value.status_code == 503
    assert error.value.detail == "MLB odds feed unavailable"


@pytest.mark.parametrize("with_stale_odds", [False, True])
def test_wnba_today_returns_503_when_odds_read_fails(with_stale_odds: bool):
    scoreboard = {"scoreboard": {"games": []}}
    cache = _wnba_cache()
    if with_stale_odds:
        cache.set("odds", {("2026-09-22", "AWAY@HOME"): {"homeMoneyline": -110}})
        cache._entries["odds"]["last_fetch"] = 0

    with (
        patch.object(wnba, "cache", cache),
        patch.object(
            wnba,
            "fetch_wnba_scoreboard",
            AsyncMock(return_value=scoreboard),
        ),
        patch.object(wnba, "get_standings_map", return_value={}),
        patch.object(wnba, "get_advanced_stats_map", return_value={}),
        patch.object(wnba, "load_persisted_odds", _failed_odds_load),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(wnba.get_todays_games())

    assert error.value.status_code == 503
    assert error.value.detail == "WNBA odds feed unavailable"
