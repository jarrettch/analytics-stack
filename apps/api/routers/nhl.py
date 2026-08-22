from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from .auth import CronOffset, require_cron_auth
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    Cache,
    fetch_sgo_odds,
    format_et_time,
    get_http_client,
    load_persisted_odds,
    slate_date_et,
    to_num,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nhl", tags=["NHL"])

cache = Cache({
    "scoreboard":   CACHE_TTL_DEFAULT,
    "standings":    300,
    "team_summary": 600,
    "odds":         CACHE_TTL_ODDS,
    "assembled":    CACHE_TTL_DEFAULT,
})

NHL_LOOKAHEAD_DAYS = 14


def _detail_status(game_status: int) -> str:
    if game_status == 2:
        return "live"
    if game_status == 3:
        return "final"
    return "preview"


def _safe_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _clock(play: dict) -> str:
    return play.get("timeInPeriod") or play.get("clock") or ""


def _nhl_linescore(team: dict) -> dict:
    by_period = team.get("periods") or []
    scores = [_safe_int(p.get("goals")) for p in by_period]
    while len(scores) < 3:
        scores.append(0)
    out = {
        "p1": scores[0],
        "p2": scores[1],
        "p3": scores[2],
        "total": _safe_int(team.get("score") or team.get("goals") or sum(scores)),
    }
    if len(scores) > 3:
        out["ot"] = scores[3]
    if len(scores) > 4:
        out["so"] = scores[4]
    return out


def _nhl_linescore_from_plays(data: dict, side: str) -> dict:
    team_id = (data.get(f"{side}Team") or {}).get("id")
    scores = [0, 0, 0]
    ot = None
    so = None
    total = _safe_int((data.get(f"{side}Team") or {}).get("score"))
    for play in data.get("plays") or []:
        if play.get("typeDescKey") != "goal":
            continue
        details = play.get("details") or {}
        if details.get("eventOwnerTeamId") != team_id:
            continue
        period = _safe_int((play.get("periodDescriptor") or {}).get("number"), 0)
        period_type = (play.get("periodDescriptor") or {}).get("periodType", "REG")
        if period_type == "SO":
            so = (so or 0) + 1
        elif period_type == "OT" or period > 3:
            ot = (ot or 0) + 1
        elif 1 <= period <= 3:
            scores[period - 1] += 1

    out = {"p1": scores[0], "p2": scores[1], "p3": scores[2], "total": total}
    if ot is not None:
        out["ot"] = ot
    if so is not None:
        out["so"] = so
    return out


def _empty_team_stats() -> dict:
    return {
        "shots": 0,
        "pim": 0,
        "powerPlayGoals": 0,
        "powerPlayOpportunities": 0,
        "faceoffWins": 0,
        "faceoffTotal": 0,
        "hits": 0,
        "blocks": 0,
        "giveaways": 0,
        "takeaways": 0,
    }


def _stat_pair(stats: dict, key: str) -> tuple[int, int]:
    item = stats.get(key) or {}
    if isinstance(item, dict):
        return _safe_int(item.get("awayValue")), _safe_int(item.get("homeValue"))
    return 0, 0


def _team_stats(raw: dict, side: str) -> dict:
    if not raw:
        return _empty_team_stats()
    first_value = next(iter(raw.values()), {}) if raw else {}
    if isinstance(first_value, dict) and "awayValue" in first_value:
        idx = 0 if side == "away" else 1
        shots = _stat_pair(raw, "shotsOnGoal")[idx]
        pim = _stat_pair(raw, "pim")[idx]
        hits = _stat_pair(raw, "hits")[idx]
        blocks = _stat_pair(raw, "blockedShots")[idx]
        giveaways = _stat_pair(raw, "giveaways")[idx]
        takeaways = _stat_pair(raw, "takeaways")[idx]
        faceoff_wins = _stat_pair(raw, "faceoffWinningPctg")[idx]
        pp_goals = _stat_pair(raw, "powerPlayGoals")[idx]
        pp_opps = _stat_pair(raw, "powerPlayOpportunities")[idx]
        return {
            "shots": shots,
            "pim": pim,
            "powerPlayGoals": pp_goals,
            "powerPlayOpportunities": pp_opps,
            "faceoffWins": faceoff_wins,
            "faceoffTotal": faceoff_wins * 2 if faceoff_wins else 0,
            "hits": hits,
            "blocks": blocks,
            "giveaways": giveaways,
            "takeaways": takeaways,
        }

    side_stats = raw.get(f"{side}Team") or raw.get(side) or {}
    return {
        "shots": _safe_int(side_stats.get("shotsOnGoal") or side_stats.get("shots")),
        "pim": _safe_int(side_stats.get("pim") or side_stats.get("penaltyMinutes")),
        "powerPlayGoals": _safe_int(side_stats.get("powerPlayGoals")),
        "powerPlayOpportunities": _safe_int(side_stats.get("powerPlayOpportunities")),
        "faceoffWins": _safe_int(side_stats.get("faceoffWins")),
        "faceoffTotal": _safe_int(side_stats.get("faceoffTotal")),
        "hits": _safe_int(side_stats.get("hits")),
        "blocks": _safe_int(side_stats.get("blockedShots") or side_stats.get("blocks")),
        "giveaways": _safe_int(side_stats.get("giveaways")),
        "takeaways": _safe_int(side_stats.get("takeaways")),
    }


def _live_team_stats_from_plays(data: dict, side: str) -> dict:
    team = data.get(f"{side}Team") or {}
    team_id = team.get("id")
    stats = _empty_team_stats()
    stats["shots"] = _safe_int(team.get("sog"))
    faceoff_total = 0

    for play in data.get("plays") or []:
        key = play.get("typeDescKey")
        details = play.get("details") or {}
        owner = details.get("eventOwnerTeamId")

        if key == "faceoff":
            faceoff_total += 1
            if details.get("winningPlayerId") and owner == team_id:
                stats["faceoffWins"] += 1
            continue

        if owner != team_id:
            continue

        if key == "hit":
            stats["hits"] += 1
        elif key == "blocked-shot":
            stats["blocks"] += 1
        elif key == "giveaway":
            stats["giveaways"] += 1
        elif key == "takeaway":
            stats["takeaways"] += 1
        elif key == "penalty":
            minutes = details.get("duration") or details.get("penaltyMinutes") or details.get("minutes")
            stats["pim"] += _safe_int(minutes, 2)
        elif key == "goal" and play.get("situationCode"):
            # situationCode is coarse but available live; count obvious PP goals
            situation = str(play.get("situationCode"))
            if situation and situation[0] != situation[-1]:
                stats["powerPlayGoals"] += 1

    stats["faceoffTotal"] = faceoff_total
    return stats


def _player_name(row: dict) -> str:
    name = row.get("name") or row.get("firstName") or {}
    if isinstance(name, dict):
        default = name.get("default")
        if default:
            return default
    first = row.get("firstName")
    last = row.get("lastName")
    if isinstance(first, dict):
        first = first.get("default")
    if isinstance(last, dict):
        last = last.get("default")
    return f"{first or ''} {last or ''}".strip() or "Unknown"


def _nhl_skaters(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        out.append({
            "name": _player_name(row),
            "position": row.get("positionCode") or row.get("position") or "",
            "goals": _safe_int(row.get("goals")),
            "assists": _safe_int(row.get("assists")),
            "points": _safe_int(row.get("points") or (_safe_int(row.get("goals")) + _safe_int(row.get("assists")))),
            "plusMinus": _safe_int(row.get("plusMinus")),
            "pim": _safe_int(row.get("pim") or row.get("penaltyMinutes")),
            "shots": _safe_int(row.get("sog") or row.get("shots")),
            "hits": _safe_int(row.get("hits")),
            "blocks": _safe_int(row.get("blockedShots") or row.get("blocks")),
            "toi": row.get("toi") or row.get("timeOnIce") or "",
        })
    return out


def _nhl_goalies(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows:
        shots_against = _safe_int(row.get("shotsAgainst"))
        saves = _safe_int(row.get("saves"))
        save_pct = row.get("savePctg")
        if save_pct is None and shots_against:
            save_pct = saves / shots_against
        out.append({
            "name": _player_name(row),
            "saves": saves,
            "shotsAgainst": shots_against,
            "savePct": f"{float(save_pct):.3f}".lstrip("0") if save_pct is not None else "--",
            "toi": row.get("toi") or row.get("timeOnIce") or "",
        })
    return out


def _nhl_team_box(data: dict, side: str, pbp_data: dict | None = None) -> dict:
    stats = data.get("playerByGameStats") or {}
    team_rows = stats.get(f"{side}Team") or {}
    forwards = team_rows.get("forwards") or []
    defense = team_rows.get("defense") or team_rows.get("defencemen") or []
    goalies = team_rows.get("goalies") or []
    return {
        "skaters": _nhl_skaters(forwards + defense),
        "goalies": _nhl_goalies(goalies),
        "teamStats": _team_stats(data.get("summary") or data.get("teamStats") or {}, side)
        if data.get("summary") or data.get("teamStats")
        else _live_team_stats_from_plays(pbp_data or data, side),
    }


def _team_id_map(data: dict, matchup: dict) -> dict:
    mapping = {}
    for side in ("awayTeam", "homeTeam"):
        team = data.get(side) or {}
        matchup_team = matchup.get("away") if side == "awayTeam" else matchup.get("home")
        tri = (matchup_team or {}).get("abbreviation")
        if team.get("id") is not None and tri:
            mapping[team.get("id")] = tri
    return mapping


def _nhl_plays(data: dict, matchup: dict) -> list[dict]:
    teams = _team_id_map(data, matchup)
    out = []
    for index, play in enumerate(data.get("plays") or []):
        details = play.get("details") or {}
        period = (play.get("periodDescriptor") or {}).get("number") or play.get("period")
        team_id = details.get("eventOwnerTeamId") or details.get("scoringPlayerId")
        event_type = (play.get("typeDescKey") or play.get("typeCode") or "event").replace("-", " ")
        desc = details.get("descKey") or event_type.title()
        out.append({
            "id": str(play.get("eventId") or index),
            "eventId": str(play.get("eventId") or index),
            "sequence": index,
            "clock": _clock(play),
            "quarter": period or "",
            "team": teams.get(team_id),
            "event": str(desc),
            "awayScore": _safe_int(details.get("awayScore")),
            "homeScore": _safe_int(details.get("homeScore")),
        })
    return out


def _three_stars(data: dict) -> list[dict]:
    stars = data.get("summary", {}).get("threeStars") or data.get("threeStars") or []
    out = []
    for row in stars[:3]:
        team = row.get("teamAbbrev") or row.get("team")
        if isinstance(team, dict):
            team = team.get("default")
        out.append({
            "name": _player_name(row),
            "team": team or "",
            "position": row.get("positionCode") or row.get("position"),
        })
    return out

# --- Team identifiers -------------------------------------------------------

# Spot-checked against a.espncdn.com/i/teamlogos/nhl/500 — validate each URL
# before shipping. Re-run when a team rebrands.
ESPN_SLUG_OVERRIDES = {
    "TBL": "tb",
    "LAK": "la",
    "SJS": "sj",
}

NHL_TRI_TO_FULL_NAME = {
    "ANA": "Anaheim Ducks",        "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres",       "CAR": "Carolina Hurricanes",
    "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks",   "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars",         "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers",      "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings",    "MIN": "Minnesota Wild",
    "MTL": "Montréal Canadiens",   "NJD": "New Jersey Devils",
    "NSH": "Nashville Predators",  "NYI": "New York Islanders",
    "NYR": "New York Rangers",     "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers",  "PIT": "Pittsburgh Penguins",
    "SEA": "Seattle Kraken",       "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues",      "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs",  "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks",    "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets",        "WSH": "Washington Capitals",
}

NHL_FULL_NAME_TO_TRI = {v: k for k, v in NHL_TRI_TO_FULL_NAME.items()}

# SGO uses UPPER_SNAKE_CASE_NHL format (verified against a live response 2026-05-14)
SGO_TEAM_ID_TO_TRI = {
    "ANAHEIM_DUCKS_NHL": "ANA",         "BOSTON_BRUINS_NHL": "BOS",
    "BUFFALO_SABRES_NHL": "BUF",        "CAROLINA_HURRICANES_NHL": "CAR",
    "COLUMBUS_BLUE_JACKETS_NHL": "CBJ", "CALGARY_FLAMES_NHL": "CGY",
    "CHICAGO_BLACKHAWKS_NHL": "CHI",    "COLORADO_AVALANCHE_NHL": "COL",
    "DALLAS_STARS_NHL": "DAL",          "DETROIT_RED_WINGS_NHL": "DET",
    "EDMONTON_OILERS_NHL": "EDM",       "FLORIDA_PANTHERS_NHL": "FLA",
    "LOS_ANGELES_KINGS_NHL": "LAK",     "MINNESOTA_WILD_NHL": "MIN",
    "MONTREAL_CANADIENS_NHL": "MTL",    "NEW_JERSEY_DEVILS_NHL": "NJD",
    "NASHVILLE_PREDATORS_NHL": "NSH",   "NEW_YORK_ISLANDERS_NHL": "NYI",
    "NEW_YORK_RANGERS_NHL": "NYR",      "OTTAWA_SENATORS_NHL": "OTT",
    "PHILADELPHIA_FLYERS_NHL": "PHI",   "PITTSBURGH_PENGUINS_NHL": "PIT",
    "SEATTLE_KRAKEN_NHL": "SEA",        "SAN_JOSE_SHARKS_NHL": "SJS",
    "ST_LOUIS_BLUES_NHL": "STL",        "TAMPA_BAY_LIGHTNING_NHL": "TBL",
    "TORONTO_MAPLE_LEAFS_NHL": "TOR",   "UTAH_MAMMOTH_NHL": "UTA",
    "VANCOUVER_CANUCKS_NHL": "VAN",     "VEGAS_GOLDEN_KNIGHTS_NHL": "VGK",
    "WINNIPEG_JETS_NHL": "WPG",         "WASHINGTON_CAPITALS_NHL": "WSH",
}


def espn_slug(tri: str) -> str:
    return ESPN_SLUG_OVERRIDES.get(tri, tri.lower())


def get_tricode_from_sgo_team_id(team_id: str) -> str:
    return SGO_TEAM_ID_TO_TRI.get(team_id.upper(), "")


# --- Game state composer ----------------------------------------------------

def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def _final_str(period_type: str) -> str:
    if period_type == "OT":
        return "F/OT"
    if period_type == "SO":
        return "F/SO"
    return "Final"


def compose_status(g: dict) -> tuple[int, str, int | None, str | None]:
    """Map an api-web.nhle.com game object to (gameStatus, statusText, period, gameClock).

    See nhl_dashboard.md "Game state → card status" for the full table.
    """
    state = g.get("gameState", "")
    sched = g.get("gameScheduleState", "OK")
    pd = g.get("periodDescriptor") or {}
    period_type = pd.get("periodType", "REG")
    period_num = pd.get("number")
    clock = g.get("clock") or {}
    time_left = clock.get("timeRemaining")

    if state in ("FUT", "PRE"):
        return 1, format_et_time(g.get("startTimeUTC", "")), None, None

    if state == "LIVE":
        if sched == "INT":
            return 2, "INT", period_num, None
        if period_type == "SO":
            return 2, "SO", 5, None
        if period_type == "OT":
            label = f"OT {time_left}" if time_left else "OT"
            return 2, label, 4, time_left
        # Regulation
        if period_num and time_left:
            return 2, f"{_ordinal(period_num)} {time_left}", period_num, time_left
        return 2, "LIVE", period_num, time_left

    if state in ("FINAL", "OFF"):
        return 3, _final_str(period_type), period_num, None

    return 1, "TBD", None, None


# --- Fetchers ---------------------------------------------------------------

async def get_standings_map() -> dict:
    """Returns {tricode: {record, winPct, points, gamesPlayed, conference, division, seed, streak, last10}}."""
    try:
        client = get_http_client()
        r = await client.get("https://api-web.nhle.com/v1/standings/now", follow_redirects=True)
        r.raise_for_status()
        data = r.json()
        out = {}
        for t in data.get("standings", []):
            tri = (t.get("teamAbbrev") or {}).get("default")
            if not tri:
                continue
            wins = t.get("wins", 0)
            losses = t.get("losses", 0)
            ot = t.get("otLosses", 0)
            gp = t.get("gamesPlayed", 0)
            pts = t.get("points", 0)
            l10w = t.get("l10Wins", 0)
            l10l = t.get("l10Losses", 0)
            l10ot = t.get("l10OtLosses", 0)

            sc = t.get("streakCode")
            scount = t.get("streakCount")
            streak = f"{sc}{scount}" if sc and scount is not None else "N/A"

            out[tri] = {
                "record": f"{wins}-{losses}-{ot}",
                "winPct": (pts / (gp * 2)) if gp > 0 else 0.5,
                "conference": t.get("conferenceAbbrev", "N/A"),
                "division": t.get("divisionAbbrev", "N/A"),
                "seed": t.get("divisionSequence", 0),
                "streak": streak,
                "last10": f"{l10w}-{l10l}-{l10ot}",
            }
        logger.info(f"NHL standings: {len(out)} teams")
        return out
    except Exception as e:
        logger.error(f"NHL standings fetch error: {e}", exc_info=True)
        return {}


async def get_team_summary_map() -> dict:
    """Returns {tricode: {goalsFor, goalsAgainst, powerPlayPct, penaltyKillPct, savesPct, shotsForPerGame}}.

    Missing source fields surface as None. PP%/PK% scaled from 0–1 → 0–100.
    """
    season = os.getenv("NHL_SEASON_ID", "20252026")
    url = f"https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId={season}"
    try:
        client = get_http_client()
        r = await client.get(url)
        r.raise_for_status()
        rows = r.json().get("data", [])
        out = {}
        for row in rows:
            name = row.get("teamFullName", "")
            tri = NHL_FULL_NAME_TO_TRI.get(name)
            if not tri:
                logger.warning(f"NHL team-summary row with unmapped name: {name!r}")
                continue

            gf = to_num(row.get("goalsForPerGame"))
            ga = to_num(row.get("goalsAgainstPerGame"))
            pp = to_num(row.get("powerPlayPct"))
            pk = to_num(row.get("penaltyKillPct"))
            sapg = to_num(row.get("shotsAgainstPerGame"))
            sfpg = to_num(row.get("shotsForPerGame"))

            if (
                sapg is not None
                and sapg > 0
                and ga is not None
            ):
                saves_pct = 1 - (ga / sapg)
            else:
                saves_pct = None

            out[tri] = {
                "goalsFor": gf,
                "goalsAgainst": ga,
                "powerPlayPct": pp * 100 if pp is not None else None,
                "penaltyKillPct": pk * 100 if pk is not None else None,
                "savesPct": saves_pct,
                "shotsForPerGame": sfpg,
            }
        logger.info(f"NHL team summary: {len(out)} teams (season {season})")
        return out
    except Exception as e:
        logger.error(f"NHL team-summary fetch error: {e}", exc_info=True)
        return {}


async def get_scoreboard_data(target_date: date | None = None) -> dict:
    """Fetches NHL slate from api-web.nhle.com. Defaults to today's slate."""
    try:
        client = get_http_client()
        path = "score/now" if target_date is None else f"score/{target_date.isoformat()}"
        r = await client.get(f"https://api-web.nhle.com/v1/{path}", follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"NHL scoreboard fetch error: {e}", exc_info=True)
        return {}


async def get_next_nhl_scoreboard(start_date: date, lookahead_days: int = NHL_LOOKAHEAD_DAYS) -> tuple[dict, date]:
    """Return today's NHL scoreboard if populated, otherwise the next slate with games."""
    for offset in range(0, lookahead_days + 1):
        target = start_date + timedelta(days=offset)
        sb = await get_scoreboard_data(None if offset == 0 else target)
        games = [g for g in sb.get("games", []) if g.get("gameType") in (2, 3)]
        if games:
            logger.info(f"NHL lookahead selected {target.isoformat()} with {len(games)} games")
            return sb, target
    return {}, start_date


# --- Odds (SGO) -------------------------------------------------------------

async def get_odds_map(target_date: date | None = None):
    """Fetch NHL odds from SportsGameOdds, merged with persisted rows."""
    return await fetch_sgo_odds(
        sport="nhl",
        league_id="NHL",
        table_name="nhl_odds",
        tricode_for=get_tricode_from_sgo_team_id,
        target_date=target_date,
    )

# --- Endpoints --------------------------------------------------------------

@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds(offset: CronOffset = 0):
    """Daily odds prefetch. offset=0 fetches today (ET), offset=1 fetches tomorrow."""
    try:
        target = datetime.now(ET).date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(target_date=target)
        cache.set("odds", odds)
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:nhl_odds",
            "date": target.isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _project_score(
    away_gf, away_ga, home_gf, home_ga,
    market_total, market_spread_home_perspective,
) -> tuple[int, int, int | None]:
    """Naive GF/GA blend + 70% market / 30% naive lean. Returns (away, home, projected_total)."""
    if None in (away_gf, away_ga, home_gf, home_ga):
        return 0, 0, None

    naive_away = (away_gf + home_ga) / 2
    naive_home = (home_gf + away_ga) / 2
    naive_total = naive_away + naive_home
    naive_margin = naive_home - naive_away  # positive = home favored

    if market_spread_home_perspective is not None:
        market_margin = -market_spread_home_perspective  # positive = home favored
        blended_margin = naive_margin * 0.3 + market_margin * 0.7
    else:
        blended_margin = naive_margin

    if market_total is not None:
        blended_total = market_total * 0.7 + naive_total * 0.3
    else:
        blended_total = naive_total

    projected_away = round(max(0, (blended_total - blended_margin) / 2))
    projected_home = round(max(0, (blended_total + blended_margin) / 2))
    return projected_away, projected_home, round(blended_total)


@router.get("/today")
async def get_todays_games():
    cached_response = cache.get("assembled")
    if cached_response:
        return cached_response

    try:
        # Fetch only what's stale
        cached_scoreboard = cache.get("scoreboard")
        cached_standings = cache.get("standings")
        cached_summary = cache.get("team_summary")
        cached_odds = cache.get("odds")

        fetch_keys = []
        coros = []

        today_et = slate_date_et()
        slate_date = today_et

        if not cached_scoreboard:
            coros.append(get_next_nhl_scoreboard(today_et))
            fetch_keys.append("scoreboard")
        if not cached_standings:
            coros.append(get_standings_map())
            fetch_keys.append("standings")
        if not cached_summary:
            coros.append(get_team_summary_map())
            fetch_keys.append("team_summary")
        if not cached_odds:
            coros.append(asyncio.to_thread(load_persisted_odds, table_name="nhl_odds"))
            fetch_keys.append("odds")

        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch NHL {key}: {result}, using cached data")
                else:
                    if key == "scoreboard" and isinstance(result, tuple):
                        scoreboard_data, selected_date = result
                        slate_date = selected_date
                        cache.set(key, scoreboard_data)
                    else:
                        cache.set(key, result)

        sb = cache.get("scoreboard") or cache.get_stale("scoreboard") or {}
        standings_map = cache.get("standings") or cache.get_stale("standings") or {}
        summary_map = cache.get("team_summary") or cache.get_stale("team_summary") or {}
        odds_map = cache.get("odds") or cache.get_stale("odds") or {}
        if slate_date > today_et + timedelta(days=1):
            odds_map.update(load_persisted_odds(slate_date, table_name="nhl_odds"))

        games = sb.get("games", [])
        # Filter to regular season (2) + playoffs (3). Skip preseason (1) and others.
        games = [g for g in games if g.get("gameType") in (2, 3)]
        logger.info(f"NHL: processing {len(games)} games (standings={len(standings_map)}, summary={len(summary_map)}, odds={len(odds_map)})")

        refined = []
        for g in games:
            away_team = g.get("awayTeam", {})
            home_team = g.get("homeTeam", {})
            away_tri = away_team.get("abbrev", "")
            home_tri = home_team.get("abbrev", "")
            if not away_tri or not home_tri:
                continue

            matchup_key = f"{away_tri}@{home_tri}"
            game_date_str = (g.get("gameDate") or "")[:10]
            game_odds = odds_map.get((game_date_str, matchup_key), {})

            game_status, status_text, period, game_clock = compose_status(g)

            # Spread line for display
            away_spread = game_odds.get("awaySpread")
            home_spread = game_odds.get("homeSpread")
            if away_spread is not None and home_spread is not None:
                if away_spread < 0:
                    spread_line = f"{away_tri} {away_spread}"
                    favorite = "away"
                    spread_value = away_spread
                else:
                    spread_line = f"{home_tri} {home_spread}"
                    favorite = "home"
                    spread_value = home_spread
            else:
                spread_line = "N/A"
                favorite = None
                spread_value = None

            # Final-game grading
            spread_result = None
            total_result = None
            over_under = game_odds.get("overUnder")
            if game_status == 3:
                away_score = away_team.get("score", 0) or 0
                home_score = home_team.get("score", 0) or 0
                if spread_value is not None:
                    margin = away_score - home_score
                    if favorite == "away":
                        spread_result = margin > abs(spread_value)
                    else:
                        spread_result = (-margin) > abs(spread_value)
                if over_under is not None:
                    total = away_score + home_score
                    total_result = "over" if total > over_under else "under" if total < over_under else "push"

            away_std = standings_map.get(away_tri, {})
            home_std = standings_map.get(home_tri, {})
            away_sum = summary_map.get(away_tri, {})
            home_sum = summary_map.get(home_tri, {})

            market_total = game_odds.get("overUnder")
            market_spread_home_perspective = game_odds.get("homeSpread")
            projected_away, projected_home, projected_total = _project_score(
                away_sum.get("goalsFor"), away_sum.get("goalsAgainst"),
                home_sum.get("goalsFor"), home_sum.get("goalsAgainst"),
                market_total, market_spread_home_perspective,
            )

            def team_payload(tri: str, team_obj: dict, std: dict, summary: dict, projected: int) -> dict:
                return {
                    "name": (team_obj.get("name") or {}).get("default") or NHL_TRI_TO_FULL_NAME.get(tri, tri),
                    "abbreviation": tri,
                    "espnSlug": espn_slug(tri),
                    "record": std.get("record", "0-0-0"),
                    "winPct": std.get("winPct", 0.5),
                    "seed": std.get("seed", 0),
                    "conference": std.get("conference", "N/A"),
                    "streak": std.get("streak", "N/A"),
                    "last10": std.get("last10", "0-0-0"),
                    "score": team_obj.get("score", 0) or 0,
                    "predictedScore": projected,
                    "goalsFor": summary.get("goalsFor"),
                    "goalsAgainst": summary.get("goalsAgainst"),
                    "powerPlayPct": summary.get("powerPlayPct"),
                    "penaltyKillPct": summary.get("penaltyKillPct"),
                    "savesPct": summary.get("savesPct"),
                }

            away_payload = team_payload(away_tri, away_team, away_std, away_sum, projected_away)
            home_payload = team_payload(home_tri, home_team, home_std, home_sum, projected_home)
            away_payload["winProbability"] = game_odds.get("awayWinProbability")
            home_payload["winProbability"] = game_odds.get("homeWinProbability")

            refined.append({
                "sport": "nhl",
                "gameId": str(g.get("id", "")),
                "status": status_text,
                "gameStatus": game_status,
                "period": period,
                "gameClock": game_clock,
                "time": format_et_time(g.get("startTimeUTC", "")),
                "date": game_date_str,
                "venue": (g.get("venue") or {}).get("default", "TBD"),
                "broadcast": "Local",
                "model": "xG v5.0",
                "spreadLine": spread_line,
                "spreadResult": spread_result,
                "pregameSpread": game_odds.get("homeSpread"),
                "spreadOddsHome": game_odds.get("homeSpreadOdds"),
                "spreadOddsAway": game_odds.get("awaySpreadOdds"),
                "homeMoneyline": game_odds.get("homeMoneyline"),
                "awayMoneyline": game_odds.get("awayMoneyline"),
                "overUnder": over_under,
                "overOdds": game_odds.get("overOdds"),
                "underOdds": game_odds.get("underOdds"),
                "totalResult": total_result,
                "projectedTotal": projected_total,
                # Playoff context — typed in v1, populated by a later feature.
                "seriesGameNumber": None,
                "seriesStatus": None,
                "away": away_payload,
                "home": home_payload,
            })

        has_live = any(g.get("gameStatus") == 2 for g in refined)
        assembled_ttl = CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT
        if has_live:
            cache.set_ttl("scoreboard", CACHE_TTL_LIVE)
        cache.set("assembled", refined, ttl=assembled_ttl)

        logger.info(f"NHL assembled: {len(refined)} games, TTL={assembled_ttl}s, live={has_live}")
        return refined
    except Exception as e:
        logger.error(f"NHL /today error: {e}", exc_info=True)
        cached = cache.get_stale("assembled")
        if cached:
            return cached
        return []


@router.get("/game/{game_id}")
async def get_game_detail(game_id: str):
    matchups = await get_todays_games()
    matchup = next((g for g in matchups if str(g.get("gameId")) == str(game_id)), None)
    if not matchup:
        raise HTTPException(status_code=404, detail="NHL game not found in today's scoreboard")

    game_status = int(matchup.get("gameStatus", 1) or 1)
    status = _detail_status(game_status)
    raw_status = matchup.get("status") or ""
    client = get_http_client()

    async def fetch_json(path: str):
        try:
            response = await client.get(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/{path}", follow_redirects=True)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(f"NHL detail {path} failed for {game_id}: {exc}")
            return None

    box_data, pbp_data, landing_data = await asyncio.gather(
        fetch_json("boxscore"),
        fetch_json("play-by-play"),
        fetch_json("landing"),
    )

    score_source = pbp_data or landing_data or box_data or {}
    if pbp_data:
        away_score = _nhl_linescore_from_plays(pbp_data, "away")
        home_score = _nhl_linescore_from_plays(pbp_data, "home")
    else:
        away_score = _nhl_linescore(score_source.get("awayTeam") or {})
        home_score = _nhl_linescore(score_source.get("homeTeam") or {})
    if not away_score["total"] and matchup.get("away"):
        away_score["total"] = _safe_int(matchup["away"].get("score"))
    if not home_score["total"] and matchup.get("home"):
        home_score["total"] = _safe_int(matchup["home"].get("score"))

    away_box = _nhl_team_box(box_data, "away", pbp_data) if box_data else None
    home_box = _nhl_team_box(box_data, "home", pbp_data) if box_data else None
    plays = _nhl_plays(pbp_data, matchup) if pbp_data else []
    three_stars = _three_stars(landing_data or {}) if landing_data else []

    return {
        "sport": "nhl",
        "gameId": game_id,
        "matchup": matchup,
        "status": status,
        "rawStatus": raw_status,
        "awayScore": away_score,
        "homeScore": home_score,
        "awayBoxScore": away_box,
        "homeBoxScore": home_box,
        "playByPlay": plays,
        "winProbability": [],
        "threeStars": three_stars,
    }
