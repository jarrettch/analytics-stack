from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta

from fastapi import APIRouter
from nba_api.stats.endpoints import leaguestandingsv3, leaguedashteamstats

from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    Cache,
    fetch_odds_api_odds,
    get_http_client,
    load_persisted_odds,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ESPN_WNBA_SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard"
)


router = APIRouter(prefix="/api/wnba", tags=["WNBA"])

cache = Cache({
    "scoreboard":     CACHE_TTL_DEFAULT,
    "standings":      300,
    "advanced_stats": 600,
    "odds":           CACHE_TTL_ODDS,
    "assembled":      CACHE_TTL_DEFAULT,
})

# Authoritative WNBA team identity, harvested from the WNBA schedule CDN. ESPN's
# scoreboard abbreviations are normalized to these tricodes. nba_api's team list is stale for 2026
# (missing Toronto/Portland, and lists Phoenix as PHO not PHX), so we hardcode here.
# ESPN's logo CDN uses its own abbreviations (lv not lva, gs not gsv, etc.), so each
# team carries the ESPN slug for the frontend's espnSlug override.
#   tricode: (team_id, full_name, nickname, arena, espn_slug)
WNBA_TEAMS: dict[str, tuple[str, str, str, str, str]] = {
    "ATL": ("1611661330", "Atlanta Dream",          "Dream",     "Gateway Center Arena @ College Park", "atl"),
    "CHI": ("1611661329", "Chicago Sky",            "Sky",       "Wintrust Arena",                      "chi"),
    "CON": ("1611661323", "Connecticut Sun",        "Sun",       "Mohegan Sun Arena",                   "con"),
    "DAL": ("1611661321", "Dallas Wings",           "Wings",     "College Park Center",                 "dal"),
    "GSV": ("1611661331", "Golden State Valkyries", "Valkyries", "Chase Center",                        "gs"),
    "IND": ("1611661325", "Indiana Fever",          "Fever",     "Gainbridge Fieldhouse",               "ind"),
    "LAS": ("1611661320", "Los Angeles Sparks",     "Sparks",    "Crypto.com Arena",                    "la"),
    "LVA": ("1611661319", "Las Vegas Aces",         "Aces",      "Michelob ULTRA Arena",                "lv"),
    "MIN": ("1611661324", "Minnesota Lynx",         "Lynx",      "Target Center",                       "min"),
    "NYL": ("1611661313", "New York Liberty",       "Liberty",   "Barclays Center",                     "ny"),
    "PDX": ("1611661327", "Portland Fire",          "Fire",      "Moda Center",                         "por"),
    "PHX": ("1611661317", "Phoenix Mercury",        "Mercury",   "Mortgage Matchup Center",             "phx"),
    "SEA": ("1611661328", "Seattle Storm",          "Storm",     "Climate Pledge Arena",                "sea"),
    "TOR": ("1611661332", "Toronto Tempo",          "Tempo",     "Coca-Cola Coliseum",                  "tor"),
    "WAS": ("1611661322", "Washington Mystics",     "Mystics",   "CareFirst Arena",                     "wsh"),
}

_FULLNAME_TO_TRICODE = {v[1].lower(): tri for tri, v in WNBA_TEAMS.items()}
_NICKNAME_TO_TRICODE = {v[2].lower(): tri for tri, v in WNBA_TEAMS.items()}
_ESPN_SLUG = {tri: v[4] for tri, v in WNBA_TEAMS.items()}
_ESPN_TO_WNBA_TRICODE = {"POR": "PDX"}


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _espn_game_clock(display_clock: str) -> str:
    """Convert ESPN's M:SS display clock to the ISO duration used by the UI."""
    if not display_clock or ":" not in display_clock:
        return ""
    minutes, seconds = display_clock.split(":", 1)
    try:
        return f"PT{int(minutes):02d}M{float(seconds):05.2f}S"
    except (TypeError, ValueError):
        return ""


def _espn_record(competitor: dict) -> tuple[int, int]:
    records = competitor.get("records") or []
    overall = next((r for r in records if r.get("name") == "overall"), None)
    summary = (overall or (records[0] if records else {})).get("summary", "0-0")
    try:
        wins, losses = summary.split("-", 1)
        return int(wins), int(losses)
    except (AttributeError, TypeError, ValueError):
        return 0, 0


def _espn_team(competitor: dict) -> dict:
    team = competitor.get("team") or {}
    abbreviation = team.get("abbreviation", "")
    tricode = _ESPN_TO_WNBA_TRICODE.get(abbreviation, abbreviation)
    wins, losses = _espn_record(competitor)
    canonical_id = WNBA_TEAMS.get(tricode, (str(team.get("id", "")),))[0]
    return {
        "teamId": canonical_id,
        "teamCity": team.get("location", ""),
        "teamName": team.get("name", ""),
        "teamTricode": tricode,
        "wins": wins,
        "losses": losses,
        "score": _safe_int(competitor.get("score")),
    }


def adapt_espn_scoreboard(payload: dict) -> dict:
    """Adapt ESPN's WNBA scoreboard into nba_api's live scoreboard shape."""
    games = []
    for event in payload.get("events") or []:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        competition = competitions[0]
        competitors = competition.get("competitors") or []
        home = next((c for c in competitors if c.get("homeAway") == "home"), None)
        away = next((c for c in competitors if c.get("homeAway") == "away"), None)
        if not home or not away:
            continue

        status = competition.get("status") or event.get("status") or {}
        status_type = status.get("type") or {}
        state = status_type.get("state")
        completed = bool(status_type.get("completed"))
        game_status = 3 if completed or state == "post" else 2 if state == "in" else 1

        game_datetime = event.get("date") or competition.get("date") or ""
        if game_datetime:
            try:
                game_datetime = (
                    datetime.fromisoformat(game_datetime.replace("Z", "+00:00"))
                    .astimezone(ET)
                    .isoformat()
                )
            except ValueError:
                pass

        broadcasts = [
            name
            for broadcast in competition.get("broadcasts") or []
            for name in broadcast.get("names") or []
        ]
        games.append(
            {
                "gameId": str(event.get("id") or competition.get("id") or ""),
                "gameEt": game_datetime,
                "gameStatus": game_status,
                "gameStatusText": (
                    status_type.get("description") or status_type.get("detail") or ""
                ),
                "period": _safe_int(status.get("period")),
                "gameClock": (
                    _espn_game_clock(status.get("displayClock", ""))
                    if game_status == 2
                    else ""
                ),
                "awayTeam": _espn_team(away),
                "homeTeam": _espn_team(home),
                "seriesText": "",
                "seriesGameNumber": "",
                "broadcast": ", ".join(broadcasts),
            }
        )

    return {"scoreboard": {"games": games}}


async def fetch_wnba_scoreboard(target_date: date | None = None) -> dict:
    """Fetch a WNBA slate from ESPN; the WNBA CDN blocks Render with Akamai WAF."""
    target = target_date or datetime.now(ET).date()
    client = get_http_client()
    response = await client.get(
        ESPN_WNBA_SCOREBOARD_URL,
        params={"dates": target.strftime("%Y%m%d")},
    )
    response.raise_for_status()
    return adapt_espn_scoreboard(response.json())

# WNBA league averages (per-game ORtg/DRtg, pace) used only as fallbacks when the
# advanced-stats endpoint is missing a team.
DEFAULT_OFF_RATING = 100.0
DEFAULT_DEF_RATING = 100.0
DEFAULT_PACE = 96.0

# WNBA regulation is 40 minutes (4x10) vs the NBA's 48; used to rescale the per-48
# PACE stat when projecting scores.
WNBA_REG_MINUTES = 40


def wnba_season() -> str:
    """WNBA seasons are single calendar years (May–Oct), so the season string is the year."""
    return str(datetime.now(ET).year)


def get_tricode_from_name(name: str) -> str:
    """Map a team name to its WNBA tricode.

    Handles The Odds API's full names ("Las Vegas Aces") and bare nicknames, with a
    last-token fallback (WNBA nicknames are unique, so "LA Sparks" -> Sparks -> LAS).
    Returns "" on no match so the odds provider logs and skips the event.
    """
    if not name:
        return ""
    key = name.strip().lower()
    if key in _FULLNAME_TO_TRICODE:
        return _FULLNAME_TO_TRICODE[key]
    if key in _NICKNAME_TO_TRICODE:
        return _NICKNAME_TO_TRICODE[key]
    tokens = key.split()
    return _NICKNAME_TO_TRICODE.get(tokens[-1], "") if tokens else ""


def get_arena_map() -> dict[str, str]:
    return {tri: v[3] for tri, v in WNBA_TEAMS.items()}


def get_standings_map() -> dict:
    """WNBA standings via LeagueStandingsV3 (league_id=10). Keyed by tricode and team ID."""
    try:
        season = wnba_season()
        logger.info(f"Fetching WNBA standings (v3) for {season}...")
        standings = leaguestandingsv3.LeagueStandingsV3(
            league_id="10", season=season, timeout=10
        ).get_dict()
        rs = standings['resultSets'][0]
        rows = rs['rowSet']
        h = {header: i for i, header in enumerate(rs['headers'])}

        stats_map = {}
        for row in rows:
            team_id = str(row[h['TeamID']]) if 'TeamID' in h else None
            team_name = row[h['TeamName']] if 'TeamName' in h else None  # nickname, e.g. "Aces"
            tricode = _NICKNAME_TO_TRICODE.get((team_name or "").lower())

            wins = row[h['WINS']]
            losses = row[h['LOSSES']]
            conference = row[h['Conference']] if 'Conference' in h else "N/A"
            seed = row[h['PlayoffRank']] if 'PlayoffRank' in h else 0
            streak = row[h['strCurrentStreak']] if 'strCurrentStreak' in h else "N/A"
            last10 = row[h['L10']] if 'L10' in h else "N/A"

            team_data = {
                "record": f"{wins}-{losses}",
                "winPct": wins / (wins + losses) if (wins + losses) > 0 else 0.5,
                "seed": seed,
                "conference": conference if conference else "N/A",
                "streak": streak if streak else "N/A",
                "last10": last10 if last10 else "N/A",
            }
            if tricode:
                stats_map[tricode] = team_data
            if team_id:
                stats_map[team_id] = team_data

        logger.info(f"Built WNBA standings map with {len(stats_map)} entries")
        return stats_map
    except Exception as e:
        logger.error(f"WNBA Standings Fetch Error: {e}", exc_info=True)
        return {}


def get_advanced_stats_map() -> dict:
    """WNBA advanced team stats (ORtg/DRtg/pace). Keyed by full team name and team ID."""
    try:
        season = wnba_season()
        logger.info(f"Fetching WNBA advanced team stats for {season}...")
        stats = leaguedashteamstats.LeagueDashTeamStats(
            measure_type_detailed_defense='Advanced',
            per_mode_detailed='PerGame',
            league_id_nullable="10",
            season=season,
            timeout=15,
        )
        result = stats.get_dict()
        rs = result['resultSets'][0]
        rows = rs['rowSet']
        h = {header: i for i, header in enumerate(rs['headers'])}

        advanced_map = {}
        for row in rows:
            team_name = row[h['TEAM_NAME']]
            team_id = str(row[h['TEAM_ID']])
            team_stats = {
                "offRating": row[h['OFF_RATING']],
                "defRating": row[h['DEF_RATING']],
                "netRating": row[h['NET_RATING']] if 'NET_RATING' in h else None,
                "pace": row[h['PACE']] if 'PACE' in h else DEFAULT_PACE,
            }
            advanced_map[team_name] = team_stats
            advanced_map[team_id] = team_stats

        logger.info(f"Built WNBA advanced stats map with {len(rows)} teams")
        return advanced_map
    except Exception as e:
        logger.error(f"WNBA Advanced Stats Fetch Error: {e}", exc_info=True)
        return {}


async def get_odds_map(target_date: date | None = None):
    """Fetch WNBA odds from The Odds API, merged with persisted rows.

    WNBA isn't on our SportsGameOdds tier, so this uses the Odds API provider instead
    of fetch_sgo_odds. Same normalized output shape as the other sports.
    """
    return await fetch_odds_api_odds(
        sport="wnba",
        sport_key="basketball_wnba",
        table_name="wnba_odds",
        tricode_for=get_tricode_from_name,
        target_date=target_date,
    )


@router.get("/debug/standings")
def debug_standings():
    """Debug endpoint to test the WNBA standings fetch directly."""
    try:
        result = get_standings_map()
        return {
            "success": True,
            "entry_count": len(result),
            "sample_keys": list(result.keys())[:10] if result else [],
        }
    except Exception as e:
        return {"success": False, "error": str(e), "error_type": type(e).__name__}


@router.get("/debug/scoreboard")
async def debug_scoreboard():
    """See what the WNBA live scoreboard actually returns."""
    try:
        data = await fetch_wnba_scoreboard()
        games = data.get('scoreboard', {}).get('games', [])
        if games:
            first = games[0]
            return {
                "success": True,
                "game_count": len(games),
                "first_game_keys": list(first.keys()),
                "game_ids": [g.get('gameId') for g in games],
                "away_team_data": first.get('awayTeam', {}),
                "home_team_data": first.get('homeTeam', {}),
            }
        return {"success": True, "message": "No WNBA games today"}
    except Exception as e:
        return {"success": False, "error": str(e), "error_type": type(e).__name__}


@router.get("/cron/fetch-odds")
async def cron_fetch_odds(offset: int = 0):
    """GitHub Actions cron: fetch and persist a single ET day's WNBA odds.

    `offset` selects which ET day: 0 = today (default), 1 = tomorrow (pre-fetch).
    """
    try:
        target = datetime.now(ET).date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(target_date=target)
        cache.set("odds", odds)  # Bust the 30-min cache so the next /today sees updated data
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:wnba_odds",
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
        loop = asyncio.get_event_loop()

        cached_scoreboard = cache.get("scoreboard")
        cached_standings = cache.get("standings")
        cached_advanced = cache.get("advanced_stats")
        cached_odds = cache.get("odds")

        tasks = []
        fetch_keys = []
        if not cached_scoreboard:
            tasks.append(fetch_wnba_scoreboard())
            fetch_keys.append("scoreboard")
        if not cached_standings:
            tasks.append(loop.run_in_executor(None, get_standings_map))
            fetch_keys.append("standings")
        if not cached_advanced:
            tasks.append(loop.run_in_executor(None, get_advanced_stats_map))
            fetch_keys.append("advanced_stats")
        if not cached_odds:
            tasks.append(loop.run_in_executor(None, lambda: load_persisted_odds(table_name="wnba_odds")))
            fetch_keys.append("odds")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}, using cached data")
                else:
                    cache.set(key, result)

        sb = cache.get("scoreboard") or cache.get_stale("scoreboard")
        standings_map = cache.get("standings") or cache.get_stale("standings") or {}
        advanced_stats_map = cache.get("advanced_stats") or cache.get_stale("advanced_stats") or {}
        odds_map = cache.get("odds") or cache.get_stale("odds") or {}

        if not sb:
            logger.error("No WNBA scoreboard data available")
            return cache.get_stale("assembled") or []

        data = sb
        games = data.get('scoreboard', {}).get('games', [])
        logger.info(
            f"Processing {len(games)} WNBA games (standings: {len(standings_map)}, "
            f"advanced: {len(advanced_stats_map)}, odds: {len(odds_map)})"
        )

        arena_map = get_arena_map()

        refined_games = []
        for g in games:
            away_tri = g['awayTeam']['teamTricode']
            home_tri = g['homeTeam']['teamTricode']
            away_team_id = str(g['awayTeam']['teamId'])
            home_team_id = str(g['homeTeam']['teamId'])

            matchup_key = f"{away_tri}@{home_tri}"
            game_date_str = g.get('gameEt', '').split('T')[0]
            odds = odds_map.get((game_date_str, matchup_key), {})

            # Spread line with team abbreviation (favorite carries the negative number).
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

            spread_result = None
            total_result = None
            over_under = odds.get("overUnder")

            if g.get('gameStatus') == 3:
                away_score = g['awayTeam'].get('score', 0)
                home_score = g['homeTeam'].get('score', 0)

                if spread_value is not None:
                    margin = away_score - home_score  # positive = away won
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

            venue = arena_map.get(home_tri, f"{g['homeTeam'].get('teamCity', 'TBD')} Arena")

            away_stats = standings_map.get(away_tri) or standings_map.get(away_team_id, {})
            home_stats = standings_map.get(home_tri) or standings_map.get(home_team_id, {})

            away_full_name = f"{g['awayTeam']['teamCity']} {g['awayTeam']['teamName']}"
            home_full_name = f"{g['homeTeam']['teamCity']} {g['homeTeam']['teamName']}"
            away_advanced = advanced_stats_map.get(away_full_name) or advanced_stats_map.get(away_team_id, {})
            home_advanced = advanced_stats_map.get(home_full_name) or advanced_stats_map.get(home_team_id, {})

            away_wins = g['awayTeam']['wins']
            away_losses = g['awayTeam']['losses']
            away_win_pct = away_wins / (away_wins + away_losses) if (away_wins + away_losses) > 0 else 0.500
            home_wins = g['homeTeam']['wins']
            home_losses = g['homeTeam']['losses']
            home_win_pct = home_wins / (home_wins + home_losses) if (home_wins + home_losses) > 0 else 0.500

            away_ortg = away_advanced.get("offRating", DEFAULT_OFF_RATING)
            away_drtg = away_advanced.get("defRating", DEFAULT_DEF_RATING)
            away_pace = away_advanced.get("pace", DEFAULT_PACE)
            home_ortg = home_advanced.get("offRating", DEFAULT_OFF_RATING)
            home_drtg = home_advanced.get("defRating", DEFAULT_DEF_RATING)
            home_pace = home_advanced.get("pace", DEFAULT_PACE)

            # PACE from the advanced-stats endpoint is normalized per 48 minutes (NBA
            # convention), but WNBA games are 40 minutes — without scaling, naive
            # projections run ~20% hot (e.g. ~200 totals for an ~165-point league).
            game_pace = (away_pace + home_pace) / 2 * (WNBA_REG_MINUTES / 48)
            naive_away = game_pace * (away_ortg + home_drtg) / 2 / 100
            naive_home = game_pace * (home_ortg + away_drtg) / 2 / 100
            naive_margin = naive_home - naive_away

            if spread_value is not None:
                market_margin = spread_value if favorite == "away" else -spread_value
            else:
                market_margin = naive_margin

            blended_margin = (naive_margin * 0.3) + (market_margin * 0.7)
            total_points = naive_away + naive_home
            projected_away = round((total_points - blended_margin) / 2)
            projected_home = round((total_points + blended_margin) / 2)

            # Playoff series context — empty strings during the regular season.
            series_text = (g.get('seriesText') or '').strip()
            series_game_number = g.get('seriesGameNumber') or ''

            refined_games.append({
                "sport": "wnba",
                "gameId": g['gameId'],
                "status": g.get('gameStatusText', ''),
                "gameStatus": g.get('gameStatus', 1),  # 1=scheduled, 2=live, 3=final
                "period": g.get('period', 0),
                "gameClock": g.get('gameClock', ''),
                "time": g.get('gameEt', '').split('T')[-1][:5],
                "date": g.get('gameEt', '').split('T')[0],
                "venue": venue,
                "broadcast": g.get("broadcast") or "Local",
                "model": "EV-MODEL-V1",
                "series": series_text or None,
                "seriesGameNumber": series_game_number or None,
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
                "projectedTotal": round(total_points),
                "away": {
                    "name": g['awayTeam']['teamName'],
                    "abbreviation": away_tri,
                    "espnSlug": _ESPN_SLUG.get(away_tri),
                    "record": f"{g['awayTeam']['wins']}-{g['awayTeam']['losses']}",
                    "winPct": away_win_pct,
                    "seed": away_stats.get("seed", 0),
                    "conference": away_stats.get("conference", "N/A"),
                    "streak": away_stats.get("streak", "N/A"),
                    "last10": away_stats.get("last10", "0-0"),
                    "score": g['awayTeam'].get('score', 0),
                    "predictedScore": projected_away,
                    "winProbability": odds.get("awayWinProbability", 0.5),
                    "offRating": away_advanced.get("offRating", DEFAULT_OFF_RATING),
                    "defRating": away_advanced.get("defRating", DEFAULT_DEF_RATING),
                    "pace": away_advanced.get("pace", DEFAULT_PACE),
                },
                "home": {
                    "name": g['homeTeam']['teamName'],
                    "abbreviation": home_tri,
                    "espnSlug": _ESPN_SLUG.get(home_tri),
                    "record": f"{g['homeTeam']['wins']}-{g['homeTeam']['losses']}",
                    "winPct": home_win_pct,
                    "seed": home_stats.get("seed", 0),
                    "conference": home_stats.get("conference", "N/A"),
                    "streak": home_stats.get("streak", "N/A"),
                    "last10": home_stats.get("last10", "0-0"),
                    "score": g['homeTeam'].get('score', 0),
                    "predictedScore": projected_home,
                    "winProbability": odds.get("homeWinProbability", 0.5),
                    "offRating": home_advanced.get("offRating", DEFAULT_OFF_RATING),
                    "defRating": home_advanced.get("defRating", DEFAULT_DEF_RATING),
                    "pace": home_advanced.get("pace", DEFAULT_PACE),
                }
            })

        has_live_games = any(g.get('gameStatus') == 2 for g in games)
        assembled_ttl = CACHE_TTL_LIVE if has_live_games else CACHE_TTL_DEFAULT
        if has_live_games:
            cache.set_ttl("scoreboard", CACHE_TTL_LIVE)

        cache.set("assembled", refined_games, ttl=assembled_ttl)
        logger.info(f"WNBA response assembled with TTL={assembled_ttl}s (live games: {has_live_games})")
        return refined_games
    except Exception as e:
        logger.error(f"WNBA API Fetch Error: {str(e)}", exc_info=True)
        cached = cache.get_stale("assembled")
        if cached:
            logger.info("Returning stale cached WNBA data due to error")
            return cached
        return []
