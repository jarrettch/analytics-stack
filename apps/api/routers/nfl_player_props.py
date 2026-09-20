"""Schedule-triggered NFL prop archives, committed only after a complete sweep."""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from . import nfl, utils
from .auth import require_cron_auth
from .sharp_odds import SharpSweep, fetch_sharp_odds

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/nfl", tags=["NFL"])
MARKETS = (
    "player_receptions", "player_receiving_yards", "player_rushing_yards",
    "player_passing_yards", "anytime_touchdown_scorer",
)
SWEEP_GUARD = timedelta(minutes=10)
ELIGIBILITY_WINDOW = timedelta(minutes=90)
# Leave two minutes of the kickoff guard for schedule lookup and persistence.
SWEEP_TIMEOUT_SECONDS = 480
_collection_lock = asyncio.Lock()


def _timestamp(value) -> datetime:
    if not isinstance(value, str):
        raise TypeError("Missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return parsed


@dataclass(frozen=True)
class ScheduledGame:
    matchup_key: str
    starts_at: datetime
    eligible: bool

    @property
    def game_date(self) -> str:
        return self.starts_at.astimezone(utils.ET).date().isoformat()


class ScheduleError(ValueError):
    pass


class NFLSchedule:
    """Cache ESPN results and date failures for one sweep, never across sweeps."""

    def __init__(self):
        self.days: dict[date, list[ScheduledGame]] = {}
        self.failed_days: dict[date, str] = {}

    async def day(self, target: date) -> list[ScheduledGame]:
        if target in self.days:
            return self.days[target]
        if target in self.failed_days:
            # Store messages, not exception objects whose tracebacks would grow
            # each time hundreds of incidental rows encounter the same failure.
            raise ScheduleError(self.failed_days[target])
        payload, ok = await nfl._scoreboard_for_date_with_status(target)
        if not ok or not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            self.failed_days[target] = f"NFL schedule unavailable for {target}"
            raise ScheduleError(self.failed_days[target])
        games = []
        try:
            for event in payload["events"]:
                competition = event["competitions"][0]
                competitors = {item["homeAway"]: item["team"]["abbreviation"]
                               for item in competition["competitors"]}
                away, home = competitors["away"], competitors["home"]
                if away not in nfl.NFL_TEAM_NAMES or home not in nfl.NFL_TEAM_NAMES or away == home:
                    raise ValueError("Invalid NFL schedule teams")
                status = (competition.get("status") or event.get("status") or {}).get("type") or {}
                if status.get("state") not in {"pre", "in", "post"}:
                    raise ValueError("Missing NFL schedule status")
                starts_at = _timestamp(event["date"])
                games.append(ScheduledGame(
                    f"{away}@{home}", starts_at,
                    status.get("state") == "pre"
                    and status.get("name") not in {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"},
                ))
        except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
            self.failed_days[target] = f"Malformed NFL schedule for {target}"
            raise ScheduleError(self.failed_days[target]) from exc
        self.days[target] = games
        return games

    async def selected(self, now: datetime) -> list[ScheduledGame]:
        first = now.astimezone(utils.ET).date()
        last = (now + ELIGIBILITY_WINDOW).astimezone(utils.ET).date()
        games = await self.day(first)
        if last != first:
            games = games + await self.day(last)
        return list({(g.game_date, g.matchup_key): g for g in games if g.eligible
                     and now + SWEEP_GUARD <= g.starts_at <= now + ELIGIBILITY_WINDOW}.values())

    async def resolve(self, matchup_key: str, provider_start: datetime) -> ScheduledGame:
        # The book's time is only a search hint. Adjacent ET dates cover books
        # disagreeing across midnight; both canonical fields come from ESPN.
        hint = provider_start.astimezone(utils.ET).date()
        matches = {}
        for offset in (-1, 0, 1):
            for game in await self.day(hint + timedelta(days=offset)):
                if game.matchup_key == matchup_key:
                    matches[(game.game_date, game.matchup_key)] = game
        if len(matches) != 1:
            raise ValueError(f"Expected one ESPN game for {matchup_key} near {hint}, found {len(matches)}")
        return next(iter(matches.values()))


def _text(row: dict, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {field}")
    return value


def _numeric(value, field: str, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise TypeError(f"Invalid {field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"Non-finite {field}")
    return parsed


def _prop_matchup(row: dict, unmapped_teams: set[str]) -> str:
    """Use mapped teams, never the provider date, to scope row failures."""
    if not isinstance(row, dict):
        raise TypeError("Invalid odds row")
    teams = []
    for field in ("away_team", "home_team"):
        name = _text(row, field)
        tri = nfl.get_tricode_from_team_name(name)
        if not tri:
            unmapped_teams.add(name)
        teams.append(tri)
    if not all(teams) or teams[0] == teams[1]:
        raise ValueError("Unmapped or invalid NFL matchup")
    return "@".join(teams)


async def normalize_prop(
    row: dict, market: str, schedule: NFLSchedule, collection_id: str,
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
    if row.get("league") != "nfl" or row.get("market_type") != market:
        raise ValueError("Response does not match requested NFL market")
    if row.get("sportsbook") not in {"draftkings", "fanduel"}:
        raise ValueError("Unexpected sportsbook")

    if matchup_key is None:
        matchup_key = _prop_matchup(row, unmapped_teams)

    scorer = market == "anytime_touchdown_scorer"
    line = _numeric(row.get("line"), "line", optional=scorer)
    side = None if scorer else row.get("selection_type")
    if (scorer and line is not None) or (not scorer and side not in {"over", "under"}):
        raise ValueError("Invalid market shape")
    selection_name = _text(row, "selection" if scorer else "player_name")
    selection_id = _text(row, "selection_id")
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
    game = await schedule.resolve(matchup_key, _timestamp(row.get("event_start_time")))
    if provider_time >= game.starts_at:
        raise ValueError("Provider observation is at or after canonical kickoff")

    return {
        "collection_id": collection_id, "captured_at": captured_at.isoformat(),
        "game_date": game.game_date, "matchup_key": game.matchup_key,
        "starts_at": game.starts_at.isoformat(), "provider": "sharpapi",
        "provider_event_id": _text(row, "event_id"),
        "provider_event_uuid": row.get("event_uuid"), "market_id": row.get("market_id"),
        "selection_id": selection_id, "bookmaker_id": row["sportsbook"],
        "market_type": market, "stat_category": row.get("stat_category"),
        "market_shape": "scorer" if scorer else "line", "selection_name": selection_name,
        "selection_raw": _text(row, "selection"), "provider_player_name": row.get("player_name"),
        "side": side, "line": line, "american_odds": int(american),
        "decimal_odds": decimal, "implied_probability": probability,
        "is_main_line": True, "is_alternate_line": alternate,
        "is_active": row["is_active"], "is_stale_pregame_price": row["is_stale_pregame_price"],
        "provider_timestamp": provider_time.isoformat(),
    }


def persist_props(rows: list[dict]) -> int:
    """One PostgreSQL transaction; the scalar count is not subject to row limits."""
    if not rows:
        return 0
    if utils.supabase is None:
        raise ValueError("Supabase is not configured")
    response = utils.supabase.rpc("append_nfl_player_prop_odds", {"observations": rows}).execute()
    if type(response.data) is not int or not 0 <= response.data <= len(rows):
        raise ValueError("Invalid NFL props insert count")
    return response.data


async def collect_player_props(collection_id: str, now: datetime) -> dict:
    sweep = SharpSweep()
    result = {
        "success": False, "collection_id": collection_id, "schedule_ok": False,
        "games_selected": 0, "games_persisted": 0, "markets_requested": 0,
        "markets_completed": 0, "pages_fetched": 0, "rows_accepted": 0,
        "rows_rejected": 0, "rows_skipped": 0,
        "rows_deduplicated": 0, "rows_inserted": 0, "unmapped_teams": [],
        "sweep_complete": False, "persistence_ok": False, "rate_limit": sweep.rate_limit,
    }
    unmapped: set[str] = set()
    try:
        schedule = NFLSchedule()
        selected = await schedule.selected(now)
        selected_matchups = {game.matchup_key for game in selected}
        result.update(schedule_ok=True, games_selected=len(selected))
        if not selected:
            result.update(success=True, sweep_complete=True, persistence_ok=True)
            return result
        if min(game.starts_at for game in selected) < datetime.now(utils.UTC) + SWEEP_GUARD:
            raise ValueError("Workflow arrived too late for the ten-minute kickoff guard")

        # Finish fetching all markets before normalizing or writing anything.
        observations = []
        for market in MARKETS:
            result["markets_requested"] += 1
            rows = await fetch_sharp_odds("nfl", market, sweep=sweep)
            result["markets_completed"] += 1
            captured_at = datetime.now(utils.UTC)
            observations.extend((market, row, captured_at) for row in rows)
        normalized = []
        for market, row, captured_at in observations:
            matchup_key = None
            try:
                # Unknown identities remain fatal: a provider's date alone
                # cannot establish that a malformed row is outside the window.
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
                    logger.warning("NFL prop %s market=%s matchup=%s selection_id=%s: %s",
                                   counter, market, matchup_key,
                                   row.get("selection_id") if isinstance(row, dict) else None, exc)
        result["rows_accepted"] = len(normalized)
        if result["rows_rejected"]:
            raise ValueError("NFL prop validation failed; no rows inserted")
        result["sweep_complete"] = True
        result["rows_inserted"] = await asyncio.to_thread(persist_props, normalized)
        result["rows_deduplicated"] = len(normalized) - result["rows_inserted"]
        result["games_persisted"] = len({r["matchup_key"] for r in normalized})
        result.update(success=True, persistence_ok=True)
    except Exception as exc:
        if isinstance(exc, ScheduleError):
            result["schedule_ok"] = False
        logger.exception("NFL props sweep failed collection_id=%s", collection_id)
        result["error"] = str(exc)
    finally:
        result["pages_fetched"] = sweep.pages_fetched
        result["requests_made"] = sweep.requests_made
        result["unmapped_teams"] = sorted(unmapped)
    return result


@router.post("/cron/fetch-player-props", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_player_props(
    collection_key: Annotated[str, Query(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.-]+$")],
    window_at: datetime | None = None,
):
    # Workflow eligibility is anchored before the server warm-up. Manual calls
    # without window_at use their API request time.
    if window_at is not None and window_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="window_at must include a timezone")
    # A second request must not queue behind a minutes-long sweep and use an
    # eligibility window that has expired by the time it gets the lock.
    if _collection_lock.locked():
        return {"success": False, "collection_id": collection_key,
                "sweep_complete": False, "persistence_ok": False, "error": "NFL props sweep already running"}
    async with _collection_lock:
        try:
            return await asyncio.wait_for(
                collect_player_props(collection_key, window_at or datetime.now(utils.UTC)),
                timeout=SWEEP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.error("NFL props sweep exceeded kickoff guard collection_id=%s", collection_key)
            return {"success": False, "collection_id": collection_key,
                    "sweep_complete": False, "persistence_ok": False, "error": "NFL props sweep timed out"}
