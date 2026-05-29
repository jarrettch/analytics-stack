from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter

from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    Cache,
    de_vig_ml,
    fetch_sgo_odds,
    format_et_time,
    get_http_client,
    load_persisted_odds,
    local_time_hhmm,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mlb", tags=["MLB"])

cache = Cache({
    "schedule":         CACHE_TTL_DEFAULT,
    "standings":        300,
    "stats":            600,
    "odds":             CACHE_TTL_ODDS,
    "pitcher_records":  600,
    "assembled":        CACHE_TTL_DEFAULT,
})


async def get_pitcher_records(pitcher_ids: list[int]) -> dict[int, str]:
    """Fetch season W-L for the given pitcher IDs in one MLB Stats API call.

    Returns a dict mapping pitcher_id → "W-L" string. Pitchers with no season
    stats yet (e.g. just promoted) are omitted from the map.
    """
    if not pitcher_ids:
        return {}
    try:
        client = get_http_client()
        r = await client.get(
            "https://statsapi.mlb.com/api/v1/people",
            params={
                "personIds": ",".join(str(pid) for pid in pitcher_ids),
                "hydrate": f"stats(group=[pitching],type=[season],season={datetime.now().year})",
            },
            timeout=10.0,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.warning(f"Pitcher records fetch failed: {e}")
        return {}

    records: dict[int, str] = {}
    for person in data.get("people", []):
        pid = person.get("id")
        for stats_group in person.get("stats", []):
            if stats_group.get("group", {}).get("displayName") != "pitching":
                continue
            for split in stats_group.get("splits", []):
                stat = split.get("stat", {})
                wins = stat.get("wins")
                losses = stat.get("losses")
                if wins is not None and losses is not None:
                    records[pid] = f"{wins}-{losses}"
                    break
            if pid in records:
                break
    logger.info(f"MLB pitcher records: {len(records)}/{len(pitcher_ids)} resolved")
    return records


async def get_context_metrics(client: httpx.AsyncClient, game_pk: int):
    try:
        resp = await client.get(
            f"https://statsapi.mlb.com/api/v1/game/{game_pk}/contextMetrics",
            timeout=5.0,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None

MLB_TEAM_ID_TO_TRI = {
    108: "LAA", 109: "AZ",  110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH",
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "STL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}

def get_tricode_from_sgo_team_id(team_id: str) -> str:
    """Map SportsGameOdds MLB teamID to tricode. All teams use full name + _MLB suffix."""
    sgo_map = {
        "LOS_ANGELES_ANGELS_MLB": "LAA",
        "ARIZONA_DIAMONDBACKS_MLB": "AZ",
        "BALTIMORE_ORIOLES_MLB": "BAL",
        "BOSTON_RED_SOX_MLB": "BOS",
        "CHICAGO_CUBS_MLB": "CHC",
        "CINCINNATI_REDS_MLB": "CIN",
        "CLEVELAND_GUARDIANS_MLB": "CLE",
        "COLORADO_ROCKIES_MLB": "COL",
        "DETROIT_TIGERS_MLB": "DET",
        "HOUSTON_ASTROS_MLB": "HOU",
        "KANSAS_CITY_ROYALS_MLB": "KC",
        "LOS_ANGELES_DODGERS_MLB": "LAD",
        "WASHINGTON_NATIONALS_MLB": "WSH",
        "NEW_YORK_METS_MLB": "NYM",
        "OAKLAND_ATHLETICS_MLB": "ATH",
        "PITTSBURGH_PIRATES_MLB": "PIT",
        "SAN_DIEGO_PADRES_MLB": "SD",
        "SEATTLE_MARINERS_MLB": "SEA",
        "SAN_FRANCISCO_GIANTS_MLB": "SF",
        "ST_LOUIS_CARDINALS_MLB": "STL",
        "STLOUIS_CARDINALS_MLB": "STL",  # SGO actually returns this variant (no underscore)
        "TAMPA_BAY_RAYS_MLB": "TB",
        "TEXAS_RANGERS_MLB": "TEX",
        "TORONTO_BLUE_JAYS_MLB": "TOR",
        "MINNESOTA_TWINS_MLB": "MIN",
        "PHILADELPHIA_PHILLIES_MLB": "PHI",
        "ATLANTA_BRAVES_MLB": "ATL",
        "CHICAGO_WHITE_SOX_MLB": "CWS",
        "MIAMI_MARLINS_MLB": "MIA",
        "NEW_YORK_YANKEES_MLB": "NYY",
        "MILWAUKEE_BREWERS_MLB": "MIL",
    }
    return sgo_map.get(team_id.upper(), "")

async def get_schedule(date_str: str) -> list:
    """Fetch today's MLB schedule from the MLB Stats API."""
    try:
        url = "https://statsapi.mlb.com/api/v1/schedule"
        params = {
            "sportId": 1,
            "date": date_str,
            "hydrate": "probablePitcher,linescore,team",
        }
        client = get_http_client()
        r = await client.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        games = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                # Spring training filter — flip to "R" for regular season
                if game.get("gameType") != "R":
                    continue

                status_obj     = game.get("status", {})
                abstract_state = status_obj.get("abstractGameState", "Preview")
                detailed_state = status_obj.get("detailedState", "")
                STATUS_MAP = {"Preview": 1, "Live": 2, "Final": 3}
                game_status = STATUS_MAP.get(abstract_state, 1)

                starts_at = game.get("gameDate", "")
                official_date = game.get("officialDate", date_str)

                if "Postponed" in detailed_state:
                    status_text = "PPD"
                    game_status = 1
                elif "Suspended" in detailed_state:
                    status_text = "Suspended"
                    game_status = 1
                elif "Delayed" in detailed_state:
                    status_text = f"Delayed ({format_et_time(starts_at)})"
                elif game_status == 1:
                    status_text = format_et_time(starts_at)
                elif game_status == 2:
                    linescore  = game.get("linescore", {})
                    inning     = linescore.get("currentInning", 0)
                    half       = linescore.get("inningHalf", "")
                    status_text = f"{'T' if half == 'Top' else 'B'}{inning}" if inning else "Live"
                    current_inning = inning
                else:
                    linescore  = game.get("linescore", {})
                    current    = linescore.get("currentInning", 0)
                    scheduled  = linescore.get("scheduledInnings", 9)
                    status_text = f"F/{current}" if current > scheduled else "Final"
                    current_inning = current

                if game_status == 1:
                    current_inning = 0

                if abstract_state not in ("Preview", "Live", "Final"):
                    logger.warning(f"Unrecognized MLB game state: abstractGameState={abstract_state!r} detailedState={detailed_state!r} gamePk={game.get('gamePk')}")

                home_team = game["teams"]["home"]
                away_team = game["teams"]["away"]

                double_header = game.get("doubleHeader", "N")
                game_number = game.get("gameNumber", 1)

                games.append({
                    "gamePk": str(game["gamePk"]),
                    "gameStatus": game_status,
                    "statusText": status_text,
                    "officialDate": official_date,
                    "localTimeHHMM": local_time_hhmm(starts_at),
                    "startsAt": starts_at,
                    "homeTeamId": home_team["team"]["id"],
                    "awayTeamId": away_team["team"]["id"],
                    "homeAbbr": home_team["team"].get("abbreviation", ""),
                    "awayAbbr": away_team["team"].get("abbreviation", ""),
                    "homePitcher": home_team.get("probablePitcher", {}).get("fullName"),
                    "awayPitcher": away_team.get("probablePitcher", {}).get("fullName"),
                    "homePitcherId": home_team.get("probablePitcher", {}).get("id"),
                    "awayPitcherId": away_team.get("probablePitcher", {}).get("id"),
                    "venue": game.get("venue", {}).get("name", ""),
                    "isDoubleHeader": double_header == "Y",
                    "gameNumber": game_number if double_header == "Y" else None,
                    # Scores for live/final games
                    "homeScore": home_team.get("score"),
                    "awayScore": away_team.get("score"),
                    "currentInning": current_inning,
                })

        logger.info(f"MLB schedule: {len(games)} ST games on {date_str}")
        return games
    except Exception as e:
        logger.error(f"MLB schedule fetch error: {e}", exc_info=True)
        return []

async def get_standings_map() -> dict:
    """Fetch AL/NL standings from MLB Stats API. Returns dict keyed by team ID."""
    try:
        current_year = datetime.now().year
        url = "https://statsapi.mlb.com/api/v1/standings"
        params = {
            "leagueId": "103,104",
            "season": current_year,
            "standingsType": "regularSeason",
        }
        client = get_http_client()
        r = await client.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()

        standings_map = {}
        for division in data.get("records", []):
            division_name = division.get("division", {}).get("nameShort", "")
            league_id = division.get("league", {}).get("id")
            conference = "AL" if league_id == 103 else "NL"

            for record in division.get("teamRecords", []):
                team_id = record["team"]["id"]
                wins = record.get("wins", 0)
                losses = record.get("losses", 0)
                win_pct_str = record.get("winningPercentage", ".000")
                div_rank = int(record.get("divisionRank", "0"))

                streak_info = record.get("streak", {})
                streak_type = streak_info.get("streakType", "")
                streak_num = streak_info.get("streakNumber", 0)
                streak = f"{'W' if streak_type == 'wins' else 'L'}{streak_num}" if streak_type else "N/A"

                standings_map[team_id] = {
                    "wins": wins,
                    "losses": losses,
                    "winPct": float(win_pct_str) if win_pct_str else (wins / (wins + losses) if (wins + losses) > 0 else 0.5),
                    "divisionRank": div_rank,
                    "streak": streak,
                    "division": division_name,
                    "conference": conference,
                }

        logger.info(f"MLB standings map: {len(standings_map)} teams")
        return standings_map
    except Exception as e:
        logger.error(f"MLB standings fetch error: {e}", exc_info=True)
        return {}

async def get_stats_map() -> dict:
    """Fetch team hitting + pitching stats from MLB Stats API. Returns dict keyed by team ID."""
    try:
        current_year = datetime.now().year
        base = "https://statsapi.mlb.com/api/v1/teams/stats"
        base_params = {"sportId": 1, "stats": "season"}

        client = get_http_client()
        hitting_r = await client.get(base, params={**base_params, "season": current_year, "group": "hitting"}, timeout=15)
        pitching_r = await client.get(base, params={**base_params, "season": current_year, "group": "pitching"}, timeout=15)
        hitting_r.raise_for_status()
        pitching_r.raise_for_status()
        hitting_data = hitting_r.json()
        pitching_data = pitching_r.json()

        hitting_records = hitting_data.get("stats", [{}])[0].get("splits", [])
        pitching_records = pitching_data.get("stats", [{}])[0].get("splits", [])

        # Fall back to prior season if current year is sparse (early spring training)
        if len(hitting_records) < 5:
            logger.warning(f"MLB hitting stats sparse for {current_year} ({len(hitting_records)} teams), falling back to {current_year - 1}")
            r = await client.get(base, params={**base_params, "season": current_year - 1, "group": "hitting"}, timeout=15)
            r.raise_for_status()
            hitting_records = r.json().get("stats", [{}])[0].get("splits", [])
        if len(pitching_records) < 5:
            logger.warning(f"MLB pitching stats sparse for {current_year} ({len(pitching_records)} teams), falling back to {current_year - 1}")
            r = await client.get(base, params={**base_params, "season": current_year - 1, "group": "pitching"}, timeout=15)
            r.raise_for_status()
            pitching_records = r.json().get("stats", [{}])[0].get("splits", [])

        stats_map = {}

        for split in hitting_records:
            team_id = split.get("team", {}).get("id")
            s = split.get("stat", {})
            games = s.get("gamesPlayed", 1) or 1
            stats_map.setdefault(team_id, {}).update({
                "battingAvg": float(s.get("avg", "0") or 0),
                "ops": float(s.get("ops", "0") or 0),
                "runsScored": round(int(s.get("runs", 0)) / games, 2),
            })

        for split in pitching_records:
            team_id = split.get("team", {}).get("id")
            s = split.get("stat", {})
            games = s.get("gamesPlayed", 1) or 1
            stats_map.setdefault(team_id, {}).update({
                "era": float(s.get("era", "4.50") or 4.50),
                "runsAllowed": round(int(s.get("runs", 0)) / games, 2),
            })

        logger.info(f"MLB stats map: {len(stats_map)} teams")
        return stats_map
    except Exception as e:
        logger.error(f"MLB stats fetch error: {e}", exc_info=True)
        return {}

async def get_odds_map(target_date: date | None = None):
    """Fetch MLB odds from SportsGameOdds, merged with persisted rows."""
    return await fetch_sgo_odds(
        league_id="MLB",
        table_name="mlb_odds",
        tricode_for=get_tricode_from_sgo_team_id,
        target_date=target_date,
    )

@router.get("/cron/fetch-odds")
async def cron_fetch_odds(offset: int = 0):
    """GitHub Actions cron: fetch and persist a single ET day's MLB odds.

    `offset` selects which ET day to fetch: 0 = today (default), 1 = tomorrow.
    Used by the 2am ET workflow to pre-fetch tomorrow's opening lines so the
    UI has data when it rolls the slate forward after the last game ends.
    """
    try:
        target = datetime.now(ET).date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(target_date=target)
        cache.set("odds", odds)
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:mlb_odds",
            "date": target.isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/today")
async def get_todays_games():
    cached_response = cache.get("assembled")
    if cached_response:
        return cached_response

    try:
        today_et = datetime.now(ET).date()
        date_str = today_et.isoformat()

        # Fetch uncached data sources in parallel
        cached_schedule  = cache.get("schedule")
        cached_standings = cache.get("standings")
        cached_stats     = cache.get("stats")
        cached_odds      = cache.get("odds")

        tasks = []
        keys  = []

        if not cached_schedule:
            tasks.append(get_schedule(date_str))
            keys.append("schedule")
        if not cached_standings:
            tasks.append(get_standings_map())
            keys.append("standings")
        if not cached_stats:
            tasks.append(get_stats_map())
            keys.append("stats")
        if not cached_odds:
            tasks.append(asyncio.get_event_loop().run_in_executor(None, lambda: load_persisted_odds(table_name="mlb_odds")))
            keys.append("odds")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}")
                else:
                    cache.set(key, result)

        schedule  = cache.get("schedule")  or cache.get_stale("schedule")  or []
        standings = cache.get("standings") or cache.get_stale("standings") or {}
        stats     = cache.get("stats")     or cache.get_stale("stats")     or {}
        odds_map  = cache.get("odds")      or cache.get_stale("odds")      or {}

        pitcher_records = cache.get("pitcher_records")
        if pitcher_records is None:
            pitcher_ids = [
                pid for g in schedule
                for pid in (g.get("homePitcherId"), g.get("awayPitcherId"))
                if pid is not None
            ]
            pitcher_records = await get_pitcher_records(pitcher_ids)
            cache.set("pitcher_records", pitcher_records)

        logger.info(f"MLB: {len(schedule)} games, {len(standings)} standings, {len(stats)} stats, {len(odds_map)} odds, {len(pitcher_records)} pitcher records")

        # Fetch contextMetrics for live games (win probability)
        live_pks = [g["gamePk"] for g in schedule if g.get("gameStatus") == 2]
        context_metrics_map: dict[int, dict] = {}
        if live_pks:
            client = get_http_client()
            cm_results = await asyncio.gather(
                *[get_context_metrics(client, pk) for pk in live_pks],
                return_exceptions=True,
            )
            for pk, result in zip(live_pks, cm_results):
                if isinstance(result, dict):
                    context_metrics_map[pk] = result

        refined_games = []
        for g in schedule:
            away_tri      = g["awayAbbr"]
            home_tri      = g["homeAbbr"]
            home_team_id  = g["homeTeamId"]
            away_team_id  = g["awayTeamId"]
            game_status   = g["gameStatus"]
            official_date = g["officialDate"]

            matchup_key = f"{away_tri}@{home_tri}"
            odds = odds_map.get((official_date, matchup_key), {})

            # Spread line
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

            over_under    = odds.get("overUnder")
            spread_result = None
            total_result  = None

            if game_status == 3:
                away_score = g.get("awayScore") or 0
                home_score = g.get("homeScore") or 0

                if spread_value is not None:
                    margin = away_score - home_score
                    if favorite == "away":
                        spread_result = margin > abs(spread_value)
                    else:
                        spread_result = (-margin) > abs(spread_value)

                if over_under is not None:
                    actual_total = away_score + home_score
                    if actual_total > over_under:
                        total_result = "over"
                    elif actual_total < over_under:
                        total_result = "under"
                    else:
                        total_result = "push"

            # Team data from standings/stats
            away_std  = standings.get(away_team_id) or standings.get(g.get("awayTeamId"), {})
            home_std  = standings.get(home_team_id) or standings.get(g.get("homeTeamId"), {})
            away_stat = stats.get(away_team_id) or stats.get(g.get("awayTeamId"), {})
            home_stat = stats.get(home_team_id) or stats.get(g.get("homeTeamId"), {})

            away_wins   = away_std.get("wins", 0)
            away_losses = away_std.get("losses", 0)
            home_wins   = home_std.get("wins", 0)
            home_losses = home_std.get("losses", 0)

            # Projected scores: naive RS/RA model blended with market O/U
            away_rs = away_stat.get("runsScored", 4.5)
            away_ra = away_stat.get("runsAllowed", 4.5)
            home_rs = home_stat.get("runsScored", 4.5)
            home_ra = home_stat.get("runsAllowed", 4.5)

            naive_away  = (away_rs + home_ra) / 2
            naive_home  = (home_rs + away_ra) / 2
            naive_total = naive_away + naive_home

            if over_under:
                projected_total = round(0.7 * over_under + 0.3 * naive_total, 1)
                ratio = naive_away / naive_total if naive_total > 0 else 0.5
                proj_away = round(projected_total * ratio)
                proj_home = round(projected_total * (1 - ratio))
            else:
                projected_total = round(naive_total, 1)
                proj_away = round(naive_away)
                proj_home = round(naive_home)

            game_pk = g["gamePk"]
            home_ml = odds.get("homeMoneyline")
            away_ml = odds.get("awayMoneyline")
            if home_ml and away_ml:
                home_prob, away_prob = de_vig_ml(home_ml, away_ml)
            else:
                home_prob = away_prob = 0.5

            live_home_prob = None
            live_away_prob = None
            if game_status == 2 and game_pk in context_metrics_map:
                cm = context_metrics_map[game_pk]
                raw_home = cm.get("homeWinProbability")
                raw_away = cm.get("awayWinProbability")
                if raw_home is not None and raw_away is not None:
                    live_home_prob = raw_home / 100
                    live_away_prob = raw_away / 100
                elif home_ml and away_ml:
                    live_home_prob, live_away_prob = de_vig_ml(home_ml, away_ml)

            game_number = g.get("gameNumber")

            refined_games.append({
                "sport": "mlb",
                "gameId": g["gamePk"],
                "gameStatus": game_status,
                "currentInning": g.get("currentInning", 0),
                "status": g["statusText"],
                "date": official_date,
                "time": g["localTimeHHMM"],
                "venue": g.get("venue", ""),
                "broadcast": "Local",
                "model": "PECOTA v2.8",
                "gameNumber": game_number,
                "spreadLine": spread_line,
                "spreadResult": spread_result,
                "pregameSpread": odds.get("homeSpread"),
                "spreadOddsHome": odds.get("homeSpreadOdds"),
                "spreadOddsAway": odds.get("awaySpreadOdds"),
                "overUnder": over_under,
                "overOdds": odds.get("overOdds"),
                "underOdds": odds.get("underOdds"),
                "homeMoneyline": odds.get("homeMoneyline"),
                "awayMoneyline": odds.get("awayMoneyline"),
                "totalResult": total_result,
                "projectedTotal": projected_total,
                "away": {
                    "name": away_tri,
                    "abbreviation": away_tri,
                    "score": g.get("awayScore") or 0,
                    "record": f"{away_wins}-{away_losses}",
                    "winPct": away_std.get("winPct", 0.5),
                    "seed": away_std.get("divisionRank", 0),
                    "division": away_std.get("division", ""),
                    "conference": away_std.get("conference", ""),
                    "streak": away_std.get("streak", "N/A"),
                    "predictedScore": proj_away,
                    "winProbability": away_prob,
                    "liveWinProbability": live_away_prob,
                    "runsScored": away_rs,
                    "runsAllowed": away_ra,
                    "era": away_stat.get("era", 4.50),
                    "battingAvg": away_stat.get("battingAvg", 0.250),
                    "ops": away_stat.get("ops", 0.700),
                    "probablePitcher": g.get("awayPitcher"),
                    "pitcherRecord": pitcher_records.get(g.get("awayPitcherId")),
                },
                "home": {
                    "name": home_tri,
                    "abbreviation": home_tri,
                    "score": g.get("homeScore") or 0,
                    "record": f"{home_wins}-{home_losses}",
                    "winPct": home_std.get("winPct", 0.5),
                    "seed": home_std.get("divisionRank", 0),
                    "division": home_std.get("division", ""),
                    "conference": home_std.get("conference", ""),
                    "streak": home_std.get("streak", "N/A"),
                    "predictedScore": proj_home,
                    "winProbability": home_prob,
                    "liveWinProbability": live_home_prob,
                    "runsScored": home_rs,
                    "runsAllowed": home_ra,
                    "era": home_stat.get("era", 4.50),
                    "battingAvg": home_stat.get("battingAvg", 0.250),
                    "ops": home_stat.get("ops", 0.700),
                    "probablePitcher": g.get("homePitcher"),
                    "pitcherRecord": pitcher_records.get(g.get("homePitcherId")),
                },
            })

        assembled_ttl = CACHE_TTL_LIVE if live_pks else CACHE_TTL_DEFAULT
        cache.set("assembled", refined_games, ttl=assembled_ttl)
        logger.info(f"MLB response assembled: {len(refined_games)} games")
        return refined_games

    except Exception as e:
        logger.error(f"MLB API error: {e}", exc_info=True)
        cached = cache.get_stale("assembled")
        if cached:
            return cached
        return []
