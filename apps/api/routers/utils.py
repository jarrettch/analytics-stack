"""Shared helpers for sport routers (NBA, MLB, future NHL).

Consolidates cache management, SGO odds extraction, win probability math,
Supabase odds persistence, time formatting, and a module-level httpx client.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

logger = logging.getLogger(__name__)

CACHE_TTL_LIVE = 20
CACHE_TTL_DEFAULT = 60
CACHE_TTL_ODDS = 1800

supabase = None
if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))


class Cache:
    """Per-router TTL cache. Each router instantiates its own keyed by source name."""

    def __init__(self, ttls: dict[str, int]):
        self._entries = {
            key: {"data": None, "last_fetch": 0, "ttl": ttl} for key, ttl in ttls.items()
        }

    def get(self, key: str):
        entry = self._entries[key]
        if entry["data"] is not None and (time.time() - entry["last_fetch"] < entry["ttl"]):
            return entry["data"]
        return None

    def set(self, key: str, data, ttl: int | None = None):
        entry = self._entries[key]
        if data is not None:
            entry["data"] = data
            entry["last_fetch"] = time.time()
            if ttl:
                entry["ttl"] = ttl

    def get_stale(self, key: str):
        return self._entries[key]["data"]

    def set_ttl(self, key: str, ttl: int):
        self._entries[key]["ttl"] = ttl


_http_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    """Module-level httpx client, created lazily on first use."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=15.0)
    return _http_client


async def close_http_client():
    """Close the shared client. Call from FastAPI shutdown."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


def implied_prob(odds: float) -> float:
    if odds == 0:
        return 0.0
    return -odds / (-odds + 100) if odds < 0 else 100 / (odds + 100)


def de_vig_ml(home_ml: float, away_ml: float) -> tuple[float, float]:
    """De-vigged win probabilities. Returns (home_prob, away_prob)."""
    raw = implied_prob(home_ml) + implied_prob(away_ml)
    return implied_prob(home_ml) / raw, implied_prob(away_ml) / raw


def calculate_win_probability_from_odds(
    away_ml, home_ml, away_spread, home_spread
) -> tuple[float | None, float | None]:
    """Win probabilities from market odds. ML preferred; spread fallback; (None, None) if no signal."""
    if away_ml is not None and home_ml is not None and abs(away_ml - home_ml) >= 20:
        home_prob, away_prob = de_vig_ml(home_ml, away_ml)
        return away_prob, home_prob

    if away_spread is not None and home_spread is not None:
        if away_spread < 0 and home_spread > 0:
            underdog_prob = 1 / (1 + 10 ** (abs(away_spread) / 15))
            return underdog_prob, 1 - underdog_prob
        if home_spread < 0 and away_spread > 0:
            underdog_prob = 1 / (1 + 10 ** (abs(home_spread) / 15))
            return 1 - underdog_prob, underdog_prob
        return None, None  # malformed spread pair

    return None, None


# BetOnline is sharper than the consensus average and is available on the SGO free tier.
# Circa was the original preference but isn't on the free plan, so it always fell through.
PREFERRED_BOOK = "betonline"


def sgo_book_val(odds: dict, odd_key: str, field: str):
    """Extract a value from an SGO odds object.

    Prefers PREFERRED_BOOK; falls back to consensus book value.
    field is one of: "odds", "spread", "overUnder".
    """
    odd = odds.get(odd_key, {})
    book = odd.get("byBookmaker", {}).get(PREFERRED_BOOK, {})
    if book.get("available"):
        return book.get(field)
    fallback_key = {
        "odds": "bookOdds",
        "spread": "bookSpread",
        "overUnder": "bookOverUnder",
    }.get(field, "bookOdds")
    return odd.get(fallback_key)


def to_num(val):
    """Parse a string/numeric value to int or float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return None


def format_et_time(utc_str: str) -> str:
    """Convert UTC ISO string to '7:05 PM ET' display format."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%-I:%M %p ET")
    except Exception:
        return ""


def local_time_hhmm(utc_str: str) -> str:
    """Convert UTC ISO string to 'HH:MM' in ET."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%H:%M")
    except Exception:
        return ""


def load_persisted_odds(game_date: date | None = None, table_name: str = "nba_odds") -> dict:
    """Load persisted odds from Supabase.

    With no date, queries yesterday/today/tomorrow ET to absorb timezone boundaries
    and post-midnight refreshes. `table_name` defaults to the shared `nba_odds`
    table that currently holds NBA + MLB; pass an explicit name once per-sport
    tables exist.
    """
    if not supabase:
        logger.warning("Supabase not configured, skipping odds load")
        return {}

    if game_date:
        dates_to_query = [game_date]
    else:
        today_et = datetime.now(ET).date()
        dates_to_query = [today_et - timedelta(days=1), today_et, today_et + timedelta(days=1)]

    odds_map = {}
    for d in dates_to_query:
        try:
            response = (
                supabase.table(table_name)
                .select("matchup_key, odds_data")
                .eq("game_date", d.isoformat())
                .execute()
            )
            for row in response.data:
                odds_map[(d.isoformat(), row["matchup_key"])] = row["odds_data"]
        except Exception as e:
            logger.warning(f"Failed to load persisted odds for {d}: {e}")
    logger.info(f"Loaded {len(odds_map)} odds from {table_name} for {[d.isoformat() for d in dates_to_query]}")
    return odds_map


def save_persisted_odds(odds_map: dict, game_date: date | None = None, table_name: str = "nba_odds"):
    """Upsert odds to Supabase (keyed by game_date + matchup_key)."""
    if not supabase:
        return

    target_date = game_date or datetime.now(ET).date()
    try:
        rows = [
            {
                "game_date": target_date.isoformat(),
                "matchup_key": matchup_key,
                "odds_data": odds_data,
            }
            for matchup_key, odds_data in odds_map.items()
        ]
        supabase.table(table_name).upsert(rows, on_conflict="game_date,matchup_key").execute()
        logger.info(f"Persisted {len(rows)} matchup odds to {table_name} for {target_date}")
    except Exception as e:
        logger.warning(f"Failed to persist odds: {e}")
