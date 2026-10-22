from __future__ import annotations

import asyncio
import logging
import math
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from . import espn_feeds
from .auth import CronOffset, require_cron_auth
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    Cache,
    UpstreamCache,
    fetch_sgo_odds,
    grade_spread_result,
    load_persisted_odds,
    slate_date_et,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nba", tags=["NBA"])

cache = Cache({
    "scoreboard":     CACHE_TTL_DEFAULT,
    "standings":      300,
    "advanced_stats": 600,
    "odds":           CACHE_TTL_ODDS,
    "assembled":      CACHE_TTL_DEFAULT,
})
game_feeds = UpstreamCache()

NBA_LOOKAHEAD_DAYS = 14


def _detail_status(game_status: int) -> str:
    if game_status == 2:
        return "live"
    if game_status == 3:
        return "final"
    return "preview"


def _nba_game_datetime(g: dict) -> str:
    return g.get("gameEt") or g.get("gameDateTimeEst") or g.get("gameDateEst") or ""


def _nba_game_date(g: dict) -> str:
    return (_nba_game_datetime(g).split("T")[0] if "T" in _nba_game_datetime(g) else "") or str(g.get("gameDateEst", ""))[:10]


def _nba_game_time(g: dict) -> str:
    game_et = _nba_game_datetime(g)
    if game_et and "T" in game_et:
        return game_et.split("T")[-1][:5]
    return g.get("gameStatusText", "")


def _national_broadcast(g: dict) -> str:
    broadcasters = (g.get("broadcasters") or {}).get("nationalBroadcasters") or []
    if broadcasters:
        return broadcasters[0].get("broadcasterAbbreviation") or broadcasters[0].get("broadcasterDisplay") or "National"
    return "Local"


def _num(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


def _parse_clock_seconds(game_clock: str) -> float:
    if not game_clock:
        return 0
    if game_clock.startswith("PT"):
        minutes = 0.0
        seconds = 0.0
        body = game_clock[2:]
        if "M" in body:
            minute_part, body = body.split("M", 1)
            minutes = float(minute_part or 0)
        if "S" in body:
            seconds = float(body.replace("S", "") or 0)
        return minutes * 60 + seconds
    if ":" in game_clock:
        minutes, seconds = game_clock.split(":", 1)
        return float(minutes or 0) * 60 + float(seconds or 0)
    # ESPN drops the minutes under one minute left: "45.3".
    try:
        return float(game_clock)
    except ValueError:
        return 0


def _total_time_remaining(period: int, game_clock: str) -> float:
    clock_seconds = _parse_clock_seconds(game_clock)
    if period <= 0:
        return 2880
    if period <= 4:
        return (4 - period) * 720 + clock_seconds
    return 0 if clock_seconds == 0 else clock_seconds


def _nba_win_prob(home_score: int, away_score: int, period: int, game_clock: str, pregame_spread=0, pace=100) -> float:
    score_diff = home_score - away_score
    time_remaining = _total_time_remaining(period, game_clock)
    if time_remaining == 0:
        return 1 if score_diff > 0 else 0 if score_diff < 0 else 0.5
    spread_fade = 0 if period > 4 else time_remaining / 2880
    k = 3.0 if pace > 102 else 4.0 if pace < 98 else 3.5
    adjusted_diff = score_diff + (-(pregame_spread or 0) * spread_fade)
    return 1 / (1 + math.exp(-(adjusted_diff * k) / math.sqrt(time_remaining + 1)))


def _nba_linescore(team: dict) -> dict:
    periods = team.get("periods") or []
    scores = [int(p.get("score", 0) or 0) for p in periods]
    while len(scores) < 4:
        scores.append(0)
    out = {
        "q1": scores[0],
        "q2": scores[1],
        "q3": scores[2],
        "q4": scores[3],
        "total": int(team.get("score", sum(scores)) or 0),
    }
    if len(scores) > 4:
        out["ot"] = scores[4:]
    return out


def _nba_player(row: dict) -> dict:
    stats = row.get("statistics") or row
    name = row.get("name") or row.get("nameI") or f"{row.get('firstName', '')} {row.get('familyName', '')}".strip() or "Unknown"
    return {
        "name": name,
        "position": row.get("position") or "",
        "minutes": str(stats.get("minutes") or stats.get("minutesCalculated") or "0:00"),
        "points": int(_num(stats.get("points"))),
        "rebounds": int(_num(stats.get("reboundsTotal") or stats.get("rebounds"))),
        "assists": int(_num(stats.get("assists"))),
        "steals": int(_num(stats.get("steals"))),
        "blocks": int(_num(stats.get("blocks"))),
        "turnovers": int(_num(stats.get("turnovers"))),
        "fouls": int(_num(stats.get("foulsPersonal") or stats.get("personalFouls"))),
        "fgm": int(_num(stats.get("fieldGoalsMade"))),
        "fga": int(_num(stats.get("fieldGoalsAttempted"))),
        "threePm": int(_num(stats.get("threePointersMade"))),
        "threePa": int(_num(stats.get("threePointersAttempted"))),
        "ftm": int(_num(stats.get("freeThrowsMade"))),
        "fta": int(_num(stats.get("freeThrowsAttempted"))),
        "plusMinus": int(_num(stats.get("plusMinusPoints") or stats.get("plusMinus"))),
        "dnp": row.get("dnp"),
    }


def _pct(made: int, attempts: int) -> float | None:
    return round((made / attempts) * 100, 1) if attempts else None


def _nba_team_box(team: dict) -> dict:
    players = [_nba_player(p) for p in team.get("players", [])]
    totals = {
        "points": sum(p["points"] for p in players),
        "rebounds": sum(p["rebounds"] for p in players),
        "assists": sum(p["assists"] for p in players),
        "steals": sum(p["steals"] for p in players),
        "blocks": sum(p["blocks"] for p in players),
        "turnovers": sum(p["turnovers"] for p in players),
        "fgPct": _pct(sum(p["fgm"] for p in players), sum(p["fga"] for p in players)),
        "threePct": _pct(sum(p["threePm"] for p in players), sum(p["threePa"] for p in players)),
        "ftPct": _pct(sum(p["ftm"] for p in players), sum(p["fta"] for p in players)),
    }
    return {"players": players, "totals": totals}


def _nba_efficiency(box: dict | None) -> dict:
    if not box:
        return {"ppp": None, "tsPct": None, "possessions": None}
    players = box.get("players", [])
    pts = sum(p["points"] for p in players)
    fga = sum(p["fga"] for p in players)
    fta = sum(p["fta"] for p in players)
    orb = 0
    tov = sum(p["turnovers"] for p in players)
    possessions = fga + 0.44 * fta - orb + tov
    ts = (pts / (2 * (fga + 0.44 * fta)) * 100) if (fga + 0.44 * fta) else None
    return {
        "ppp": round(pts / possessions, 3) if possessions else None,
        "tsPct": round(ts, 1) if ts is not None else None,
        "possessions": round(possessions, 1) if possessions else None,
    }


def _nba_play_description(action: dict) -> str:
    for key in ("description", "actionType", "subType"):
        if action.get(key):
            return str(action[key])
    return "Game event"


def _nba_pbp(
    actions: list[dict], matchup: dict, home_prob_by_action: dict[str, float] | None = None
) -> tuple[list[dict], list[dict]]:
    """Plays and the win-probability series.

    With home_prob_by_action (ESPN's per-play probabilities), those are used,
    carrying the last one forward over any play without a point. The local
    model is the fallback for games ESPN publishes no probabilities for.
    """
    pregame_spread = matchup.get("pregameSpread") or 0
    pace = ((matchup.get("away") or {}).get("pace", 100) + (matchup.get("home") or {}).get("pace", 100)) / 2
    plays = []
    win_probability = []
    last_score = None
    last_published_prob = None

    for index, action in enumerate(actions):
        period = int(_num(action.get("period"), 0))
        clock = str(action.get("clock") or "")
        away_score = int(_num(action.get("scoreAway")))
        home_score = int(_num(action.get("scoreHome")))
        if home_prob_by_action:
            last_published_prob = home_prob_by_action.get(str(action.get("actionNumber")), last_published_prob)
        if last_published_prob is not None:
            home_prob = last_published_prob
        else:
            home_prob = _nba_win_prob(home_score, away_score, period, clock, pregame_spread, pace)
        away_prob = 1 - home_prob
        elapsed = 2880 - _total_time_remaining(period, clock) if period <= 4 else 2880
        x = min(100, max(0, elapsed / 2880 * 100))

        play = {
            "id": str(action.get("actionNumber") or index),
            "eventId": str(action.get("actionNumber") or index),
            "sequence": index,
            "clock": clock,
            "quarter": period,
            "team": action.get("teamTricode"),
            "event": _nba_play_description(action),
            "awayScore": away_score,
            "homeScore": home_score,
            "winProbAway": round(away_prob * 100, 1),
            "winProbHome": round(home_prob * 100, 1),
            "sourceTimestamp": action.get("timeActual"),
        }
        plays.append(play)

        score_key = (away_score, home_score, period, clock)
        if score_key != last_score or action.get("actionType") == "period":
            win_probability.append({
                "x": round(x, 1),
                "awayProb": round(away_prob * 100, 1),
                "homeProb": round(home_prob * 100, 1),
                "label": f"Q{period} {clock}" if period else None,
            })
            last_score = score_key

    return plays, win_probability


# Game detail comes from ESPN's summary, adapted into the nba_api live shapes the
# helpers above read. cdn.nba.com 403s every request (nba_api issue #678), and
# the slate already carries ESPN event ids.

def _espn_made_attempted(value) -> tuple[int, int]:
    made, _, attempted = str(value or "").partition("-")
    return int(_num(made)), int(_num(attempted))


def _espn_linescore(competitor: dict) -> dict:
    return _nba_linescore({
        "periods": [{"score": p.get("displayValue")} for p in competitor.get("linescores") or []],
        "score": competitor.get("score"),
    })


def _espn_title(text: str) -> str:
    # str.title() would give "Coach'S Decision".
    return " ".join(word.capitalize() for word in text.split())


def _espn_injury_label(details: dict) -> str:
    """"Left Ankle, Sprain" from ESPN's structured injury details."""
    side = details.get("side") if details.get("side") not in (None, "", "Not Specified") else ""
    body = " ".join(part for part in (side, details.get("type") or "") if part) or "Injury"
    return f"{body}, {details['detail']}" if details.get("detail") else body


def _espn_ruled_out(team_injuries: dict) -> dict[str, dict]:
    """Players the injury report has Out, keyed by athlete id, with their DNP label."""
    out = {}
    for injury in team_injuries.get("injuries") or []:
        athlete = injury.get("athlete") or {}
        if (injury.get("type") or {}).get("name") == "INJURY_STATUS_OUT" and athlete.get("id"):
            out[str(athlete["id"])] = {
                "athlete": athlete,
                "dnp": _espn_injury_label(injury.get("details") or {}),
            }
    return out


def _espn_statless_row(athlete: dict, dnp: str | None) -> dict:
    """A player with no stats: a DNP when dnp is set, otherwise a row of zeros."""
    return {
        "name": athlete.get("displayName") or "Unknown",
        "position": (athlete.get("position") or {}).get("abbreviation") or "",
        "statistics": {"minutes": "0"},
        "dnp": dnp,
    }


def _espn_box_team(player_box: dict, ruled_out: dict[str, dict], final: bool) -> dict:
    """One boxscore.players entry as an nba_api box score team.

    ESPN marks every bench player who hasn't checked in as a Coach's Decision
    DNP while the game is live, injured or not, and leaves most injured players
    out of the box entirely. So players the injury report has Out are DNPs from
    tip-off, with their injury; anyone else without stats is a row of zeros
    until the final, when ESPN's reason stands. DNPs sort to the bottom.
    """
    players = []
    dnps = []
    seen_ids = set()
    for group in player_box.get("statistics") or []:
        keys = group.get("keys") or []
        for row in group.get("athletes") or []:
            athlete = row.get("athlete") or {}
            athlete_id = str(athlete.get("id") or "")
            seen_ids.add(athlete_id)
            if not row.get("stats"):
                if athlete_id in ruled_out:
                    dnps.append(_espn_statless_row(athlete, ruled_out[athlete_id]["dnp"]))
                elif final:
                    dnps.append(_espn_statless_row(athlete, _espn_title(row.get("reason") or "Coach's Decision")))
                else:
                    players.append(_espn_statless_row(athlete, None))
                continue
            stats = dict(zip(keys, row["stats"]))
            fgm, fga = _espn_made_attempted(stats.get("fieldGoalsMade-fieldGoalsAttempted"))
            tpm, tpa = _espn_made_attempted(stats.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted"))
            ftm, fta = _espn_made_attempted(stats.get("freeThrowsMade-freeThrowsAttempted"))
            players.append({
                "name": athlete.get("displayName") or "Unknown",
                "position": (athlete.get("position") or {}).get("abbreviation") or "",
                "statistics": {
                    "minutes": stats.get("minutes") or "0",
                    "points": stats.get("points"),
                    "reboundsTotal": stats.get("rebounds"),
                    "assists": stats.get("assists"),
                    "steals": stats.get("steals"),
                    "blocks": stats.get("blocks"),
                    "turnovers": stats.get("turnovers"),
                    "foulsPersonal": stats.get("fouls"),
                    "fieldGoalsMade": fgm,
                    "fieldGoalsAttempted": fga,
                    "threePointersMade": tpm,
                    "threePointersAttempted": tpa,
                    "freeThrowsMade": ftm,
                    "freeThrowsAttempted": fta,
                    "plusMinusPoints": stats.get("plusMinus"),
                },
            })
    dnps.extend(
        _espn_statless_row(entry["athlete"], entry["dnp"])
        for athlete_id, entry in ruled_out.items()
        if athlete_id not in seen_ids
    )
    return {"players": players + dnps}


def _espn_home_prob_by_play(points: list[dict]) -> dict[str, float]:
    """ESPN's winprobability points keyed by playId, dropping malformed values."""
    probs = {}
    for point in points:
        try:
            home_prob = float(point["homeWinPercentage"])
        except (KeyError, TypeError, ValueError):
            continue
        if point.get("playId") and 0 <= home_prob <= 1:
            probs[str(point["playId"])] = home_prob
    return probs


def _espn_actions(plays: list[dict], tricode_by_team_id: dict[str, str]) -> list[dict]:
    return [
        {
            "actionNumber": play.get("id") or play.get("sequenceNumber"),
            "period": (play.get("period") or {}).get("number"),
            "clock": (play.get("clock") or {}).get("displayValue") or "",
            "scoreAway": play.get("awayScore"),
            "scoreHome": play.get("homeScore"),
            "teamTricode": tricode_by_team_id.get(str((play.get("team") or {}).get("id"))),
            "description": play.get("text"),
            "actionType": (play.get("type") or {}).get("text"),
            "timeActual": play.get("wallclock"),
        }
        for play in plays
    ]

# Get the standings map
# ESPN's scoreboard/stats abbreviations are not NBA tricodes. Anything missing
# here passes through unchanged and silently fails the standings and efficiency
# joins, which is how the WNBA lost five teams' odds for weeks.
_ESPN_TO_NBA_TRICODE = {
    "GS": "GSW",    # Golden State Warriors
    "NO": "NOP",    # New Orleans Pelicans
    "NY": "NYK",    # New York Knicks
    "SA": "SAS",    # San Antonio Spurs
    "UTAH": "UTA",  # Utah Jazz
    "WSH": "WAS",   # Washington Wizards
}

NBA_TRICODES = {
    "ATL", "BOS", "BKN", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GSW",
    "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NOP", "NYK",
    "OKC", "ORL", "PHI", "PHX", "POR", "SAC", "SAS", "TOR", "UTA", "WAS",
}

_warned_espn_abbreviations: set[str] = set()


def _canonical_tricode(abbreviation: str) -> str:
    """Translate an ESPN abbreviation to an NBA tricode, warning when we cannot."""
    tricode = _ESPN_TO_NBA_TRICODE.get(abbreviation, abbreviation)
    if tricode not in NBA_TRICODES and abbreviation not in _warned_espn_abbreviations:
        _warned_espn_abbreviations.add(abbreviation)
        logger.warning(
            "ESPN NBA abbreviation %r maps to %r, which is not a known tricode — "
            "standings and ratings will not join until _ESPN_TO_NBA_TRICODE is updated",
            abbreviation,
            tricode,
        )
    return tricode


def get_standings_map():
    """NBA standings from ESPN, keyed by tricode and full team name.

    Replaces LeagueStandingsV3: stats.nba.com tarpits datacenter IPs, so from
    Render that call never returned and every team silently fell back to
    placeholder values.
    """
    try:
        return espn_feeds.fetch_standings(
            sport="nba",
            canonical_tricode=_canonical_tricode,
        )
    except Exception as e:
        logger.error(f"Standings Fetch Error: {e}", exc_info=True)
        return {}


def get_advanced_stats_map():
    """NBA ORtg/DRtg/pace derived from ESPN season totals.

    pace is possessions per game; NBA regulation is 48 minutes, so this is
    already on the same scale the per-48 PACE stat used to report.
    """
    try:
        return espn_feeds.fetch_efficiency(
            sport="nba",
            canonical_tricode=_canonical_tricode,
        )
    except Exception as e:
        logger.error(f"Advanced Stats Fetch Error: {e}", exc_info=True)
        return {}


def get_arena_map():
    """Static arena map by team tricode. Arenas rarely change."""
    return {
        "ATL": "State Farm Arena",
        "BOS": "TD Garden",
        "BKN": "Barclays Center",
        "CHA": "Spectrum Center",
        "CHI": "United Center",
        "CLE": "Rocket Mortgage FieldHouse",
        "DAL": "American Airlines Center",
        "DEN": "Ball Arena",
        "DET": "Little Caesars Arena",
        "GSW": "Chase Center",
        "HOU": "Toyota Center",
        "IND": "Gainbridge Fieldhouse",
        "LAC": "Intuit Dome",
        "LAL": "Crypto.com Arena",
        "MEM": "FedExForum",
        "MIA": "Kaseya Center",
        "MIL": "Fiserv Forum",
        "MIN": "Target Center",
        "NOP": "Smoothie King Center",
        "NYK": "Madison Square Garden",
        "OKC": "Paycom Center",
        "ORL": "Kia Center",
        "PHI": "Wells Fargo Center",
        "PHX": "Footprint Center",
        "POR": "Moda Center",
        "SAC": "Golden 1 Center",
        "SAS": "Frost Bank Center",
        "TOR": "Scotiabank Arena",
        "UTA": "Delta Center",
        "WAS": "Capital One Arena",
    }


def get_nba_slate(start_date: date, lookahead_days: int = NBA_LOOKAHEAD_DAYS) -> tuple[list[dict], date | None]:
    """The next NBA date with games and its games, from ESPN.

    ESPN is the only slate source: cdn.nba.com 403s nba_api's live scoreboard
    (nba_api issue #678), and stats.nba.com tarpits datacenter IPs. Raises on
    failure so /today keeps the last good slate rather than caching an empty one.
    """
    return espn_feeds.fetch_next_slate(
        sport="nba",
        start_date=start_date,
        lookahead_days=lookahead_days,
        canonical_tricode=_canonical_tricode,
        timezone=ET,
    )


def get_tricode_from_sgo_team_id(team_id: str) -> str:
    """Map SportsGameOdds teamID to NBA tricode.

    SGO team IDs appear to follow an UPPER_SNAKE_CASE_NBA pattern based on
    player ID conventions (e.g. LEBRON_JAMES_1_NBA). If the format differs
    from what's mapped here, warnings will appear in logs on first run.
    """
    sgo_map = {
        # With _NBA suffix (expected primary format)
        "ATLANTA_HAWKS_NBA": "ATL", "BOSTON_CELTICS_NBA": "BOS",
        "BROOKLYN_NETS_NBA": "BKN", "CHARLOTTE_HORNETS_NBA": "CHA",
        "CHICAGO_BULLS_NBA": "CHI", "CLEVELAND_CAVALIERS_NBA": "CLE",
        "DALLAS_MAVERICKS_NBA": "DAL", "DENVER_NUGGETS_NBA": "DEN",
        "DETROIT_PISTONS_NBA": "DET", "GOLDEN_STATE_WARRIORS_NBA": "GSW",
        "HOUSTON_ROCKETS_NBA": "HOU", "INDIANA_PACERS_NBA": "IND",
        "LOS_ANGELES_CLIPPERS_NBA": "LAC", "LA_CLIPPERS_NBA": "LAC", "LOS_ANGELES_LAKERS_NBA": "LAL",
        "MEMPHIS_GRIZZLIES_NBA": "MEM", "MIAMI_HEAT_NBA": "MIA",
        "MILWAUKEE_BUCKS_NBA": "MIL", "MINNESOTA_TIMBERWOLVES_NBA": "MIN",
        "NEW_ORLEANS_PELICANS_NBA": "NOP", "NEW_YORK_KNICKS_NBA": "NYK",
        "OKLAHOMA_CITY_THUNDER_NBA": "OKC", "ORLANDO_MAGIC_NBA": "ORL",
        "PHILADELPHIA_76ERS_NBA": "PHI", "PHOENIX_SUNS_NBA": "PHX",
        "PORTLAND_TRAIL_BLAZERS_NBA": "POR", "SACRAMENTO_KINGS_NBA": "SAC",
        "SAN_ANTONIO_SPURS_NBA": "SAS", "TORONTO_RAPTORS_NBA": "TOR",
        "UTAH_JAZZ_NBA": "UTA", "WASHINGTON_WIZARDS_NBA": "WAS",
        # Without suffix (fallback)
        "ATLANTA_HAWKS": "ATL", "BOSTON_CELTICS": "BOS",
        "BROOKLYN_NETS": "BKN", "CHARLOTTE_HORNETS": "CHA",
        "CHICAGO_BULLS": "CHI", "CLEVELAND_CAVALIERS": "CLE",
        "DALLAS_MAVERICKS": "DAL", "DENVER_NUGGETS": "DEN",
        "DETROIT_PISTONS": "DET", "GOLDEN_STATE_WARRIORS": "GSW",
        "HOUSTON_ROCKETS": "HOU", "INDIANA_PACERS": "IND",
        "LOS_ANGELES_CLIPPERS": "LAC", "LA_CLIPPERS": "LAC", "LOS_ANGELES_LAKERS": "LAL",
        "MEMPHIS_GRIZZLIES": "MEM", "MIAMI_HEAT": "MIA",
        "MILWAUKEE_BUCKS": "MIL", "MINNESOTA_TIMBERWOLVES": "MIN",
        "NEW_ORLEANS_PELICANS": "NOP", "NEW_YORK_KNICKS": "NYK",
        "OKLAHOMA_CITY_THUNDER": "OKC", "ORLANDO_MAGIC": "ORL",
        "PHILADELPHIA_76ERS": "PHI", "PHOENIX_SUNS": "PHX",
        "PORTLAND_TRAIL_BLAZERS": "POR", "SACRAMENTO_KINGS": "SAC",
        "SAN_ANTONIO_SPURS": "SAS", "TORONTO_RAPTORS": "TOR",
        "UTAH_JAZZ": "UTA", "WASHINGTON_WIZARDS": "WAS",
        # Short codes (if SGO uses tricodes directly)
        "ATL": "ATL", "BOS": "BOS", "BKN": "BKN", "CHA": "CHA",
        "CHI": "CHI", "CLE": "CLE", "DAL": "DAL", "DEN": "DEN",
        "DET": "DET", "GSW": "GSW", "HOU": "HOU", "IND": "IND",
        "LAC": "LAC", "LAL": "LAL", "MEM": "MEM", "MIA": "MIA",
        "MIL": "MIL", "MIN": "MIN", "NOP": "NOP", "NYK": "NYK",
        "OKC": "OKC", "ORL": "ORL", "PHI": "PHI", "PHX": "PHX",
        "POR": "POR", "SAC": "SAC", "SAS": "SAS", "TOR": "TOR",
        "UTA": "UTA", "WAS": "WAS",
    }
    return sgo_map.get(team_id.upper(), "")


def get_tricode_from_team_name(team_name: str) -> str:
    key = " ".join((team_name or "").split())
    return get_tricode_from_sgo_team_id("_".join(key.split())) or {
        "PHO Suns": "PHX",
    }.get(key, "")

async def get_odds_map(
    target_date: date | None = None,
    *,
    request_time: datetime | None = None,
):
    """Fetch NBA odds from SportsGameOdds, merged with persisted rows."""
    return await fetch_sgo_odds(
        sport="nba",
        league_id="NBA",
        table_name="nba_odds",
        tricode_for=get_tricode_from_sgo_team_id,
        fallback_tricode_for=get_tricode_from_team_name,
        target_date=target_date,
        request_time=request_time,
    )

@router.get("/debug/standings")
def debug_standings():
    """Debug endpoint to test standings API directly"""
    try:
        result = get_standings_map()
        return {
            "success": True,
            "team_count": len(result),
            "sample_teams": list(result.keys())[:10] if result else [],
            "sample_data": {k: result[k] for k in list(result.keys())[:2]} if result else {}
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }

@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds(offset: CronOffset = 0):
    """Endpoint for GitHub Actions to trigger daily odds fetch.

    `offset` selects which ET day to fetch: 0 = today (default), 1 = tomorrow.
    Used by the 2am ET workflow to pre-fetch tomorrow's opening lines so the
    UI has data when it rolls the slate forward after the last game ends.
    """
    try:
        request_time = datetime.now(ET)
        target = request_time.date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(
            target_date=target,
            request_time=request_time,
        )
        cache.set("odds", odds)  # Bust the 30-min cache so the next /games request sees updated data
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:nba_odds",
            "date": target.isoformat(),
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
        }

@router.get("/debug/scoreboard")
def debug_scoreboard():
    """See what the ESPN slate provides"""
    try:
        games, slate_date = get_nba_slate(slate_date_et())

        if games:
            # Return first game's full structure
            first_game = games[0]
            return {
                "success": True,
                "slate_date": slate_date.isoformat(),
                "game_count": len(games),
                "first_game_keys": list(first_game.keys()),
                "first_game_id": first_game.get('gameId'),
                "game_ids": [g.get('gameId') for g in games],
                "away_team_data": first_game.get('awayTeam', {}),
                "home_team_data": first_game.get('homeTeam', {}),
            }
        return {"success": True, "message": f"No games within {NBA_LOOKAHEAD_DAYS} days"}
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }

@router.get("/today")
async def get_todays_games():
    # Check if assembled response is still fresh
    cached_response = cache.get("assembled")
    if cached_response:
        return cached_response

    try:
        loop = asyncio.get_event_loop()

        # Fetch each data source, using cache when fresh. The scoreboard entry is
        # ESPN's (games, slate date); its TTL drops to CACHE_TTL_LIVE during games.
        today_et = slate_date_et()
        cached_scoreboard = cache.get("scoreboard")
        cached_standings = cache.get("standings")
        cached_advanced = cache.get("advanced_stats")
        cached_odds = cache.get("odds")

        # Build list of fetches needed
        tasks = []
        fetch_keys = []

        if not cached_scoreboard:
            tasks.append(loop.run_in_executor(None, lambda: get_nba_slate(today_et)))
            fetch_keys.append("scoreboard")
        if not cached_standings:
            tasks.append(loop.run_in_executor(None, get_standings_map))
            fetch_keys.append("standings")
        if not cached_advanced:
            tasks.append(loop.run_in_executor(None, get_advanced_stats_map))
            fetch_keys.append("advanced_stats")
        if not cached_odds:
            tasks.append(loop.run_in_executor(None, lambda: load_persisted_odds(table_name="nba_odds")))
            fetch_keys.append("odds")

        # Fetch only what's needed
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}, using cached data")
                else:
                    cache.set(key, result)

        # Get data (fresh or cached, with fallback to stale)
        slate = cache.get("scoreboard") or cache.get_stale("scoreboard")
        standings_map = cache.get("standings") or cache.get_stale("standings") or {}
        advanced_stats_map = cache.get("advanced_stats") or cache.get_stale("advanced_stats") or {}
        odds_map = cache.get("odds") or cache.get_stale("odds") or {}

        if not slate:
            logger.error("No NBA slate available from ESPN")
            return cache.get_stale("assembled") or []

        games, slate_date = slate
        slate_date = slate_date or today_et
        # The default odds load covers yesterday through tomorrow.
        if slate_date > today_et + timedelta(days=1):
            odds_map.update(load_persisted_odds(slate_date, table_name="nba_odds"))

        logger.info(f"Processing {len(games)} games (standings: {len(standings_map)}, advanced: {len(advanced_stats_map)}, odds: {len(odds_map)})")

        arena_map = get_arena_map()

        refined_games = []
        for g in games:
            away_tri = g['awayTeam']['teamTricode']
            home_tri = g['homeTeam']['teamTricode']
            away_team_id = str(g['awayTeam']['teamId'])
            home_team_id = str(g['homeTeam']['teamId'])

            matchup_key = f"{away_tri}@{home_tri}"
            game_status = g.get('gameStatus', 1)
            game_date_str = _nba_game_date(g) or slate_date.isoformat()
            odds = odds_map.get((game_date_str, matchup_key), {})

            # Format spread line with team abbreviation
            away_spread = odds.get("awaySpread")
            home_spread = odds.get("homeSpread")
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

            # Calculate spread result for final games
            spread_result = None  # True = covered, False = lost, "push" = exact margin
            total_result = None   # None = not final or no O/U, "over" = hit over, "under" = hit under
            over_under = odds.get("overUnder")

            if g.get('gameStatus') == 3:
                away_score = g['awayTeam'].get('score', 0)
                home_score = g['homeTeam'].get('score', 0)

                # Spread result
                if spread_value is not None:
                    spread_result = grade_spread_result(
                        away_score,
                        home_score,
                        favorite,
                        spread_value,
                    )

                # Total result (over/under)
                if over_under is not None:
                    actual_total = away_score + home_score
                    if actual_total > over_under:
                        total_result = "over"
                    elif actual_total < over_under:
                        total_result = "under"
                    else:
                        total_result = "push"

            # Get arena from map, fallback to city name
            venue = arena_map.get(home_tri, f"{g['homeTeam'].get('teamCity', 'TBD')} Arena")

            # Get standings - try tricode first, fallback to team ID
            away_stats = standings_map.get(away_tri) or standings_map.get(away_team_id, {})
            home_stats = standings_map.get(home_tri) or standings_map.get(home_team_id, {})

            # Get advanced stats - try by team name first, then team ID
            away_full_name = f"{g['awayTeam']['teamCity']} {g['awayTeam']['teamName']}"
            home_full_name = f"{g['homeTeam']['teamCity']} {g['homeTeam']['teamName']}"

            # Try name first, fallback to ID
            # Tricode last: it's the only key guaranteed to agree across feeds.
            away_advanced = (
                advanced_stats_map.get(away_full_name)
                or advanced_stats_map.get(str(g['awayTeam']['teamId']))
                or advanced_stats_map.get(away_tri, {})
            )
            home_advanced = (
                advanced_stats_map.get(home_full_name)
                or advanced_stats_map.get(str(g['homeTeam']['teamId']))
                or advanced_stats_map.get(home_tri, {})
            )

            # Calculate win percentage from scoreboard data
            away_wins = g['awayTeam']['wins']
            away_losses = g['awayTeam']['losses']
            away_win_pct = away_wins / (away_wins + away_losses) if (away_wins + away_losses) > 0 else 0.500

            home_wins = g['homeTeam']['wins']
            home_losses = g['homeTeam']['losses']
            home_win_pct = home_wins / (home_wins + home_losses) if (home_wins + home_losses) > 0 else 0.500

            # Calculate projected scores (70% market, 30% naive model)
            away_ortg = away_advanced.get("offRating", 112.0)
            away_drtg = away_advanced.get("defRating", 112.0)
            away_pace = away_advanced.get("pace", 100.0)
            home_ortg = home_advanced.get("offRating", 112.0)
            home_drtg = home_advanced.get("defRating", 112.0)
            home_pace = home_advanced.get("pace", 100.0)

            # Naive projection using ORtg/DRtg/Pace
            game_pace = (away_pace + home_pace) / 2
            naive_away = game_pace * (away_ortg + home_drtg) / 2 / 100
            naive_home = game_pace * (home_ortg + away_drtg) / 2 / 100
            naive_margin = naive_home - naive_away  # positive = home favored

            # Market margin from spread (positive = home favored)
            # spread_value is always negative (e.g., -15.5), so flip sign for home favorite
            if spread_value is not None:
                market_margin = spread_value if favorite == "away" else -spread_value
            else:
                market_margin = naive_margin  # Fall back to naive if no spread

            # Blend: 70% market, 30% naive
            blended_margin = (naive_margin * 0.3) + (market_margin * 0.7)
            total_points = naive_away + naive_home

            projected_away = round((total_points - blended_margin) / 2)
            projected_home = round((total_points + blended_margin) / 2)

            # Playoff series context — both fields are empty strings during the
            # regular season. seriesText reads like "Pacers lead series 1-0",
            # "Series tied 2-2", or "NBA Finals".
            series_text = (g.get('seriesText') or '').strip()
            series_game_number = g.get('seriesGameNumber') or ''

            refined_games.append({
                "sport": "nba",
                "gameId": g['gameId'],
                "status": g.get('gameStatusText', ''),
                "gameStatus": g.get('gameStatus', 1),  # 1=scheduled, 2=live, 3=final
                "period": g.get('period', 0),
                "gameClock": g.get('gameClock', ''),
                "time": _nba_game_time(g),
                "date": game_date_str,
                "venue": venue,
                "broadcast": _national_broadcast(g),
                "model": "EV-MODEL-V1",
                "series": series_text or None,
                "seriesGameNumber": series_game_number or None,
                "spreadLine": spread_line,
                "spreadResult": spread_result,
                "pregameSpread": odds.get("homeSpread"),  # home perspective, negative = home favored
                "spreadOddsHome": odds.get("homeSpreadOdds"),
                "spreadOddsAway": odds.get("awaySpreadOdds"),
                "homeMoneyline": odds.get("homeMoneyline"),
                "awayMoneyline": odds.get("awayMoneyline"),
                "overUnder": over_under,  # Market total from The Odds API
                "overOdds": odds.get("overOdds"),
                "underOdds": odds.get("underOdds"),
                "totalResult": total_result,  # "over", "under", "push", or None
                "projectedTotal": round(total_points),  # Model's projected total
                "away": {
                    "name": g['awayTeam']['teamName'],
                    "abbreviation": away_tri,
                    "record": f"{g['awayTeam']['wins']}-{g['awayTeam']['losses']}",
                    "winPct": away_win_pct,
                    "seed": away_stats.get("seed", 0),
                    "conference": away_stats.get("conference", "N/A"),
                    "streak": away_stats.get("streak", "N/A"),
                    "last10": away_stats.get("last10", "0-0"),
                    "score": g['awayTeam'].get('score', 0),  # Live/final score
                    "predictedScore": projected_away,
                    "winProbability": odds.get("awayWinProbability", 0.5),
                    "offRating": away_advanced.get("offRating", 112.5),
                    "defRating": away_advanced.get("defRating", 110.2),
                    "pace": away_advanced.get("pace", 98.5)
                },
                "home": {
                    "name": g['homeTeam']['teamName'],
                    "abbreviation": home_tri,
                    "record": f"{g['homeTeam']['wins']}-{g['homeTeam']['losses']}",
                    "winPct": home_win_pct,
                    "seed": home_stats.get("seed", 0),
                    "conference": home_stats.get("conference", "N/A"),
                    "streak": home_stats.get("streak", "N/A"),
                    "last10": home_stats.get("last10", "0-0"),
                    "score": g['homeTeam'].get('score', 0),  # Live/final score
                    "predictedScore": projected_home,
                    "winProbability": odds.get("homeWinProbability", 0.5),
                    "offRating": home_advanced.get("offRating", 115.1),
                    "defRating": home_advanced.get("defRating", 113.4),
                    "pace": home_advanced.get("pace", 98.5)
                }
            })

        # Set dynamic TTL based on game status
        has_live_games = any(g.get('gameStatus') == 2 for g in games)  # gameStatus 2 = live
        assembled_ttl = CACHE_TTL_LIVE if has_live_games else CACHE_TTL_DEFAULT

        # Also update scoreboard cache TTL for live games
        if has_live_games:
            cache.set_ttl("scoreboard", CACHE_TTL_LIVE)

        cache.set("assembled", refined_games, ttl=assembled_ttl)

        logger.info(f"Response assembled with TTL={assembled_ttl}s (live games: {has_live_games})")
        return refined_games
    except Exception as e:
        logger.error(f"NBA API Fetch Error: {str(e)}", exc_info=True)

        # Fallback to stale cache so the frontend stays hydrated
        cached = cache.get_stale("assembled")
        if cached:
            logger.info("Returning stale cached data due to error")
            return cached

        # Only return empty list if we have absolutely zero history
        return []


async def _game_summary(game_id: str) -> dict:
    """ESPN's summary for one game, shared by the detail page and card sparkline."""
    loop = asyncio.get_event_loop()
    return await game_feeds.fetch(
        f"{espn_feeds.SUMMARY_URL.format(sport='nba')}?event={game_id}",
        lambda: loop.run_in_executor(None, lambda: espn_feeds.fetch_summary("nba", game_id)),
    )


@router.get("/game/{game_id}/win-probability")
async def get_game_win_probability(game_id: str):
    """ESPN's per-play win-probability timeline, for the slate card sparkline."""
    try:
        summary = await _game_summary(game_id)
    except Exception as exc:
        logger.warning("NBA win-probability fetch failed for %s: %s", game_id, exc)
        raise HTTPException(status_code=502, detail="NBA win-probability feed unavailable") from exc
    return espn_feeds.adapt_espn_win_probability(summary)


@router.get("/game/{game_id}")
async def get_game_detail(game_id: str):
    matchups = await get_todays_games()
    matchup = next((g for g in matchups if str(g.get("gameId")) == str(game_id)), None)
    if not matchup:
        raise HTTPException(status_code=404, detail="NBA game not found in today's scoreboard")

    game_status = int(matchup.get("gameStatus", 1) or 1)
    status = _detail_status(game_status)
    raw_status = matchup.get("status") or ""
    away_score = {"q1": 0, "q2": 0, "q3": 0, "q4": 0, "total": (matchup.get("away") or {}).get("score", 0)}
    home_score = {"q1": 0, "q2": 0, "q3": 0, "q4": 0, "total": (matchup.get("home") or {}).get("score", 0)}
    away_box = None
    home_box = None
    plays = []
    win_probability = []

    try:
        summary = await _game_summary(game_id)
    except Exception as e:
        logger.warning(f"NBA detail summary failed for {game_id}: {e}")
        summary = {}

    competition = ((summary.get("header") or {}).get("competitions") or [{}])[0]
    competitors = {c.get("homeAway"): c for c in competition.get("competitors") or []}
    side_by_team_id = {
        str((competitors[side].get("team") or {}).get("id")): side
        for side in ("away", "home")
        if side in competitors
    }
    tricode_by_team_id = {
        team_id: (matchup.get(side) or {}).get("abbreviation")
        for team_id, side in side_by_team_id.items()
    }

    if competitors.get("away") and competitors.get("home"):
        away_score = _espn_linescore(competitors["away"])
        home_score = _espn_linescore(competitors["home"])
        raw_status = ((competition.get("status") or {}).get("type") or {}).get("detail") or raw_status

    ruled_out_by_team_id = {
        str((team_injuries.get("team") or {}).get("id")): _espn_ruled_out(team_injuries)
        for team_injuries in summary.get("injuries") or []
    }
    for player_box in (summary.get("boxscore") or {}).get("players") or []:
        team_id = str((player_box.get("team") or {}).get("id"))
        side = side_by_team_id.get(team_id)
        team = _espn_box_team(player_box, ruled_out_by_team_id.get(team_id, {}), final=game_status == 3)
        if side == "away" and team["players"]:
            away_box = _nba_team_box(team)
        elif side == "home" and team["players"]:
            home_box = _nba_team_box(team)

    if summary.get("plays"):
        plays, win_probability = _nba_pbp(
            _espn_actions(summary["plays"], tricode_by_team_id),
            matchup,
            _espn_home_prob_by_play(summary.get("winprobability") or []),
        )

    return {
        "sport": "nba",
        "gameId": game_id,
        "matchup": matchup,
        "status": status,
        "rawStatus": raw_status,
        "awayScore": away_score,
        "homeScore": home_score,
        "awayBoxScore": away_box,
        "homeBoxScore": home_box,
        "playByPlay": plays,
        "winProbability": win_probability,
        "scoringEfficiency": {
            "away": _nba_efficiency(away_box),
            "home": _nba_efficiency(home_box),
        } if away_box or home_box else None,
    }
