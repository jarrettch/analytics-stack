"""Shared helpers for sport routers (NBA, MLB, future NHL).

Consolidates cache management, SGO odds extraction, win probability math,
Supabase odds persistence, time formatting, and a module-level httpx client.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import date, datetime, timedelta
from typing import Literal
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
PERSISTED_ODDS_READ_ATTEMPTS = 3
PERSISTED_ODDS_RETRY_DELAYS = (0.25, 0.75)

# A slate day runs until 6am ET, not midnight. A 7:10pm PT first pitch is
# 10:10pm ET and can run past 1am ET; rolling at midnight dropped those games
# from the board while they were still being played.
SLATE_ROLLOVER_HOUR = 6

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


def slate_date_et(now: datetime | None = None) -> date:
    """The ET date whose slate should currently be on the board.

    Only for deciding which slate to *display*. Odds capture and the cron
    prefetch endpoints stay on the calendar date: the 2am ET cron passes
    offset=1 to mean "tomorrow", and shifting its baseline would silently make
    that mean "today".
    """
    current = now or datetime.now(ET)
    return (current - timedelta(hours=SLATE_ROLLOVER_HOUR)).date()


def sgo_event_window(
    target: date,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return the UTC bounds for one ET calendar day of SGO events.

    Collection uses calendar dates because providers and persisted odds key
    games that way. It must not use the 6am display-slate rollover. Today's
    window begins now so repeated polls do not pay for games already underway.
    Future-day windows begin at midnight ET so opening lines cover the full day.
    Past dates are rejected because fetching them would rebill completed games.
    """
    current = (now or datetime.now(ET)).astimezone(ET)
    if target < current.date():
        raise ValueError("SGO event windows cannot target a past ET date")
    starts_at = datetime.combine(target, datetime.min.time(), ET)
    if target == current.date():
        starts_at = max(starts_at, current)
    ends_at = datetime.combine(
        target + timedelta(days=1),
        datetime.min.time(),
        ET,
    )
    return starts_at.astimezone(UTC), ends_at.astimezone(UTC)


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


def grade_spread_result(
    away_score: int | float,
    home_score: int | float,
    favorite: str | None,
    spread_value: int | float | None,
) -> bool | Literal["push"] | None:
    """Grade the listed favorite against the spread."""
    if favorite not in {"away", "home"} or spread_value is None:
        return None

    favorite_margin = (
        away_score - home_score
        if favorite == "away"
        else home_score - away_score
    )
    required_margin = abs(spread_value)
    if favorite_margin == required_margin:
        return "push"
    return favorite_margin > required_margin


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


def load_persisted_odds(game_date: date | None = None, *, table_name: str) -> dict:
    """Load persisted odds from Supabase.

    With no date, queries yesterday/today/tomorrow ET to absorb timezone boundaries
    and post-midnight refreshes. `table_name` is required and keyword-only — every
    caller must name the per-sport table explicitly so we can't silently land in
    the wrong one.
    """
    if not supabase:
        logger.warning("Supabase not configured, skipping odds load")
        return {}

    if game_date:
        dates_to_query = [game_date]
    else:
        today_et = datetime.now(ET).date()
        dates_to_query = [today_et - timedelta(days=1), today_et, today_et + timedelta(days=1)]

    date_values = [d.isoformat() for d in dates_to_query]
    last_error: Exception | None = None

    for attempt in range(1, PERSISTED_ODDS_READ_ATTEMPTS + 1):
        try:
            query = supabase.table(table_name).select(
                "game_date,matchup_key,odds_data"
            )
            query = (
                query.eq("game_date", date_values[0])
                if len(date_values) == 1
                else query.in_("game_date", date_values)
            )
            response = query.execute()
            odds_map = {}
            for row in response.data:
                odds_map[(row["game_date"], row["matchup_key"])] = row["odds_data"]
            logger.info(
                "Loaded %s odds from %s for %s",
                len(odds_map),
                table_name,
                date_values,
            )
            return odds_map
        except Exception as exc:
            last_error = exc
            if attempt < PERSISTED_ODDS_READ_ATTEMPTS:
                delay = PERSISTED_ODDS_RETRY_DELAYS[attempt - 1]
                logger.warning(
                    "Persisted odds read failed for %s on attempt %s/%s: %s; retrying in %.2fs",
                    date_values,
                    attempt,
                    PERSISTED_ODDS_READ_ATTEMPTS,
                    exc,
                    delay,
                )
                time.sleep(delay)

    message = (
        f"Failed to load persisted odds from {table_name} for {date_values} "
        f"after {PERSISTED_ODDS_READ_ATTEMPTS} attempts"
    )
    logger.error("%s: %s", message, last_error)
    raise RuntimeError(message) from last_error


def save_persisted_odds(
    odds_map: dict, game_date: date | None = None, *, table_name: str
) -> bool:
    """Upsert odds to Supabase (keyed by game_date + matchup_key).

    Returns whether the rows were written. Failures are logged rather than
    raised; callers that must not report success without a write check the
    result. An empty map is a successful no-op even without Supabase.
    """
    if not odds_map:
        return True
    if not supabase:
        logger.warning("Supabase not configured, %s odds not persisted", table_name)
        return False

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
        return True
    except Exception as e:
        logger.warning(f"Failed to persist odds: {e}")
        return False


def odds_snapshot_hash(odds_data: dict) -> str:
    """Stable digest for consecutive snapshot deduplication."""
    canonical = json.dumps(
        odds_data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_odds_snapshots(
    snapshots: list[dict], *, stats: dict | None = None
) -> int:
    """Append changed normalized odds bundles to the shared history table.

    Deduplication is deliberately consecutive rather than globally unique: if
    a line moves away and later returns to an earlier value, the return is part
    of the movement path and must be retained.
    """
    result = {
        "candidates": len(snapshots),
        "prepared": 0,
        "inserted": 0,
        "deduplicated": 0,
        "persistence_ok": False,
    }

    def finish(**updates) -> int:
        result.update(updates)
        if stats is not None:
            stats.clear()
            stats.update(result)
        return result["inserted"]

    if not snapshots:
        return finish(persistence_ok=True)
    if not supabase:
        return finish()

    prepared = []
    for snapshot in snapshots:
        odds_data = snapshot.get("odds_data") or {}
        if not odds_data:
            continue
        prepared.append({
            **snapshot,
            "snapshot_hash": odds_snapshot_hash(odds_data),
        })

    result["prepared"] = len(prepared)
    if not prepared:
        return finish(persistence_ok=True)

    latest_hashes: dict[tuple[str, str, str, str, str], str] = {}
    groups = {
        (row["sport"], row["provider"], row["game_date"])
        for row in prepared
    }

    try:
        for sport, provider, game_date_value in groups:
            response = (
                supabase.table("odds_snapshots")
                .select("sport,provider,bookmaker,game_date,matchup_key,snapshot_hash,captured_at")
                .eq("sport", sport)
                .eq("provider", provider)
                .eq("game_date", game_date_value)
                .order("captured_at", desc=True)
                .limit(1000)
                .execute()
            )
            for row in response.data:
                key = (
                    row["sport"],
                    row["provider"],
                    row["bookmaker"],
                    str(row["game_date"]),
                    row["matchup_key"],
                )
                latest_hashes.setdefault(key, row["snapshot_hash"])

        inserts = []
        for row in prepared:
            key = (
                row["sport"],
                row["provider"],
                row["bookmaker"],
                str(row["game_date"]),
                row["matchup_key"],
            )
            if latest_hashes.get(key) == row["snapshot_hash"]:
                continue
            inserts.append(row)
            latest_hashes[key] = row["snapshot_hash"]

        if inserts:
            supabase.table("odds_snapshots").insert(inserts).execute()
        logger.info(
            "Odds history: inserted %d changed snapshots, deduplicated %d",
            len(inserts),
            len(prepared) - len(inserts),
        )
        return finish(
            inserted=len(inserts),
            deduplicated=len(prepared) - len(inserts),
            persistence_ok=True,
        )
    except Exception as exc:
        # History collection must never take down the latest-odds cache or the
        # dashboard if the migration has not been applied yet.
        logger.warning("Failed to persist odds snapshots: %s", exc)
        return finish()


SGO_EVENTS_URL = "https://api.sportsgameodds.com/v2/events"

# The six markets every SGO-backed sport pulls: home/away moneyline, home/away
# spread, and the game over/under.
SGO_ODD_IDS = [
    "points-home-game-ml-home", "points-away-game-ml-away",
    "points-home-game-sp-home", "points-away-game-sp-away",
    "points-all-game-ou-over",  "points-all-game-ou-under",
]


async def fetch_sgo_odds(
    *,
    sport: str,
    league_id: str,
    table_name: str,
    tricode_for: Callable[[str], str],
    fallback_tricode_for: Callable[[str], str] | None = None,
    target_date: date | None = None,
    request_time: datetime | None = None,
    extra_odd_ids: Iterable[str] = (),
    include_alt_lines: bool = False,
    include_open_close_odds: bool = False,
    response_consumer: Callable[
        [list[dict], datetime], Awaitable[None] | None
    ] | None = None,
    optional_market_fallback_consumer: Callable[
        [], Awaitable[None] | None
    ] | None = None,
    sharp_market_cache: dict[str, list[dict]] | None = None,
    collection_stats: dict | None = None,
) -> tuple[dict, int]:
    """Fetch and persist SGO odds, using SharpAPI if the SGO request fails.

    Identical for every SGO-backed sport (NBA/NFL/MLB/NHL); the only per-sport inputs are
    the league ID, the Supabase table, and `tricode_for`, which maps an SGO teamID to
    that sport's tricode. Returns (merged_odds_map, fresh_count).

    A sport may add markets with `extra_odd_ids` and consume the already-paid-for
    event response with `response_consumer`. Consumer failures are logged and isolated
    from the primary matchup-odds persistence path.

    Callers that need collection observability may pass `collection_stats`; it is
    populated with the selected provider, eligible pregame candidates, changed
    inserts, deduplications, candidate matchup keys, and persistence health.

    SharpAPI is not used for a successful empty SGO response. Empty slates are
    valid, and probing three fallback markets would waste requests on dark days.

    Prefers PREFERRED_BOOK lines, falling back to consensus bookOdds. Spreads and totals
    often go null early in the day or temporarily off-board, so for those we fall back to
    the persisted value rather than wiping the prior line.
    """
    odds_map = load_persisted_odds(table_name=table_name)
    if collection_stats is not None:
        collection_stats.clear()
        collection_stats.update({
            "provider": None,
            "pregame_snapshot_candidates": 0,
            "pregame_snapshot_matchups": [],
            "snapshots_inserted": 0,
            "snapshots_deduplicated": 0,
            "snapshot_persistence_ok": False,
        })

    current = (request_time or datetime.now(ET)).astimezone(ET)
    target = target_date or current.date()
    try:
        starts_after_utc, starts_before_utc = sgo_event_window(target, current)
    except ValueError:
        logger.warning(
            "Skipping odds provider request for past ET date %s (%s)",
            target,
            league_id,
        )
        return odds_map, 0

    async def run_optional_market_fallback() -> None:
        if optional_market_fallback_consumer:
            try:
                result = optional_market_fallback_consumer()
                if result is not None:
                    await result
            except Exception:
                logger.exception("%s optional-market fallback consumer failed", league_id)

    async def sharp_fallback(reason: str) -> tuple[dict, int]:
        await run_optional_market_fallback()
        if not (os.getenv("SHARP_API_KEY") or os.getenv("SHARPAPI_KEY")):
            logger.warning(
                "SportsGameOdds %s unavailable (%s); SharpAPI is not configured",
                league_id,
                reason,
            )
            return odds_map, 0
        try:
            from .sharp_game_odds import fetch_sharp_game_odds

            logger.warning(
                "SportsGameOdds %s unavailable (%s); using SharpAPI DK/FD fallback",
                league_id,
                reason,
            )
            return await fetch_sharp_game_odds(
                sport=sport,
                league=league_id.lower(),
                table_name=table_name,
                tricode_for=fallback_tricode_for or tricode_for,
                target_date=target,
                persisted_odds=odds_map,
                collection_stats=collection_stats,
                market_rows=sharp_market_cache,
            )
        except Exception as exc:
            logger.error("SharpAPI %s fallback failed: %s", league_id, exc)
            return odds_map, 0

    api_key = os.getenv("SPORTS_GAME_ODDS_KEY")
    if not api_key:
        return await sharp_fallback("SPORTS_GAME_ODDS_KEY is not configured")

    try:
        # Scope to a single ET day's window so SGO only returns that day's slate.
        # Without these filters, SGO returns up to `limit` upcoming events spanning
        # multiple days, billing entities for games we don't need.
        optional_odd_ids = list(extra_odd_ids)
        requested_odd_ids = list(dict.fromkeys([*SGO_ODD_IDS, *optional_odd_ids]))
        params = {
            "leagueID": league_id,
            "oddsAvailable": "true",
            "startsAfter": starts_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "startsBefore": starts_before_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "oddID": ",".join(requested_odd_ids),
            "limit": 20,
        }
        if include_alt_lines:
            params["includeAltLines"] = "true"
        if include_open_close_odds:
            params["includeOpenCloseOdds"] = "true"
        headers = {"x-api-key": api_key}

        client = get_http_client()
        consumer_response_available = True
        try:
            r = await client.get(SGO_EVENTS_URL, params=params, headers=headers)
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Optional player-prop filters must never take down the primary game-line
            # refresh. Retry only request-shape errors; auth and rate-limit failures
            # cannot be repaired by removing markets.
            if optional_odd_ids and e.response.status_code in {400, 422}:
                logger.warning(
                    "SportsGameOdds %s rejected optional markets (%s); "
                    "retrying game odds only",
                    league_id,
                    e.response.status_code,
                )
                fallback_params = {
                    key: value
                    for key, value in params.items()
                    if key not in {"includeAltLines", "includeOpenCloseOdds"}
                }
                fallback_params["oddID"] = ",".join(SGO_ODD_IDS)
                r = await client.get(
                    SGO_EVENTS_URL, params=fallback_params, headers=headers
                )
                r.raise_for_status()
                consumer_response_available = False
                await run_optional_market_fallback()
            else:
                raise

        payload = r.json()
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise ValueError("SportsGameOdds returned an error response")
        games = payload.get("data", [])
        if not isinstance(games, list):
            raise ValueError("SportsGameOdds returned an invalid data envelope")
        captured_at = datetime.now(UTC)
        usage_headers = {
            key: value
            for key, value in r.headers.items()
            if any(token in key.lower() for token in ("rate", "limit", "usage", "remaining"))
        }
        logger.info(
            "SportsGameOdds: %d %s events for %s (usage headers: %s)",
            len(games),
            league_id,
            target.isoformat(),
            usage_headers or "unavailable",
        )

        if response_consumer and consumer_response_available:
            try:
                consumer_result = response_consumer(games, captured_at)
                if consumer_result is not None:
                    await consumer_result
            except Exception:
                logger.exception(f"SportsGameOdds {league_id} response consumer failed")

        fresh_odds = {}
        odds_by_date = {}
        snapshots = []

        for game in games:
            teams = game.get("teams", {})
            home_id = teams.get("home", {}).get("teamID", "")
            away_id = teams.get("away", {}).get("teamID", "")
            home_tri = tricode_for(home_id)
            away_tri = tricode_for(away_id)

            if not home_tri or not away_tri:
                logger.warning(
                    f"Skipping SGO {league_id} event {game.get('eventID', '?')} — "
                    f"home_team_id={home_id!r}, away_team_id={away_id!r}"
                )
                continue

            matchup_key = f"{away_tri}@{home_tri}"

            starts_at = game.get("status", {}).get("startsAt", "")
            try:
                starts_at_dt = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
                game_date = starts_at_dt.astimezone(ET).date()
            except Exception:
                starts_at_dt = None
                game_date = datetime.now(ET).date()

            odds = game.get("odds", {})

            away_spread      = to_num(sgo_book_val(odds, "points-away-game-sp-away", "spread"))
            home_spread      = to_num(sgo_book_val(odds, "points-home-game-sp-home", "spread"))
            away_spread_odds = to_num(sgo_book_val(odds, "points-away-game-sp-away", "odds"))
            home_spread_odds = to_num(sgo_book_val(odds, "points-home-game-sp-home", "odds"))
            away_ml          = to_num(sgo_book_val(odds, "points-away-game-ml-away", "odds"))
            home_ml          = to_num(sgo_book_val(odds, "points-home-game-ml-home", "odds"))
            over_under       = to_num(sgo_book_val(odds, "points-all-game-ou-over",  "overUnder"))
            over_odds        = to_num(sgo_book_val(odds, "points-all-game-ou-over",  "odds"))
            under_odds       = to_num(sgo_book_val(odds, "points-all-game-ou-under", "odds"))

            # fairOdds has the vig already removed by SGO; fall back to bookOdds.
            away_fair_ml = to_num(odds.get("points-away-game-ml-away", {}).get("fairOdds"))
            home_fair_ml = to_num(odds.get("points-home-game-ml-home", {}).get("fairOdds"))

            # Preserve non-null spread/O-U from persisted when the fresh API has null,
            # so an early-day or off-board gap doesn't wipe the prior line.
            persisted = odds_map.get((game_date.isoformat(), matchup_key), {})
            eff_away_spread      = away_spread      if away_spread      is not None else persisted.get("awaySpread")
            eff_home_spread      = home_spread      if home_spread      is not None else persisted.get("homeSpread")
            eff_away_spread_odds = away_spread_odds if away_spread_odds is not None else persisted.get("awaySpreadOdds")
            eff_home_spread_odds = home_spread_odds if home_spread_odds is not None else persisted.get("homeSpreadOdds")
            eff_over_under       = over_under       if over_under       is not None else persisted.get("overUnder")
            eff_over_odds        = over_odds        if over_odds        is not None else persisted.get("overOdds")
            eff_under_odds       = under_odds       if under_odds       is not None else persisted.get("underOdds")

            away_prob, home_prob = calculate_win_probability_from_odds(
                away_fair_ml or away_ml, home_fair_ml or home_ml, eff_away_spread, eff_home_spread
            )

            odds_data = {
                "awaySpread": eff_away_spread, "homeSpread": eff_home_spread,
                "awaySpreadOdds": eff_away_spread_odds, "homeSpreadOdds": eff_home_spread_odds,
                "overUnder": eff_over_under, "overOdds": eff_over_odds, "underOdds": eff_under_odds,
                "awayMoneyline": away_ml, "homeMoneyline": home_ml,
                "awayWinProbability": away_prob, "homeWinProbability": home_prob,
            }
            fresh_odds[(game_date.isoformat(), matchup_key)] = odds_data
            odds_by_date.setdefault(game_date, {})[matchup_key] = odds_data

            status = game.get("status", {})
            event_started = bool(
                status.get("started")
                or status.get("live")
                or (starts_at_dt is not None and starts_at_dt <= captured_at)
            )
            if not event_started and any(value is not None for value in odds_data.values()):
                preferred_count = sum(
                    bool(odds.get(odd_id, {}).get("byBookmaker", {}).get(PREFERRED_BOOK, {}).get("available"))
                    for odd_id in SGO_ODD_IDS
                )
                populated_count = sum(bool(odds.get(odd_id)) for odd_id in SGO_ODD_IDS)
                if preferred_count and preferred_count == populated_count:
                    bookmaker = PREFERRED_BOOK
                elif preferred_count:
                    bookmaker = f"mixed:{PREFERRED_BOOK}+consensus"
                else:
                    bookmaker = "consensus"
                snapshots.append({
                    "sport": sport,
                    "provider": "sportsgameodds",
                    "bookmaker": bookmaker,
                    "provider_event_id": str(game.get("eventID") or "") or None,
                    "game_date": game_date.isoformat(),
                    "matchup_key": matchup_key,
                    "starts_at": starts_at or None,
                    "captured_at": captured_at.isoformat(),
                    "odds_data": odds_data,
                })

        odds_map.update(fresh_odds)
        for gd, gd_odds in odds_by_date.items():
            save_persisted_odds(gd_odds, game_date=gd, table_name=table_name)
        snapshot_stats: dict = {}
        save_odds_snapshots(snapshots, stats=snapshot_stats)
        if collection_stats is not None:
            collection_stats.update({
                "provider": "sportsgameodds",
                "pregame_snapshot_candidates": len(snapshots),
                "pregame_snapshot_matchups": [
                    snapshot["matchup_key"]
                    for snapshot in snapshots
                    if snapshot.get("matchup_key")
                ],
                "snapshots_inserted": snapshot_stats.get("inserted", 0),
                "snapshots_deduplicated": snapshot_stats.get("deduplicated", 0),
                "snapshot_persistence_ok": snapshot_stats.get("persistence_ok", False),
            })

        logger.info(
            f"{league_id} odds map: {len(fresh_odds)} fresh + "
            f"{len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total"
        )
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"SportsGameOdds {league_id} HTTP error: {e.response.status_code} - {e.response.text}")
        return await sharp_fallback(f"HTTP {e.response.status_code}")
    except Exception as e:
        logger.error(f"SportsGameOdds {league_id} fetch error: {e}")
        return await sharp_fallback(type(e).__name__)


ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Book preference for The Odds API, first available per event wins. BetOnline and
# Pinnacle aren't carried on the free tier for every league (notably WNBA), so the
# chain leads with the sharp, always-present US books and degrades from there.
ODDS_API_BOOKS = ["pinnacle", "draftkings", "fanduel", "betmgm", "betrivers", "bovada", "mybookieag"]


def _odds_api_pick_book(event: dict, books: list[str]) -> dict | None:
    """First preferred book present on an event; falls back to whatever's listed first."""
    available = {b["key"]: b for b in event.get("bookmakers", [])}
    for key in books:
        if key in available:
            return available[key]
    bookmakers = event.get("bookmakers", [])
    return bookmakers[0] if bookmakers else None


async def fetch_odds_api_odds(
    *,
    sport: str,
    sport_key: str,
    table_name: str,
    tricode_for: Callable[[str], str],
    target_date: date | None = None,
    books: list[str] | None = None,
) -> tuple[dict, int]:
    """Fetch a league's odds from The Odds API, merge with persisted rows, persist fresh.

    Mirrors fetch_sgo_odds's contract and output shape, but reads The Odds API's
    per-bookmaker market/outcome structure (h2h/spreads/totals) instead of SGO's flat
    oddID keys. Used for leagues not on our SGO tier (e.g. WNBA). `tricode_for` maps a
    full team name ("Las Vegas Aces") to a tricode.

    The Odds API returns raw book odds only (no de-vigged fairOdds), so win probability
    is derived from the book moneyline pair, which calculate_win_probability_from_odds
    de-vigs. Spreads/totals fall back to the persisted line when the fresh API has null.
    """
    odds_map = load_persisted_odds(table_name=table_name)

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        logger.warning(f"ODDS_API_KEY not set, returning persisted {sport_key} odds only")
        return odds_map, 0

    books = books or ODDS_API_BOOKS
    try:
        # Scope The Odds API to the target ET calendar day; commenceTime filters use UTC.
        target = target_date or datetime.now(ET).date()
        starts_after_utc = datetime.combine(target, datetime.min.time(), ET).astimezone(UTC)
        starts_before_utc = datetime.combine(target + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)

        params = {
            "apiKey": api_key,
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
            "dateFormat": "iso",
            "commenceTimeFrom": starts_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "commenceTimeTo": starts_before_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        client = get_http_client()
        r = await client.get(f"{ODDS_API_BASE}/sports/{sport_key}/odds", params=params)
        r.raise_for_status()
        events = r.json()
        remaining = r.headers.get("x-requests-remaining", "?")
        logger.info(f"The Odds API: {len(events)} {sport_key} events for {target.isoformat()} (credits left: {remaining})")

        captured_at = datetime.now(UTC)
        fresh_odds = {}
        odds_by_date = {}
        snapshots = []

        for event in events:
            home_name = event.get("home_team", "")
            away_name = event.get("away_team", "")
            home_tri = tricode_for(home_name)
            away_tri = tricode_for(away_name)

            if not home_tri or not away_tri:
                logger.warning(
                    f"Skipping Odds API {sport_key} event {event.get('id', '?')} — "
                    f"home={home_name!r}, away={away_name!r}"
                )
                continue

            matchup_key = f"{away_tri}@{home_tri}"
            try:
                starts_at_dt = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
                game_date = starts_at_dt.astimezone(ET).date()
            except Exception:
                starts_at_dt = None
                game_date = datetime.now(ET).date()

            away_ml = home_ml = None
            away_spread = home_spread = away_spread_odds = home_spread_odds = None
            over_under = over_odds = under_odds = None

            book = _odds_api_pick_book(event, books)
            if book:
                for market in book.get("markets", []):
                    key = market.get("key")
                    if key == "h2h":
                        for oc in market.get("outcomes", []):
                            if oc.get("name") == away_name:
                                away_ml = to_num(oc.get("price"))
                            elif oc.get("name") == home_name:
                                home_ml = to_num(oc.get("price"))
                    elif key == "spreads":
                        for oc in market.get("outcomes", []):
                            if oc.get("name") == away_name:
                                away_spread = to_num(oc.get("point"))
                                away_spread_odds = to_num(oc.get("price"))
                            elif oc.get("name") == home_name:
                                home_spread = to_num(oc.get("point"))
                                home_spread_odds = to_num(oc.get("price"))
                    elif key == "totals":
                        for oc in market.get("outcomes", []):
                            if oc.get("name") == "Over":
                                over_under = to_num(oc.get("point"))
                                over_odds = to_num(oc.get("price"))
                            elif oc.get("name") == "Under":
                                under_odds = to_num(oc.get("price"))

            persisted = odds_map.get((game_date.isoformat(), matchup_key), {})
            eff_away_spread      = away_spread      if away_spread      is not None else persisted.get("awaySpread")
            eff_home_spread      = home_spread      if home_spread      is not None else persisted.get("homeSpread")
            eff_away_spread_odds = away_spread_odds if away_spread_odds is not None else persisted.get("awaySpreadOdds")
            eff_home_spread_odds = home_spread_odds if home_spread_odds is not None else persisted.get("homeSpreadOdds")
            eff_over_under       = over_under       if over_under       is not None else persisted.get("overUnder")
            eff_over_odds        = over_odds        if over_odds        is not None else persisted.get("overOdds")
            eff_under_odds       = under_odds       if under_odds       is not None else persisted.get("underOdds")

            away_prob, home_prob = calculate_win_probability_from_odds(
                away_ml, home_ml, eff_away_spread, eff_home_spread
            )

            odds_data = {
                "awaySpread": eff_away_spread, "homeSpread": eff_home_spread,
                "awaySpreadOdds": eff_away_spread_odds, "homeSpreadOdds": eff_home_spread_odds,
                "overUnder": eff_over_under, "overOdds": eff_over_odds, "underOdds": eff_under_odds,
                "awayMoneyline": away_ml, "homeMoneyline": home_ml,
                "awayWinProbability": away_prob, "homeWinProbability": home_prob,
            }
            fresh_odds[(game_date.isoformat(), matchup_key)] = odds_data
            odds_by_date.setdefault(game_date, {})[matchup_key] = odds_data

            event_started = starts_at_dt is not None and starts_at_dt <= captured_at
            if not event_started and any(value is not None for value in odds_data.values()):
                snapshots.append({
                    "sport": sport,
                    "provider": "the_odds_api",
                    "bookmaker": (book or {}).get("key", "unknown"),
                    "provider_event_id": str(event.get("id") or "") or None,
                    "game_date": game_date.isoformat(),
                    "matchup_key": matchup_key,
                    "starts_at": event.get("commence_time"),
                    "captured_at": captured_at.isoformat(),
                    "odds_data": odds_data,
                })

        odds_map.update(fresh_odds)
        for gd, gd_odds in odds_by_date.items():
            save_persisted_odds(gd_odds, game_date=gd, table_name=table_name)
        save_odds_snapshots(snapshots)

        logger.info(
            f"{sport_key} odds map: {len(fresh_odds)} fresh + "
            f"{len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total"
        )
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"The Odds API {sport_key} HTTP error: {e.response.status_code} - {e.response.text}")
        return odds_map, 0
    except Exception as e:
        logger.error(f"The Odds API {sport_key} fetch error: {e}")
        return odds_map, 0
