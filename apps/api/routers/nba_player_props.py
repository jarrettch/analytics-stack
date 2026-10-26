"""Schedule-triggered NBA prop archives: intraday sweeps plus the pregame close.

Modeled on nfl_player_props: a sweep fetches every market before writing, and
the write is one transaction. NBA differences, from specs/nba_player_props.md:
every market is line-shaped, combo rows carry a consistency check against the
same sweep's single-stat lines, and either book can be missing near tip.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from . import espn_feeds, nba, utils
from .auth import require_cron_auth
from .nfl_player_props import ScheduledGame, ScheduleError, _numeric, _text, _timestamp
from .sharp_odds import SharpSweep, fetch_sharp_odds

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nba", tags=["NBA"])
SINGLE_MARKETS = (
    "player_points", "player_rebounds", "player_assists", "player_made_threes",
)
COMBO_COMPONENTS = {
    "player_points_+_rebounds_+_assists": ("player_points", "player_rebounds", "player_assists"),
    "player_points_+_rebounds": ("player_points", "player_rebounds"),
    "player_points_+_assists": ("player_points", "player_assists"),
    "player_rebounds_+_assists": ("player_rebounds", "player_assists"),
}
MARKETS = SINGLE_MARKETS + tuple(COMBO_COMPONENTS)
BOOKS = ("draftkings", "fanduel")
# Combo lines landed within ±2 of their components' sum on the first slate; a
# mislabeled R+A row holding the PRA line misses by the points line.
COMBO_TOLERANCE = 3.0
MAX_COMBO_EXAMPLES = 5
SWEEP_GUARD = timedelta(minutes=10)
ELIGIBILITY_WINDOW = timedelta(minutes=90)
SWEEP_TIMEOUT_SECONDS = 480
# Only used to choose between same-matchup games on consecutive days.
START_MATCH_BOUND = timedelta(hours=6)
EVENT_ID_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DEAD_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED"}
_collection_lock = asyncio.Lock()

Phase = Literal["close", "intraday"]


async def _scoreboard_for_date_with_status(target_date: date) -> tuple[dict, bool]:
    try:
        response = await utils.get_http_client().get(
            espn_feeds.SCOREBOARD_URL.format(sport="nba"),
            params={"dates": target_date.strftime("%Y%m%d"), "limit": espn_feeds.SCOREBOARD_LIMIT},
        )
        response.raise_for_status()
        return response.json(), True
    except Exception as exc:
        logger.warning("NBA scoreboard fetch failed for %s: %s", target_date, exc)
        return {}, False


class NBASchedule:
    """Cache ESPN results and date failures for one sweep, never across sweeps."""

    def __init__(self):
        self.days: dict[date, list[ScheduledGame]] = {}
        self.failed_days: dict[date, str] = {}
        # (game_date, matchup_key) of postponed, cancelled or suspended games.
        self.called_off: set[tuple[str, str]] = set()

    async def day(self, target: date) -> list[ScheduledGame]:
        if target in self.days:
            return self.days[target]
        if target in self.failed_days:
            raise ScheduleError(self.failed_days[target])
        payload, ok = await _scoreboard_for_date_with_status(target)
        if not ok or not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            self.failed_days[target] = f"NBA schedule unavailable for {target}"
            raise ScheduleError(self.failed_days[target])
        games = []
        try:
            for event in payload["events"]:
                competition = event["competitions"][0]
                # ESPN abbreviations are not NBA tricodes (GS, NO, NY, SA, UTAH, WSH).
                competitors = {item["homeAway"]: nba._canonical_tricode(item["team"]["abbreviation"])
                               for item in competition["competitors"]}
                away, home = competitors["away"], competitors["home"]
                if away not in nba.NBA_TRICODES or home not in nba.NBA_TRICODES or away == home:
                    raise ValueError("Invalid NBA schedule teams")
                status = (competition.get("status") or event.get("status") or {}).get("type") or {}
                if status.get("state") not in {"pre", "in", "post"}:
                    raise ValueError("Missing NBA schedule status")
                game = ScheduledGame(
                    f"{away}@{home}", _timestamp(event["date"]),
                    status.get("state") == "pre" and status.get("name") not in _DEAD_STATUSES,
                )
                if status.get("name") in _DEAD_STATUSES:
                    self.called_off.add((game.game_date, game.matchup_key))
                games.append(game)
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            self.failed_days[target] = f"Malformed NBA schedule for {target}"
            raise ScheduleError(self.failed_days[target]) from exc
        self.days[target] = games
        return games

    async def selected(self, now: datetime, phase: Phase) -> list[ScheduledGame]:
        first = now.astimezone(utils.ET).date()
        if phase == "intraday":
            # Today's ET slate, anything that still clears the kickoff guard.
            games = await self.day(first)
            chosen = [g for g in games if g.eligible and now + SWEEP_GUARD <= g.starts_at]
        else:
            last = (now + ELIGIBILITY_WINDOW).astimezone(utils.ET).date()
            games = await self.day(first)
            if last != first:
                games = games + await self.day(last)
            chosen = [g for g in games if g.eligible
                      and now + SWEEP_GUARD <= g.starts_at <= now + ELIGIBILITY_WINDOW]
        return list({(g.game_date, g.matchup_key): g for g in chosen}.values())

    async def resolve(self, matchup_key: str, provider_start: datetime,
                      provider_event_id: str | None = None) -> ScheduledGame:
        # The book's time is only a search hint; both canonical fields come from ESPN.
        hint = provider_start.astimezone(utils.ET).date()
        matches = {}
        for offset in (-1, 0, 1):
            for game in await self.day(hint + timedelta(days=offset)):
                if game.matchup_key == matchup_key:
                    matches[(game.game_date, game.matchup_key)] = game
        if len(matches) == 1:
            return next(iter(matches.values()))
        # Same teams on consecutive days (a home back-to-back): the provider's
        # start picks the game, but only within a bound, so a book time off by
        # hours is rejected rather than attached to the wrong night.
        near = [game for game in matches.values()
                if abs(game.starts_at - provider_start) <= START_MATCH_BOUND]
        if len(near) != 1:
            raise ValueError(f"Expected one ESPN game for {matchup_key} near {hint}, found {len(matches)}"
                             f" ({len(near)} within {START_MATCH_BOUND} of the provider start)")
        # A book start off by a whole day would land within the bound of the
        # other night. SharpAPI event ids carry the ET game date
        # ("nba_grizzlies_kings_2026-10-25_b3" for a 9 pm ET tip), so when one
        # is present it must agree. The format is undocumented; no date, no check.
        event_date = EVENT_ID_DATE.search(provider_event_id or "")
        if event_date and event_date.group(0) != near[0].game_date:
            raise ValueError(f"Provider event {provider_event_id} disagrees with ESPN date "
                             f"{near[0].game_date} for back-to-back {matchup_key}")
        return near[0]


def _prop_matchup(row: dict, unmapped_teams: set[str]) -> str:
    """Use mapped teams, never the provider date, to scope row failures."""
    if not isinstance(row, dict):
        raise TypeError("Invalid odds row")
    teams = []
    for field in ("away_team", "home_team"):
        name = _text(row, field)
        tri = nba.get_tricode_from_team_name(name)
        if not tri:
            unmapped_teams.add(name)
        teams.append(tri)
    if not all(teams) or teams[0] == teams[1]:
        raise ValueError("Unmapped or invalid NBA matchup")
    return "@".join(teams)


async def normalize_prop(
    row: dict, market: str, schedule: NBASchedule, collection_id: str,
    captured_at: datetime, unmapped_teams: set[str],
    *, matchup_key: str | None = None,
) -> dict:
    if not isinstance(row, dict):
        raise TypeError("Invalid odds row")
    for field in ("is_live", "is_main_line", "is_active", "is_stale_pregame_price"):
        if type(row.get(field)) is not bool:
            raise ValueError(f"Missing or invalid {field}")
    alternate = row.get("is_alternate_line", False)
    if type(alternate) is not bool or alternate or row["is_live"] or not row["is_main_line"]:
        raise ValueError("Response contains live or alternate prices")
    if row.get("league") != "nba" or row.get("market_type") != market:
        raise ValueError("Response does not match requested NBA market")
    if row.get("sportsbook") not in BOOKS:
        raise ValueError("Unexpected sportsbook")

    if matchup_key is None:
        matchup_key = _prop_matchup(row, unmapped_teams)

    line = _numeric(row.get("line"), "line")
    side = row.get("selection_type")
    if side not in {"over", "under"}:
        raise ValueError("Invalid market shape")
    american = _numeric(row.get("odds_american"), "odds_american")
    if american is None or not american.is_integer() or abs(american) < 100 or abs(american) > 2147483647:
        raise ValueError("Invalid American odds")
    decimal = _numeric(row.get("odds_decimal"), "odds_decimal", optional=True)
    probability = _numeric(row.get("odds_probability"), "odds_probability", optional=True)
    if decimal is not None and decimal <= 1:
        raise ValueError("Invalid decimal odds")
    if probability is not None and not 0 <= probability <= 1:
        raise ValueError("Invalid implied probability")
    provider_time = _timestamp(row.get("timestamp"))
    game = await schedule.resolve(matchup_key, _timestamp(row.get("event_start_time")),
                                  row.get("event_id"))
    if provider_time >= game.starts_at:
        raise ValueError("Provider observation is at or after canonical tip-off")

    return {
        "collection_id": collection_id, "captured_at": captured_at.isoformat(),
        "game_date": game.game_date, "matchup_key": game.matchup_key,
        "starts_at": game.starts_at.isoformat(), "provider": "sharpapi",
        "provider_event_id": _text(row, "event_id"),
        "provider_event_uuid": row.get("event_uuid"), "market_id": row.get("market_id"),
        "selection_id": _text(row, "selection_id"), "bookmaker_id": row["sportsbook"],
        "market_type": market, "stat_category": row.get("stat_category"),
        # Set by apply_combo_checks once the whole sweep is normalized.
        "combo_check": None,
        "selection_name": _text(row, "player_name"), "selection_raw": _text(row, "selection"),
        "provider_player_name": row.get("player_name"),
        "side": side, "line": line, "american_odds": int(american),
        "decimal_odds": decimal, "implied_probability": probability,
        "is_main_line": True, "is_alternate_line": alternate,
        "is_active": row["is_active"], "is_stale_pregame_price": row["is_stale_pregame_price"],
        "provider_timestamp": provider_time.isoformat(),
    }


def _player_key(row: dict) -> tuple:
    return row["game_date"], row["matchup_key"], row["bookmaker_id"], row["selection_name"]


def apply_combo_checks(rows: list[dict]) -> dict:
    """Mark each combo row passed, failed or unchecked against this sweep's components.

    A component counts only when its live, non-stale rows for the player share
    one line, over and under included; anything else is ambiguous and leaves
    the combo unchecked. Returns per-book counts and a few failure examples.
    """
    component_lines: dict[tuple, set[float]] = defaultdict(set)
    for row in rows:
        if row["market_type"] in SINGLE_MARKETS and row["is_active"] and not row["is_stale_pregame_price"]:
            component_lines[(*_player_key(row), row["market_type"])].add(row["line"])

    counts: dict[str, Counter] = defaultdict(Counter)
    failures = []
    for row in rows:
        components = COMBO_COMPONENTS.get(row["market_type"])
        if components is None:
            continue
        lines = [component_lines.get((*_player_key(row), market), set()) for market in components]
        if all(len(found) == 1 for found in lines):
            component_sum = sum(next(iter(found)) for found in lines)
            row["combo_check"] = ("passed" if abs(row["line"] - component_sum) <= COMBO_TOLERANCE
                                  else "failed")
        else:
            component_sum = None
            row["combo_check"] = "unchecked"
        counts[row["bookmaker_id"]][row["combo_check"]] += 1
        if row["combo_check"] == "failed":
            example = {
                "game_date": row["game_date"], "matchup_key": row["matchup_key"],
                "bookmaker_id": row["bookmaker_id"], "selection_name": row["selection_name"],
                "market_type": row["market_type"], "side": row["side"],
                "line": row["line"], "component_sum": component_sum,
            }
            if len(failures) < 10:
                logger.warning("NBA combo check failed: %s", example)
            failures.append(example)
    return {
        "combo_checks": {book: {outcome: count[outcome] for outcome in ("passed", "failed", "unchecked")}
                         for book, count in sorted(counts.items())},
        "combo_failures": failures[:MAX_COMBO_EXAMPLES],
    }


def persist_props(rows: list[dict]) -> int:
    """One PostgreSQL transaction; the scalar count is not subject to row limits."""
    if not rows:
        return 0
    if utils.supabase is None:
        raise ValueError("Supabase is not configured")
    response = utils.supabase.rpc("append_nba_player_prop_odds", {"observations": rows}).execute()
    if type(response.data) is not int or not 0 <= response.data <= len(rows):
        raise ValueError("Invalid NBA props insert count")
    return response.data


async def collect_player_props(collection_id: str, now: datetime, phase: Phase = "close") -> dict:
    sweep = SharpSweep()
    result = {
        "success": False, "collection_id": collection_id, "phase": phase, "schedule_ok": False,
        "games_selected": 0, "games_persisted": 0, "markets_requested": 0,
        "markets_completed": 0, "pages_fetched": 0, "rows_accepted": 0,
        "rows_rejected": 0, "rows_skipped": 0,
        "rows_deduplicated": 0, "rows_inserted": 0, "unmapped_teams": [],
        "book_rows": {}, "combo_checks": {}, "combo_failures": [],
        "sweep_complete": False, "persistence_ok": False, "rate_limit": sweep.rate_limit,
    }
    unmapped: set[str] = set()
    try:
        schedule = NBASchedule()
        selected = await schedule.selected(now, phase)
        selected_matchups = {game.matchup_key for game in selected}
        result.update(schedule_ok=True, games_selected=len(selected))
        if not selected:
            result.update(success=True, sweep_complete=True, persistence_ok=True)
            return result
        if min(game.starts_at for game in selected) < datetime.now(utils.UTC) + SWEEP_GUARD:
            raise ValueError("Workflow arrived too late for the ten-minute tip-off guard")

        # Finish fetching all markets before normalizing or writing anything.
        observations = []
        for market in MARKETS:
            result["markets_requested"] += 1
            rows = await fetch_sharp_odds("nba", market, sweep=sweep)
            result["markets_completed"] += 1
            captured_at = datetime.now(utils.UTC)
            observations.extend((market, row, captured_at) for row in rows)
        normalized = []
        for market, row, captured_at in observations:
            matchup_key = None
            try:
                matchup_key = _prop_matchup(row, unmapped)
                normalized.append(await normalize_prop(
                    row, market, schedule, collection_id, captured_at, unmapped,
                    matchup_key=matchup_key,
                ))
            except (TypeError, ValueError, KeyError) as exc:
                fatal = matchup_key is None or matchup_key in selected_matchups
                if fatal and isinstance(exc, ScheduleError):
                    result["schedule_ok"] = False
                counter = "rows_rejected" if fatal else "rows_skipped"
                result[counter] += 1
                if result[counter] <= 10:
                    logger.warning("NBA prop %s market=%s matchup=%s selection_id=%s: %s",
                                   counter, market, matchup_key,
                                   row.get("selection_id") if isinstance(row, dict) else None, exc)
        result["rows_accepted"] = len(normalized)
        if result["rows_rejected"]:
            raise ValueError("NBA prop validation failed; no rows inserted")
        result.update(apply_combo_checks(normalized))
        # A missing book is not a failure; the counts make dropouts measurable.
        book_rows: dict[str, Counter] = defaultdict(Counter)
        for row in normalized:
            book_rows[f"{row['game_date']} {row['matchup_key']}"][row["bookmaker_id"]] += 1
        result["book_rows"] = {game: dict(counts) for game, counts in sorted(book_rows.items())}
        result["sweep_complete"] = True
        result["rows_inserted"] = await asyncio.to_thread(persist_props, normalized)
        result["rows_deduplicated"] = len(normalized) - result["rows_inserted"]
        result["games_persisted"] = len({(r["game_date"], r["matchup_key"]) for r in normalized})
        result.update(success=True, persistence_ok=True)
    except Exception as exc:
        if isinstance(exc, ScheduleError):
            result["schedule_ok"] = False
        logger.exception("NBA props sweep failed collection_id=%s", collection_id)
        result["error"] = str(exc)
    finally:
        result["pages_fetched"] = sweep.pages_fetched
        result["requests_made"] = sweep.requests_made
        result["unmapped_teams"] = sorted(unmapped)
    return result


def close_coverage_cells(target: date) -> list[dict]:
    """Usable close counts per matchup, book and market with any row in the window."""
    if utils.supabase is None:
        raise ValueError("Supabase is not configured")
    response = utils.supabase.rpc("nba_player_prop_close_coverage", {"target": target.isoformat()}).execute()
    if not isinstance(response.data, list):
        raise ValueError("Invalid NBA close coverage response")
    return response.data


async def close_coverage(target: date) -> dict:
    """Per played game, which book x market cells have no usable close row.

    A standing metric, not an alarm: some gaps are expected (a book that
    drops out near tip, a market a book never posted). Gaps never fail it;
    only a missing schedule or query does.
    """
    result = {
        "success": False, "game_date": target.isoformat(), "schedule_ok": False,
        "games": 0, "cells_expected": 0, "cells_covered": 0,
        "matrix": {}, "game_gaps": [], "unexpected_matchups": [],
    }
    try:
        schedule = NBASchedule()
        games = sorted(
            (g for g in await schedule.day(target)
             if g.game_date == result["game_date"]
             and (g.game_date, g.matchup_key) not in schedule.called_off),
            key=lambda g: (g.starts_at, g.matchup_key),
        )
        result.update(schedule_ok=True, games=len(games), cells_expected=len(games) * len(BOOKS) * len(MARKETS))
        cells = {(c["matchup_key"], c["bookmaker_id"], c["market_type"]): c
                 for c in await asyncio.to_thread(close_coverage_cells, target)}
        # Per book x market: how many of the day's games had a usable close.
        matrix = {book: dict.fromkeys(MARKETS, 0) for book in BOOKS}
        for game in games:
            missing, unchecked_only, failed_rows = [], [], 0
            for book in BOOKS:
                for market in MARKETS:
                    cell = cells.get((game.matchup_key, book, market)) or {}
                    failed_rows += cell.get("failed_rows", 0)
                    if cell.get("usable_rows", 0) > 0:
                        matrix[book][market] += 1
                        result["cells_covered"] += 1
                    elif cell.get("unchecked_rows", 0) > 0:
                        unchecked_only.append(f"{book} {market}")
                    else:
                        missing.append(f"{book} {market}")
            if missing or unchecked_only or failed_rows:
                result["game_gaps"].append({
                    "matchup_key": game.matchup_key, "starts_at": game.starts_at.isoformat(),
                    "missing": missing, "unchecked_only": unchecked_only, "failed_rows": failed_rows,
                })
        result["matrix"] = matrix
        played = {g.matchup_key for g in games}
        result["unexpected_matchups"] = sorted({key[0] for key in cells} - played)
        result["success"] = True
    except Exception as exc:
        logger.exception("NBA props close coverage failed for %s", target)
        result["error"] = str(exc)
    return result


@router.post("/cron/player-props-coverage", dependencies=[Depends(require_cron_auth)])
async def cron_player_props_coverage(game_date: Annotated[date | None, Query(alias="date")] = None):
    """Close coverage for one finished ET date, yesterday by default."""
    today = datetime.now(utils.UTC).astimezone(utils.ET).date()
    target = game_date or today - timedelta(days=1)
    if target >= today:
        raise HTTPException(status_code=422, detail="date must be before today in ET")
    return await close_coverage(target)


@router.post("/cron/fetch-player-props", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_player_props(
    collection_key: Annotated[str, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")],
    window_at: datetime | None = None,
    phase: Phase = "close",
):
    # The workflow anchors eligibility at its gate's start; manual calls use
    # the API request time.
    if window_at is not None and window_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="window_at must include a timezone")
    if _collection_lock.locked():
        return {"success": False, "collection_id": collection_key, "phase": phase,
                "sweep_complete": False, "persistence_ok": False, "error": "NBA props sweep already running"}
    async with _collection_lock:
        try:
            return await asyncio.wait_for(
                collect_player_props(collection_key, window_at or datetime.now(utils.UTC), phase),
                timeout=SWEEP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.error("NBA props sweep exceeded tip-off guard collection_id=%s", collection_key)
            return {"success": False, "collection_id": collection_key, "phase": phase,
                    "sweep_complete": False, "persistence_ok": False, "error": "NBA props sweep timed out"}
