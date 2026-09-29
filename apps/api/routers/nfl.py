from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta
from typing import Annotated, overload

from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import CronOffset, require_cron_auth
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    Cache,
    fetch_sgo_odds,
    format_et_time,
    grade_spread_result,
    get_http_client,
    load_persisted_odds,
    slate_date_et,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nfl", tags=["NFL"])

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/football/nfl/standings"
ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
NFL_LOOKAHEAD_DAYS = 7
NFLCollectionLookahead = Annotated[
    int,
    Query(
        ge=0,
        le=NFL_LOOKAHEAD_DAYS,
        description="Additional ET days to scan for scheduled NFL games.",
    ),
]

cache = Cache({
    "scoreboard": CACHE_TTL_DEFAULT,
    "standings": 3600,
    "odds": CACHE_TTL_ODDS,
    "assembled": CACHE_TTL_DEFAULT,
    "weekly": CACHE_TTL_DEFAULT,
})

NFL_TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WSH": "Washington Commanders",
}

_SGO_TEAM_TO_TRI = {
    full_name.upper().replace(" ", "_"): tri
    for tri, full_name in NFL_TEAM_NAMES.items()
}
_SGO_TEAM_TO_TRI.update({tri: tri for tri in NFL_TEAM_NAMES})
_SGO_TEAM_TO_TRI.update({
    "JAC": "JAX", "JACKSONVILLE": "JAX", "LA_CHARGERS": "LAC",
    "LA_RAMS": "LAR", "LOS_ANGELES_CHARGERS": "LAC",
    "LOS_ANGELES_RAMS": "LAR", "LAS_VEGAS": "LV",
    "WASHINGTON": "WSH", "WASHINGTON_FOOTBALL_TEAM": "WSH",
})


def get_tricode_from_sgo_team_id(team_id: str) -> str:
    key = (team_id or "").upper()
    if key.endswith("_NFL"):
        key = key[:-4]
    return _SGO_TEAM_TO_TRI.get(key, "")


def get_tricode_from_team_name(team_name: str) -> str:
    """Map provider display names without changing the SGO main-line mapper."""
    key = (team_name or "").strip().upper().removesuffix("_NFL")
    key = "_".join(key.split())
    return {"ARI_CARDINALS": "ARI"}.get(key) or _SGO_TEAM_TO_TRI.get(key, "")


async def get_odds_map(
    target_date: date | None = None,
    collection_stats: dict | None = None,
    *,
    request_time: datetime | None = None,
    sharp_market_cache: dict[str, list[dict]] | None = None,
):
    return await fetch_sgo_odds(
        sport="nfl",
        league_id="NFL",
        table_name="nfl_odds",
        tricode_for=get_tricode_from_sgo_team_id,
        fallback_tricode_for=get_tricode_from_team_name,
        target_date=target_date,
        request_time=request_time,
        sharp_market_cache=sharp_market_cache,
        collection_stats=collection_stats,
    )


async def _scoreboard_for_date_with_status(
    target_date: date,
) -> tuple[dict, bool]:
    try:
        response = await get_http_client().get(
            ESPN_SCOREBOARD_URL,
            params={"dates": target_date.strftime("%Y%m%d"), "limit": 100},
        )
        response.raise_for_status()
        return response.json(), True
    except Exception as exc:
        logger.warning("NFL scoreboard fetch failed for %s: %s", target_date, exc)
        return {}, False


async def _scoreboard_for_date(target_date: date) -> dict:
    scoreboard, _ = await _scoreboard_for_date_with_status(target_date)
    return scoreboard


def _event_week_key(event: dict) -> tuple[int, int, int] | None:
    try:
        season = event.get("season") or {}
        week = event.get("week") or {}
        key = (
            int(season.get("year")),
            int(season.get("type")),
            int(week.get("number")),
        )
    except (TypeError, ValueError):
        return None
    return key if all(key) else None


async def _scoreboard_for_week(anchor_event: dict) -> dict:
    week_key = _event_week_key(anchor_event)
    if not week_key:
        return {}

    season_year, season_type, week_number = week_key
    try:
        response = await get_http_client().get(
            ESPN_SCOREBOARD_URL,
            params={
                "dates": str(season_year),
                "seasontype": season_type,
                "week": week_number,
                "limit": 100,
            },
        )
        response.raise_for_status()
        scoreboard = response.json()
    except Exception as exc:
        logger.warning(
            "NFL weekly scoreboard fetch failed for %s/%s week %s: %s",
            season_year,
            season_type,
            week_number,
            exc,
        )
        return {}

    scoreboard["events"] = [
        event
        for event in scoreboard.get("events", [])
        if _event_week_key(event) == week_key
    ]
    return scoreboard


async def _next_scoreboard(start_date: date) -> tuple[dict, date]:
    for offset in range(NFL_LOOKAHEAD_DAYS + 1):
        target = start_date + timedelta(days=offset)
        scoreboard = await _scoreboard_for_date(target)
        if scoreboard.get("events"):
            return scoreboard, target
    return {}, start_date


async def _scheduled_collection_dates(
    start_date: date,
    lookahead_days: int,
) -> tuple[list[date], list[date]]:
    if lookahead_days == 0:
        return [start_date], []

    targets = [
        start_date + timedelta(days=offset)
        for offset in range(lookahead_days + 1)
    ]
    scoreboards = await asyncio.gather(
        *(_scoreboard_for_date_with_status(target) for target in targets)
    )

    collection_dates = []
    unavailable_dates = []
    for target, (scoreboard, available) in zip(targets, scoreboards):
        if not available:
            # Query SGO directly when ESPN is unavailable rather than risk missing
            # a rescheduled game on that date.
            unavailable_dates.append(target)
            collection_dates.append(target)
        elif scoreboard.get("events"):
            collection_dates.append(target)
    return collection_dates, unavailable_dates


def _stat_map(stats: list[dict]) -> dict[str, dict]:
    return {str(stat.get("name")): stat for stat in stats}


@overload
def _to_float(value, default: None) -> float | None: ...


@overload
def _to_float(value, default: float = 0.0) -> float: ...


def _to_float(value, default: float | None = 0.0) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _to_int(value, default=0) -> int:
    parsed = _to_float(value, None)
    return default if parsed is None else int(parsed)


async def _standings_for_season(season: int) -> dict[str, dict]:
    try:
        response = await get_http_client().get(
            ESPN_STANDINGS_URL,
            params={"season": season, "seasontype": 2, "region": "us", "lang": "en"},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("NFL standings fetch failed for %s: %s", season, exc)
        return {}

    result: dict[str, dict] = {}
    for conference in payload.get("children", []):
        conference_name = conference.get("abbreviation")
        if not conference_name:
            conference_name = "AFC" if "American" in conference.get("name", "") else "NFC"
        for entry in conference.get("standings", {}).get("entries", []):
            team = entry.get("team", {})
            tri = team.get("abbreviation", "")
            if not tri:
                continue
            stats = _stat_map(entry.get("stats", []))

            def value(name: str, default=0):
                return stats.get(name, {}).get("value", default)

            games = _to_int(value("gamesPlayed", 0))
            if not games:
                games = _to_int(value("wins", 0)) + _to_int(value("losses", 0)) + _to_int(value("ties", 0))
            points_for = _to_float(value("pointsFor"), None)
            points_against = _to_float(value("pointsAgainst"), None)
            result[tri] = {
                "record": stats.get("overall", {}).get("displayValue")
                    or f'{_to_int(value("wins", 0))}-{_to_int(value("losses", 0))}',
                "winPct": _to_float(value("winPercent", 0.5), 0.5),
                "seed": _to_int(value("playoffSeed", 0)),
                "conference": conference_name,
                "streak": stats.get("streak", {}).get("displayValue") or "N/A",
                "pointsFor": round(points_for / games, 1) if games and points_for is not None else None,
                "pointsAgainst": round(points_against / games, 1) if games and points_against is not None else None,
            }
    return result


def _overall_record(competitor: dict) -> str:
    for record in competitor.get("records", []):
        if record.get("type") == "total" or record.get("name") == "overall":
            return record.get("summary") or "0-0"
    return "0-0"


def _status(competition: dict) -> tuple[int, str, int, str | None]:
    status = competition.get("status", {})
    state = status.get("type", {}).get("state", "pre")
    period = int(status.get("period", 0) or 0)
    clock = status.get("displayClock") or None
    if state == "in":
        label = f"OT {clock}" if period > 4 else f"Q{period} {clock}"
        return 2, label.strip(), period, clock
    if state == "post":
        return 3, "F/OT" if period > 4 else "Final", period, None
    return 1, "", 0, None


def _week_label(event: dict) -> str:
    week = int(event.get("week", {}).get("number", 0) or 0)
    season_type = int(event.get("season", {}).get("type", 2) or 2)
    if season_type == 1:
        return "HOF" if week <= 1 else f"PRE W{week - 1}"
    if season_type == 3:
        return f"POST W{week}"
    return f"WK{week}"


def _spread_display(away_tri: str, home_tri: str, odds: dict) -> tuple[str, str | None, float | None]:
    away_spread = odds.get("awaySpread")
    home_spread = odds.get("homeSpread")
    if away_spread is None or home_spread is None:
        return "N/A", None, None
    away_spread = _to_float(away_spread)
    home_spread = _to_float(home_spread)
    if away_spread < 0:
        return f"{away_tri} {away_spread:g}", "away", away_spread
    return f"{home_tri} {home_spread:g}", "home", home_spread


def _project_score(away_stats: dict, home_stats: dict, odds: dict) -> tuple[int, int, int]:
    values = [
        away_stats.get("pointsFor"), away_stats.get("pointsAgainst"),
        home_stats.get("pointsFor"), home_stats.get("pointsAgainst"),
    ]
    parsed_values = [_to_float(value, None) for value in values]
    available = [value for value in parsed_values if value is not None]
    fallback_total = (sum(available) / len(available) * 2) if available else 44.0
    market_total = odds.get("overUnder")
    total = _to_float(market_total, fallback_total) if market_total is not None else fallback_total

    home_spread = odds.get("homeSpread")
    if home_spread is not None:
        home_margin = -_to_float(home_spread)
    else:
        away_base = (
            (_to_float(away_stats.get("pointsFor"), None) or 22)
            + (_to_float(home_stats.get("pointsAgainst"), None) or 22)
        ) / 2
        home_base = (
            (_to_float(home_stats.get("pointsFor"), None) or 22)
            + (_to_float(away_stats.get("pointsAgainst"), None) or 22)
        ) / 2
        home_margin = home_base - away_base

    away = max(0, round((total - home_margin) / 2))
    home = max(0, round((total + home_margin) / 2))
    return away, home, round(total)


def _is_early_window_start(starts_at: str | None, target: date) -> bool:
    try:
        local_start = datetime.fromisoformat(
            str(starts_at).replace("Z", "+00:00")
        ).astimezone(ET)
    except (TypeError, ValueError):
        return False
    return local_start.date() == target and 13 <= local_start.hour < 15


def _early_window_count(starts_at_values, target: date) -> int:
    return sum(
        _is_early_window_start(starts_at, target)
        for starts_at in starts_at_values
    )


def _event_matchup_key(event: dict) -> str | None:
    competitions = event.get("competitions") or []
    if not competitions:
        return None
    competitors = {
        competitor.get("homeAway"): competitor
        for competitor in competitions[0].get("competitors", [])
    }
    away = (competitors.get("away") or {}).get("team", {}).get("abbreviation")
    home = (competitors.get("home") or {}).get("team", {}).get("abbreviation")
    return f"{away}@{home}" if away and home else None


def _early_window_matchup_keys(events: list[dict], target: date) -> set[str]:
    keys = set()
    for event in events:
        if not _is_early_window_start(event.get("date"), target):
            continue
        matchup_key = _event_matchup_key(event)
        if matchup_key:
            keys.add(matchup_key)
    return keys


def _team_payload(competitor: dict, stats: dict, predicted_score: int, win_probability) -> dict:
    team = competitor.get("team", {})
    tri = team.get("abbreviation", "")
    return {
        "name": team.get("name") or NFL_TEAM_NAMES.get(tri, tri),
        "abbreviation": tri,
        "espnSlug": tri.lower(),
        "record": _overall_record(competitor),
        "winPct": stats.get("winPct", 0.5),
        "seed": stats.get("seed", 0),
        "conference": stats.get("conference", "N/A"),
        "streak": stats.get("streak", "N/A"),
        "score": int(competitor.get("score", 0) or 0),
        "predictedScore": predicted_score,
        "winProbability": win_probability,
        "pointsFor": stats.get("pointsFor"),
        "pointsAgainst": stats.get("pointsAgainst"),
        "yardsPG": None,
        "yardsAllowedPG": None,
        "turnoverDiff": None,
    }


def _event_date_et(event: dict, fallback: date) -> date:
    try:
        return datetime.fromisoformat(
            str(event.get("date", "")).replace("Z", "+00:00")
        ).astimezone(ET).date()
    except (TypeError, ValueError):
        return fallback


def _load_persisted_odds_for_dates(game_dates: list[date]) -> dict:
    odds = {}
    for game_date in game_dates:
        odds.update(load_persisted_odds(game_date, table_name="nfl_odds"))
    return odds


async def _assemble_slate(*, full_week: bool = False) -> list[dict]:
    today = slate_date_et()
    scoreboard, slate_date = await _next_scoreboard(today)
    events = scoreboard.get("events", [])
    if not events:
        return []

    if full_week:
        weekly_scoreboard = await _scoreboard_for_week(events[0])
        weekly_events = weekly_scoreboard.get("events", [])
        if weekly_events:
            events = weekly_events

    events = sorted(events, key=lambda event: str(event.get("date", "")))
    first_event = events[0]
    season_year = int(first_event.get("season", {}).get("year", today.year))
    season_type = int(first_event.get("season", {}).get("type", 2) or 2)
    context_season = season_year - 1 if season_type == 1 else season_year

    standings = await _standings_for_season(context_season)
    game_dates = sorted({_event_date_et(event, slate_date) for event in events})
    odds_map = await asyncio.to_thread(_load_persisted_odds_for_dates, game_dates)

    refined = []
    for event in events:
        competitions = event.get("competitions", [])
        if not competitions:
            continue
        competition = competitions[0]
        competitors = {item.get("homeAway"): item for item in competition.get("competitors", [])}
        away_comp = competitors.get("away", {})
        home_comp = competitors.get("home", {})
        away_tri = away_comp.get("team", {}).get("abbreviation", "")
        home_tri = home_comp.get("team", {}).get("abbreviation", "")
        if not away_tri or not home_tri:
            continue

        starts_at = event.get("date", "")
        game_date = _event_date_et(event, slate_date)
        matchup_key = f"{away_tri}@{home_tri}"
        odds = odds_map.get((game_date.isoformat(), matchup_key), {})
        away_stats = standings.get(away_tri, {})
        home_stats = standings.get(home_tri, {})
        projected_away, projected_home, projected_total = _project_score(away_stats, home_stats, odds)
        game_status, status_text, period, game_clock = _status(competition)
        if game_status == 1:
            status_text = format_et_time(starts_at)

        spread_line, favorite, spread_value = _spread_display(away_tri, home_tri, odds)
        away_score = int(away_comp.get("score", 0) or 0)
        home_score = int(home_comp.get("score", 0) or 0)
        spread_result = None
        total_result = None
        over_under = odds.get("overUnder")
        if game_status == 3:
            if spread_value is not None:
                spread_result = grade_spread_result(
                    away_score,
                    home_score,
                    favorite,
                    spread_value,
                )
            if over_under is not None:
                actual_total = away_score + home_score
                total_result = "over" if actual_total > over_under else "under" if actual_total < over_under else "push"

        broadcasts = [name for item in competition.get("broadcasts", []) for name in item.get("names", [])]
        season_slug = event.get("season", {}).get("slug", "regular-season")
        refined.append({
            "sport": "nfl",
            "gameId": str(event.get("id", "")),
            "status": status_text,
            "gameStatus": game_status,
            "period": period,
            "gameClock": game_clock,
            "date": game_date.isoformat(),
            "time": format_et_time(starts_at),
            "venue": competition.get("venue", {}).get("fullName", "TBD"),
            "broadcast": ", ".join(broadcasts) or competition.get("broadcast") or "Local",
            "week": int(event.get("week", {}).get("number", 0) or 0),
            "weekLabel": _week_label(event),
            "seasonType": season_slug,
            "statsSeason": context_season,
            "model": "MARKET + TEAM STATS",
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
        })
    return refined


@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds(
    offset: CronOffset = 0,
    verify_early_window: bool = False,
    lookahead_days: NFLCollectionLookahead = 0,
):
    try:
        request_time = datetime.now(ET)
        start_date = request_time.date() + timedelta(days=offset)
        collection_dates, unavailable_dates = await _scheduled_collection_dates(
            start_date,
            lookahead_days,
        )
        odds = {}
        fresh_count = 0
        collection_stats = {
            "pregame_snapshot_candidates": 0,
            "pregame_snapshot_matchups": [],
            "snapshots_inserted": 0,
            "snapshots_deduplicated": 0,
            "snapshot_persistence_ok": True,
        }
        sharp_market_cache: dict[str, list[dict]] = {}
        for target in collection_dates:
            target_stats: dict = {}
            target_odds, target_fresh_count = await get_odds_map(
                target_date=target,
                collection_stats=target_stats,
                request_time=request_time,
                sharp_market_cache=sharp_market_cache,
            )
            odds.update(target_odds)
            fresh_count += target_fresh_count
            collection_stats["pregame_snapshot_candidates"] += target_stats.get(
                "pregame_snapshot_candidates", 0
            )
            collection_stats["pregame_snapshot_matchups"].extend(
                target_stats.get("pregame_snapshot_matchups", [])
            )
            collection_stats["snapshots_inserted"] += target_stats.get(
                "snapshots_inserted", 0
            )
            collection_stats["snapshots_deduplicated"] += target_stats.get(
                "snapshots_deduplicated", 0
            )
            collection_stats["snapshot_persistence_ok"] = (
                collection_stats["snapshot_persistence_ok"]
                and target_stats.get("snapshot_persistence_ok", False)
            )
        cache.set("odds", odds)
        cache.set("assembled", [])
        cache.set("weekly", [])
        result = {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": max(0, len(odds) - fresh_count),
            "total_matchups": len(odds),
            "persisted_to": "supabase:nfl_odds+odds_snapshots",
            "date": start_date.isoformat(),
            "dates": [target.isoformat() for target in collection_dates],
            "lookahead_days": lookahead_days,
            "schedule_lookup_failures": [
                target.isoformat() for target in unavailable_dates
            ],
            "early_window_verification_requested": verify_early_window,
            "pregame_snapshot_candidates": collection_stats.get(
                "pregame_snapshot_candidates", 0
            ),
            "snapshots_inserted": collection_stats.get("snapshots_inserted", 0),
            "snapshots_deduplicated": collection_stats.get(
                "snapshots_deduplicated", 0
            ),
            "snapshot_persistence_ok": collection_stats.get(
                "snapshot_persistence_ok", False
            ),
        }
        if verify_early_window:
            scoreboard, scoreboard_ok = await _scoreboard_for_date_with_status(
                start_date
            )
            expected_matchups = _early_window_matchup_keys(
                scoreboard.get("events", []), start_date
            )
            candidate_matchups = set(
                collection_stats.get("pregame_snapshot_matchups", [])
            )
            result.update({
                "early_window_scoreboard_ok": scoreboard_ok,
                "early_window_games": len(expected_matchups),
                "early_window_snapshot_candidates": len(
                    expected_matchups & candidate_matchups
                ),
            })
        return result
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.get("/today")
async def get_todays_games():
    cached = cache.get("assembled")
    if cached:
        return cached
    try:
        refined = await _assemble_slate()
        has_live = any(game.get("gameStatus") == 2 for game in refined)
        ttl = CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT
        cache.set("assembled", refined, ttl=ttl)
        return refined
    except Exception as exc:
        logger.error("NFL /today failed: %s", exc, exc_info=True)
        return cache.get_stale("assembled") or []


@router.get("/week")
async def get_current_week_games():
    cached = cache.get("weekly")
    if cached:
        return cached
    try:
        refined = await _assemble_slate(full_week=True)
        has_live = any(game.get("gameStatus") == 2 for game in refined)
        ttl = CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT
        cache.set("weekly", refined, ttl=ttl)
        return refined
    except Exception as exc:
        logger.error("NFL /week failed: %s", exc, exc_info=True)
        return cache.get_stale("weekly") or []


def _split_pair(value: str, separator: str) -> tuple[int, int]:
    pieces = str(value or "").split(separator, 1)
    return (_to_int(pieces[0]), _to_int(pieces[1]) if len(pieces) > 1 else 0)


def _quarter_score(competitor: dict) -> dict:
    scores = [_to_int(item.get("displayValue")) for item in competitor.get("linescores", [])]
    overtime = scores[4:] if len(scores) > 4 else []
    return {
        "q1": scores[0] if len(scores) > 0 else 0,
        "q2": scores[1] if len(scores) > 1 else 0,
        "q3": scores[2] if len(scores) > 2 else 0,
        "q4": scores[3] if len(scores) > 3 else 0,
        "ot": overtime,
        "total": _to_int(competitor.get("score")),
    }


def _team_box_score(team_box: dict | None, player_box: dict | None) -> dict | None:
    if not team_box and not player_box:
        return None
    if not (team_box or {}).get("statistics") and not (player_box or {}).get("statistics"):
        return None
    team_stats = {
        item.get("name"): item.get("displayValue")
        for item in (team_box or {}).get("statistics", [])
    }
    penalties, penalty_yards = _split_pair(team_stats.get("totalPenaltiesYards", ""), "-")
    result = {
        "passing": [], "rushing": [], "receiving": [],
        "teamStats": {
            "firstDowns": _to_int(team_stats.get("firstDowns")),
            "totalYards": _to_int(team_stats.get("totalYards")),
            "passingYards": _to_int(team_stats.get("netPassingYards")),
            "rushingYards": _to_int(team_stats.get("rushingYards")),
            "penalties": penalties,
            "penaltyYards": penalty_yards,
            "turnovers": _to_int(team_stats.get("turnovers")),
            "possession": team_stats.get("possessionTime") or "--",
            "thirdDownEff": team_stats.get("thirdDownEff") or "--",
            "fourthDownEff": team_stats.get("fourthDownEff") or "--",
            "redZoneEff": team_stats.get("redZoneAttempts") or "--",
        },
    }

    for group in (player_box or {}).get("statistics", []):
        name = group.get("name")
        labels = group.get("labels", [])
        for row in group.get("athletes", []):
            values = dict(zip(labels, row.get("stats", [])))
            athlete_name = row.get("athlete", {}).get("displayName", "Unknown")
            if name == "passing":
                completions, attempts = _split_pair(values.get("C/ATT", ""), "/")
                sacks, _ = _split_pair(values.get("SACKS", ""), "-")
                result["passing"].append({
                    "name": athlete_name, "completions": completions, "attempts": attempts,
                    "yards": _to_int(values.get("YDS")), "touchdowns": _to_int(values.get("TD")),
                    "interceptions": _to_int(values.get("INT")),
                    "rating": _to_float(values.get("RTG")), "sacks": sacks,
                })
            elif name == "rushing":
                result["rushing"].append({
                    "name": athlete_name, "carries": _to_int(values.get("CAR")),
                    "yards": _to_int(values.get("YDS")), "average": _to_float(values.get("AVG")),
                    "touchdowns": _to_int(values.get("TD")), "longest": _to_int(values.get("LONG")),
                })
            elif name == "receiving":
                result["receiving"].append({
                    "name": athlete_name, "receptions": _to_int(values.get("REC")),
                    "targets": _to_int(values.get("TGTS")), "yards": _to_int(values.get("YDS")),
                    "average": _to_float(values.get("AVG")), "touchdowns": _to_int(values.get("TD")),
                    "longest": _to_int(values.get("LONG")),
                })
    return result


def _play_by_play(summary: dict) -> list[dict]:
    drives = summary.get("drives") or {}
    all_drives = list(drives.get("previous") or [])
    if drives.get("current"):
        all_drives.append(drives["current"])
    plays = []
    for drive in all_drives:
        team = (drive.get("team") or {}).get("abbreviation")
        for play in drive.get("plays", []):
            plays.append({
                "id": str(play.get("id") or play.get("sequenceNumber") or len(plays)),
                "eventId": str(play.get("id") or ""),
                "sequence": _to_int(play.get("sequenceNumber"), len(plays)),
                "clock": (play.get("clock") or {}).get("displayValue", ""),
                "quarter": _to_int((play.get("period") or {}).get("number")),
                "team": team,
                "event": play.get("text") or (play.get("type") or {}).get("text", "Play"),
                "awayScore": _to_int(play.get("awayScore")),
                "homeScore": _to_int(play.get("homeScore")),
                "sourceTimestamp": play.get("wallclock") or play.get("modified"),
            })
    return sorted(plays, key=lambda play: play.get("sequence", 0))


@router.get("/game/{game_id}")
async def get_game_detail(game_id: str):
    matchups = await get_current_week_games()
    matchup = next((game for game in matchups if str(game.get("gameId")) == str(game_id)), None)
    if not matchup:
        raise HTTPException(status_code=404, detail="NFL game not found in the current slate")

    try:
        response = await get_http_client().get(ESPN_SUMMARY_URL, params={"event": game_id})
        response.raise_for_status()
        summary = response.json()
    except Exception as exc:
        logger.warning("NFL detail fetch failed for %s: %s", game_id, exc)
        summary = {}

    competition = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    competitors = {item.get("homeAway"): item for item in competition.get("competitors", [])}
    away_comp = competitors.get("away", {})
    home_comp = competitors.get("home", {})
    game_status = int(matchup.get("gameStatus", 1) or 1)
    status = "live" if game_status == 2 else "final" if game_status == 3 else "preview"

    team_boxes = {item.get("homeAway"): item for item in (summary.get("boxscore") or {}).get("teams", [])}
    player_boxes = {
        item.get("team", {}).get("abbreviation"): item
        for item in (summary.get("boxscore") or {}).get("players", [])
    }
    away_tri = matchup["away"]["abbreviation"]
    home_tri = matchup["home"]["abbreviation"]
    plays = _play_by_play(summary)
    winprobability = summary.get("winprobability") or []
    win_probability = []
    denominator = max(len(winprobability) - 1, 1)
    for index, point in enumerate(winprobability):
        home_prob = _to_float(point.get("homeWinPercentage", 0.5), 0.5) * 100
        win_probability.append({
            "x": index / denominator * 100,
            "awayProb": 100 - home_prob,
            "homeProb": home_prob,
            "label": str(point.get("playId") or ""),
        })

    return {
        "sport": "nfl",
        "gameId": str(game_id),
        "matchup": matchup,
        "status": status,
        "rawStatus": (competition.get("status") or {}).get("type", {}).get("detail") or matchup.get("status", ""),
        "awayScore": _quarter_score(away_comp),
        "homeScore": _quarter_score(home_comp),
        "awayBoxScore": _team_box_score(team_boxes.get("away"), player_boxes.get(away_tri)),
        "homeBoxScore": _team_box_score(team_boxes.get("home"), player_boxes.get(home_tri)),
        "playByPlay": plays,
        "winProbability": win_probability,
    }
