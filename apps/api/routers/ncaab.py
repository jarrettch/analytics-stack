"""Men's college basketball (Division I) dashboard. Odds come from ESPN, not SportsGameOdds.

See specs/ncaab_dashboard.md. The scoreboard call that supplies the slate also
carries DraftKings odds, so NCAAB costs nothing against the SGO entity budget.
ESPN drops those odds at tipoff, which is why the board reads persisted lines
and the cron persists them pregame.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from . import nfl, utils
from .auth import CronOffset, require_cron_auth
from .espn_odds import extract_espn_odds, fetch_espn_odds, fetch_espn_scoreboard, has_market
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    ET,
    Cache,
    format_et_time,
    get_http_client,
    grade_spread_result,
    load_persisted_odds,
    slate_date_et,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ncaab", tags=["NCAAB"])

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard"
ESPN_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/mens-college-basketball/standings"
# groups=50 scopes to Division I; busy slates need more than 200 events.
SCOREBOARD_PARAMS = {"groups": 50, "limit": 400}
TABLE_NAME = "ncaab_odds"
UNRANKED = 99

cache = Cache({
    "standings": 3600,
    "assembled": CACHE_TTL_DEFAULT,
})


def _load_persisted_strict() -> dict:
    """load_persisted_odds, but missing configuration is a failure too.

    The shared loader returns {} without Supabase, which would pass for "no
    odds yet" and serve a fresh-only board that drops every game at tipoff.
    """
    if utils.supabase is None:
        raise RuntimeError("Supabase not configured; persisted NCAAB odds unavailable")
    return load_persisted_odds(table_name=TABLE_NAME)


def _records(summary: str | None) -> tuple[int, int]:
    """(wins, games) from an ESPN "W-L" or "W-L-T" summary."""
    parts = [nfl._to_int(part) for part in str(summary or "").split("-")]
    if len(parts) < 2:
        return 0, 0
    return parts[0], sum(parts)


def _standings_entries(group: dict, conference: dict | None = None):
    """Yield (entry, conference group), descending into nested child groups."""
    if group.get("isConference"):
        conference = group
    for entry in (group.get("standings") or {}).get("entries", []):
        yield entry, conference or {}
    for child in group.get("children", []):
        yield from _standings_entries(child, conference)


async def _standings_for_season(season: int) -> dict[str, dict]:
    try:
        response = await get_http_client().get(
            ESPN_STANDINGS_URL, params={"season": season, "group": 50}
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("NCAAB standings fetch failed for %s: %s", season, exc)
        return {}

    result: dict[str, dict] = {}
    for entry, conference in _standings_entries(payload):
        tri = (entry.get("team") or {}).get("abbreviation")
        if not tri:
            continue
        # Key on `type`, not `name`: each split (home, away, vs conference,
        # vs ranked) repeats names like "pointsFor" and would overwrite the
        # overall value.
        stats = {stat.get("type"): stat for stat in entry.get("stats", [])}
        _, games = _records(stats.get("total", {}).get("displayValue"))
        points_for = nfl._to_float(stats.get("avgpointsfor", {}).get("value"), None)
        points_against = nfl._to_float(stats.get("avgpointsagainst", {}).get("value"), None)
        result[tri] = {
            "conference": conference.get("shortName") or conference.get("name") or "N/A",
            "streak": stats.get("streak", {}).get("displayValue") or "N/A",
            "pointsFor": round(points_for, 1) if games and points_for is not None else None,
            "pointsAgainst": round(points_against, 1) if games and points_against is not None else None,
        }
    return result


async def _standings(season: int) -> dict[str, dict]:
    cached = cache.get("standings")
    if cached and cached.get("season") == season:
        return cached["teams"]
    teams = await _standings_for_season(season)
    if teams:
        cache.set("standings", {"season": season, "teams": teams})
        return teams
    stale = cache.get_stale("standings")
    return stale["teams"] if stale and stale.get("season") == season else {}


def _rank(competitor: dict) -> int | None:
    current = nfl._to_int((competitor.get("curatedRank") or {}).get("current"), UNRANKED)
    return None if current in (0, UNRANKED) else current


def _status(competition: dict) -> tuple[int, str, int, str | None]:
    """Men's college basketball plays halves, then five-minute overtimes."""
    status = competition.get("status") or {}
    status_type = status.get("type") or {}
    state = status_type.get("state", "pre")
    period = nfl._to_int(status.get("period"))
    clock = status.get("displayClock") or None
    if state == "in":
        short_detail = status_type.get("shortDetail") or ""
        if status_type.get("name") == "STATUS_HALFTIME" or short_detail.lower() == "halftime":
            return 2, short_detail or "Halftime", period, clock
        label = f"{period}H" if period <= 2 else "OT" if period == 3 else f"{period - 2}OT"
        return 2, f"{label} {clock or ''}".strip(), period, clock
    if state == "post":
        return 3, "F/OT" if period > 2 else "Final", period, None
    return 1, "", 0, None


def _project_score(odds: dict) -> tuple[float | None, float | None, float | None]:
    """Market-implied scores only; a partial line is not a projection."""
    total = nfl._to_float(odds.get("overUnder"), None)
    spread = nfl._to_float(odds.get("homeSpread"), None)
    if total is None or spread is None:
        return None, None, None
    home = total / 2 - spread / 2
    return total - home, home, total


def _season_year(event: dict, slate_date: date) -> int:
    # ESPN names the season by its end year (2026-27 is 2027).
    fallback = slate_date.year + (slate_date.month >= 8)
    return nfl._to_int((event.get("season") or {}).get("year"), fallback)


def _team_payload(competitor: dict, stats: dict, predicted_score: float | None, win_probability) -> dict:
    team = competitor.get("team") or {}
    tri = team.get("abbreviation", "")
    record = nfl._overall_record(competitor)
    wins, games = _records(record)
    rank = _rank(competitor)
    return {
        "name": team.get("displayName") or team.get("location") or tri,
        "abbreviation": tri,
        # College logos live at teamlogos/ncaa/500/{id}.png; abbreviations 404.
        "espnSlug": str(team.get("id") or tri.lower()),
        "record": record,
        "winPct": round(wins / games, 3) if games else 0.5,
        "conference": stats.get("conference", "N/A"),
        "streak": stats.get("streak", "N/A"),
        "score": nfl._to_int(competitor.get("score")),
        "predictedScore": predicted_score,
        "winProbability": win_probability,
        "pointsFor": stats.get("pointsFor"),
        "pointsAgainst": stats.get("pointsAgainst"),
        "rank": rank,
        "conferenceId": str(team.get("conferenceId") or ""),
    }


def _matchup(event: dict, odds: dict, standings: dict, slate_date: date) -> dict | None:
    competition = (event.get("competitions") or [{}])[0]
    competitors = {item.get("homeAway"): item for item in competition.get("competitors", [])}
    away_comp = competitors.get("away") or {}
    home_comp = competitors.get("home") or {}
    away_tri = (away_comp.get("team") or {}).get("abbreviation", "")
    home_tri = (home_comp.get("team") or {}).get("abbreviation", "")
    if not away_tri or not home_tri:
        return None

    starts_at = event.get("date", "")
    game_date = nfl._event_date_et(event, slate_date)
    away_stats = standings.get(away_tri, {})
    home_stats = standings.get(home_tri, {})
    projected_away, projected_home, projected_total = _project_score(odds)
    game_status, status_text, period, game_clock = _status(competition)
    if game_status == 1:
        status_text = format_et_time(starts_at)

    spread_line, favorite, spread_value = nfl._spread_display(away_tri, home_tri, odds)
    away_score = nfl._to_int(away_comp.get("score"))
    home_score = nfl._to_int(home_comp.get("score"))
    over_under = odds.get("overUnder")
    spread_result = total_result = None
    if game_status == 3:
        if spread_value is not None:
            spread_result = grade_spread_result(away_score, home_score, favorite, spread_value)
        if over_under is not None:
            actual_total = away_score + home_score
            total_result = "over" if actual_total > over_under else "under" if actual_total < over_under else "push"

    season = event.get("season") or {}
    broadcasts = [name for item in competition.get("broadcasts", []) for name in item.get("names", [])]
    return {
        "sport": "ncaab",
        "gameId": str(event.get("id", "")),
        "status": status_text,
        "gameStatus": game_status,
        "period": period,
        "gameClock": game_clock,
        "date": game_date.isoformat(),
        "slateDate": slate_date.isoformat(),
        "time": format_et_time(starts_at),
        "venue": (competition.get("venue") or {}).get("fullName", "TBD"),
        "broadcast": ", ".join(broadcasts) or "N/A",
        "seasonType": season.get("slug", "regular-season"),
        "statsSeason": _season_year(event, slate_date),
        "model": "MARKET (DK)",
        "spreadLine": spread_line,
        "spreadResult": spread_result,
        "pregameSpread": odds.get("homeSpread"),
        "spreadOddsHome": odds.get("homeSpreadOdds"),
        "spreadOddsAway": odds.get("awaySpreadOdds"),
        "homeMoneyline": odds.get("homeMoneyline"),
        "awayMoneyline": odds.get("awayMoneyline"),
        "overUnder": over_under,
        "overOdds": odds.get("overOdds"),
        "underOdds": odds.get("underOdds"),
        "totalResult": total_result,
        "projectedTotal": projected_total,
        "away": _team_payload(away_comp, away_stats, projected_away, odds.get("awayWinProbability")),
        "home": _team_payload(home_comp, home_stats, projected_home, odds.get("homeWinProbability")),
    }


async def _assemble_slate() -> list[dict]:
    # Display anchors on the 6am-rollover slate date so a late West Coast
    # tipoff stays on the board past midnight ET.
    slate_date = slate_date_et()
    scoreboard_task = fetch_espn_scoreboard(ESPN_SCOREBOARD_URL, slate_date, SCOREBOARD_PARAMS)
    odds_task = asyncio.to_thread(_load_persisted_strict)
    scoreboard, persisted = await asyncio.gather(
        scoreboard_task, odds_task, return_exceptions=True
    )
    if isinstance(persisted, BaseException):
        logger.warning("NCAAB persisted odds load failed: %s", persisted)
        raise HTTPException(status_code=503, detail="NCAAB odds feed unavailable")
    if isinstance(scoreboard, BaseException):
        raise scoreboard

    events = sorted(scoreboard.get("events", []), key=lambda event: str(event.get("date", "")))
    if not events:
        return []

    # Load-then-merge, as fetch_sgo_odds does: fresh ESPN odds win, persisted
    # values cover games that have tipped off and lost their odds block.
    fresh, _ = extract_espn_odds(events, persisted, sport="ncaab")
    odds_map = {**persisted, **fresh}

    season = _season_year(events[0], slate_date)
    standings = await _standings(season)

    refined = []
    for event in events:
        matchup_key = nfl._event_matchup_key(event)
        game_date = nfl._event_date_et(event, slate_date)
        odds = odds_map.get((game_date.isoformat(), matchup_key)) if matchup_key else None
        # Only games with a line: fresh or persisted, never fresh alone, or
        # every game would drop off the board at tipoff.
        if not has_market(odds):
            continue
        matchup = _matchup(event, odds, standings, slate_date)
        if matchup:
            refined.append(matchup)
    return refined


@router.get("/today")
async def get_todays_games():
    cached = cache.get("assembled")
    if cached:
        return cached
    try:
        refined = await _assemble_slate()
        has_live = any(game.get("gameStatus") == 2 for game in refined)
        cache.set("assembled", refined, ttl=CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT)
        return refined
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("NCAAB /today failed: %s", exc, exc_info=True)
        return cache.get_stale("assembled") or []


async def get_odds_map(
    target_date: date | None = None,
    collection_stats: dict | None = None,
):
    return await fetch_espn_odds(
        sport="ncaab",
        scoreboard_url=ESPN_SCOREBOARD_URL,
        table_name=TABLE_NAME,
        target_date=target_date,
        scoreboard_params=SCOREBOARD_PARAMS,
        collection_stats=collection_stats,
    )


@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds(offset: CronOffset = 0):
    # Collection stays on the calendar date; the 6am rollover is display-only.
    target = datetime.now(ET).date() + timedelta(days=offset)
    try:
        stats: dict = {}
        odds, fresh_count = await get_odds_map(target_date=target, collection_stats=stats)
        cache.set_ttl("assembled", 0)
        return {
            # A handled write failure keeps its stats; the collection ran and
            # its result is known. The lines may be gone from ESPN by the next
            # scheduled run, so this must alert rather than pass quietly.
            "success": bool(stats.get("latest_persistence_ok")),
            "date": target.isoformat(),
            "fresh_from_api": fresh_count,
            "persisted_loaded": max(0, len(odds) - fresh_count),
            "total_matchups": len(odds),
            "persisted_to": f"supabase:{TABLE_NAME}+odds_snapshots",
            **stats,
        }
    except Exception as exc:
        logger.error("NCAAB odds collection failed for %s: %s", target, exc, exc_info=True)
        return {"success": False, "date": target.isoformat(), "error": str(exc)}
