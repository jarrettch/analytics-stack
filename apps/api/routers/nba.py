from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter
from nba_api.live.nba.endpoints import scoreboard
from nba_api.stats.endpoints import leaguestandingsv3, leaguedashteamstats

from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    UTC,
    Cache,
    calculate_win_probability_from_odds,
    get_http_client,
    load_persisted_odds,
    save_persisted_odds,
    sgo_book_val,
    to_num,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# NBA's CDN (Akamai) 403s requests without a www.nba.com Referer, regardless of
# User-Agent. nba_api's bundled headers don't set one, so override them here.
NBA_LIVE_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Host": "cdn.nba.com",
    "Origin": "https://www.nba.com",
    "Referer": "https://www.nba.com/",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}

router = APIRouter(prefix="/api/nba", tags=["NBA"])

cache = Cache({
    "scoreboard":     CACHE_TTL_DEFAULT,
    "standings":      300,
    "advanced_stats": 600,
    "odds":           CACHE_TTL_ODDS,
    "assembled":      CACHE_TTL_DEFAULT,
})

# Get the standings map
# def get_standings_map():
#   try:
def get_standings_map():
    try:
        logger.info("Fetching standings (v3)...")
        # Simple call without custom headers - matches working debug endpoint
        standings = leaguestandingsv3.LeagueStandingsV3(timeout=10).get_dict()
        logger.info(f"Standings fetched successfully, processing {len(standings['resultSets'][0]['rowSet'])} teams")
        headers = standings['resultSets'][0]['headers']
        rows = standings['resultSets'][0]['rowSet']

        # Map headers to indices for safer access
        h = {header: i for i, header in enumerate(headers)}

        # Map team name to tricode
        name_to_tricode = {
            "Hawks": "ATL", "Celtics": "BOS", "Nets": "BKN", "Hornets": "CHA",
            "Bulls": "CHI", "Cavaliers": "CLE", "Mavericks": "DAL", "Nuggets": "DEN",
            "Pistons": "DET", "Warriors": "GSW", "Rockets": "HOU", "Pacers": "IND",
            "Clippers": "LAC", "Lakers": "LAL", "Grizzlies": "MEM", "Heat": "MIA",
            "Bucks": "MIL", "Timberwolves": "MIN", "Pelicans": "NOP", "Knicks": "NYK",
            "Thunder": "OKC", "Magic": "ORL", "76ers": "PHI", "Suns": "PHX",
            "Trail Blazers": "POR", "Kings": "SAC", "Spurs": "SAS", "Raptors": "TOR",
            "Jazz": "UTA", "Wizards": "WAS",
        }

        stats_map = {}
        for row in rows:
            team_id = str(row[h['TeamID']]) if 'TeamID' in h else None
            team_name = row[h['TeamName']] if 'TeamName' in h else None
            tricode = name_to_tricode.get(team_name) if team_name else None

            # Both v3 and legacy use uppercase field names
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
                "last10": last10 if last10 else "N/A"
            }

            # Store by tricode and team ID for flexible lookup
            if tricode:
                stats_map[tricode] = team_data
            if team_id:
                stats_map[team_id] = team_data

        logger.info(f"Built standings map with {len(stats_map)} teams")
        return stats_map
    except Exception as e:
        logger.error(f"Standings Fetch Error: {e}", exc_info=True)
        logger.error(f"Error type: {type(e).__name__}")
        logger.error(f"Error details: {str(e)}")
        return {}

def get_advanced_stats_map():
    try:
        logger.info("Fetching advanced team stats...")
        stats = leaguedashteamstats.LeagueDashTeamStats(
            measure_type_detailed_defense='Advanced',
            per_mode_detailed='PerGame',
            timeout=15
        )

        result = stats.get_dict()
        headers = result['resultSets'][0]['headers']
        rows = result['resultSets'][0]['rowSet']

        logger.info(f"Advanced stats fetched successfully for {len(rows)} teams")

        # Map headers to indices
        h = {header: i for i, header in enumerate(headers)}

        # Map by both team name AND team ID for reliability
        advanced_map = {}
        for row in rows:
            team_name = row[h['TEAM_NAME']]
            team_id = str(row[h['TEAM_ID']])

            stats = {
                "offRating": row[h['OFF_RATING']],
                "defRating": row[h['DEF_RATING']],
                "netRating": row[h['NET_RATING']],
                "pace": row[h.get('PACE', 0)] if 'PACE' in h else 98.5
            }

            # Store by both name and ID for flexible lookup
            advanced_map[team_name] = stats
            advanced_map[team_id] = stats

        logger.info(f"Built advanced stats map with {len(rows)} teams")
        # Debug: log some team names to verify format
        team_names = [row[h['TEAM_NAME']] for row in rows[:3]]
        logger.info(f"Sample team names: {team_names}")
        return advanced_map
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

async def get_odds_map(target_date: date | None = None):
    """Fetch odds from SportsGameOdds API (spreads, totals, moneylines).

    Prefers Circa lines; falls back to consensus bookOdds.
    Merges fresh odds with persisted odds so finished games retain their lines.
    """
    odds_map = load_persisted_odds()

    api_key = os.getenv("SPORTS_GAME_ODDS_KEY")
    if not api_key:
        logger.warning("SPORTS_GAME_ODDS_KEY not set, returning persisted odds only")
        return odds_map, 0

    try:
        # Scope to a single ET day's window so SGO only returns that day's slate.
        # Without these filters, SGO returns up to `limit` upcoming events
        # spanning multiple days, billing entities for games we don't need.
        target = target_date or datetime.now(ET).date()
        starts_after_utc = datetime.combine(target, datetime.min.time(), ET).astimezone(UTC)
        starts_before_utc = datetime.combine(target + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)

        url = "https://api.sportsgameodds.com/v2/events"
        params = {
            "leagueID": "NBA",
            "oddsAvailable": "true",
            "startsAfter": starts_after_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "startsBefore": starts_before_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "oddID": ",".join([
                "points-home-game-ml-home", "points-away-game-ml-away",
                "points-home-game-sp-home", "points-away-game-sp-away",
                "points-all-game-ou-over",  "points-all-game-ou-under",
            ]),
            "limit": 20,
        }
        headers = {"x-api-key": api_key}

        client = get_http_client()
        r = await client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

        games = data.get("data", [])
        logger.info(f"SportsGameOdds: {len(games)} upcoming NBA games")
        if games:
            logger.info(f"Sample team IDs: {games[0].get('teams', {})}")

        fresh_odds = {}
        odds_by_date = {}

        for game in games:
            teams = game.get("teams", {})
            home_team_id = teams.get("home", {}).get("teamID", "")
            away_team_id = teams.get("away", {}).get("teamID", "")

            home_tri = get_tricode_from_sgo_team_id(home_team_id)
            away_tri = get_tricode_from_sgo_team_id(away_team_id)

            if not home_tri or not away_tri:
                logger.warning(
                    f"Skipping SGO event {game.get('eventID', '?')} — "
                    f"home_team_id={home_team_id!r}, away_team_id={away_team_id!r}, "
                    f"teams_raw={teams!r}"
                )
                continue

            matchup_key = f"{away_tri}@{home_tri}"

            starts_at = game.get("status", {}).get("startsAt", "")
            try:
                game_date = datetime.fromisoformat(starts_at.replace("Z", "+00:00")).astimezone(ET).date()
            except Exception:
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

            # Use fairOdds (vig already removed by SGO) for win probability;
            # fall back to bookOdds if unavailable.
            away_fair_ml = to_num(odds.get("points-away-game-ml-away", {}).get("fairOdds"))
            home_fair_ml = to_num(odds.get("points-home-game-ml-home", {}).get("fairOdds"))

            # Preserve non-null spread/O-U from persisted when the fresh API has null.
            # Lines are often unavailable early in the day or temporarily off-board,
            # and we don't want to wipe out yesterday's data in those cases.
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

            if game_date not in odds_by_date:
                odds_by_date[game_date] = {}
            odds_by_date[game_date][matchup_key] = odds_data

        odds_map.update(fresh_odds)

        for gd, gd_odds in odds_by_date.items():
            save_persisted_odds(gd_odds, game_date=gd)

        logger.info(f"Odds map: {len(fresh_odds)} fresh + {len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total")
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"SportsGameOdds HTTP error: {e.response.status_code} - {e.response.text}")
        return odds_map, 0
    except Exception as e:
        logger.error(f"SportsGameOdds fetch error: {e}")
        return odds_map, 0

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

@router.get("/cron/fetch-odds")
async def cron_fetch_odds(offset: int = 0):
    """Endpoint for GitHub Actions to trigger daily odds fetch.

    `offset` selects which ET day to fetch: 0 = today (default), 1 = tomorrow.
    Used by the 2am ET workflow to pre-fetch tomorrow's opening lines so the
    UI has data when it rolls the slate forward after the last game ends.
    """
    try:
        target = datetime.now(ET).date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(target_date=target)
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
    """See what data the scoreboard actually provides"""
    try:
        sb = scoreboard.ScoreBoard(headers=NBA_LIVE_HEADERS)
        data = sb.get_dict()
        games = data.get('scoreboard', {}).get('games', [])

        if games:
            # Return first game's full structure
            first_game = games[0]
            return {
                "success": True,
                "game_count": len(games),
                "first_game_keys": list(first_game.keys()),
                "first_game_id": first_game.get('gameId'),
                "game_ids": [g.get('gameId') for g in games],
                "away_team_data": first_game.get('awayTeam', {}),
                "home_team_data": first_game.get('homeTeam', {}),
                "pbOdds": first_game.get('pbOdds', {})  # Check if odds are in scoreboard
            }
        return {"success": True, "message": "No games today"}
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

        # Fetch each data source, using cache when fresh
        # Scoreboard always needs fresh data for live games
        cached_scoreboard = cache.get("scoreboard")
        cached_standings = cache.get("standings")
        cached_advanced = cache.get("advanced_stats")
        cached_odds = cache.get("odds")

        # Build list of fetches needed
        tasks = []
        fetch_keys = []

        if not cached_scoreboard:
            tasks.append(loop.run_in_executor(None, lambda: scoreboard.ScoreBoard(headers=NBA_LIVE_HEADERS)))
            fetch_keys.append("scoreboard")
        if not cached_standings:
            tasks.append(loop.run_in_executor(None, get_standings_map))
            fetch_keys.append("standings")
        if not cached_advanced:
            tasks.append(loop.run_in_executor(None, get_advanced_stats_map))
            fetch_keys.append("advanced_stats")
        if not cached_odds:
            tasks.append(loop.run_in_executor(None, load_persisted_odds))
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
        sb = cache.get("scoreboard") or cache.get_stale("scoreboard")
        standings_map = cache.get("standings") or cache.get_stale("standings") or {}
        advanced_stats_map = cache.get("advanced_stats") or cache.get_stale("advanced_stats") or {}
        odds_map = cache.get("odds") or cache.get_stale("odds") or {}

        if not sb:
            logger.error("No scoreboard data available")
            return cache.get_stale("assembled") or []

        data = sb.get_dict()
        games = data.get('scoreboard', {}).get('games', [])

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
            game_date_str = g.get('gameEt', '').split('T')[0]
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
            spread_result = None  # None = not final or no spread, True = covered, False = not covered
            total_result = None   # None = not final or no O/U, "over" = hit over, "under" = hit under
            over_under = odds.get("overUnder")

            if g.get('gameStatus') == 3:
                away_score = g['awayTeam'].get('score', 0)
                home_score = g['homeTeam'].get('score', 0)

                # Spread result
                if spread_value is not None:
                    margin = away_score - home_score  # positive = away won
                    if favorite == "away":
                        spread_result = margin > abs(spread_value)
                    else:
                        spread_result = (-margin) > abs(spread_value)

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
            away_advanced = advanced_stats_map.get(away_full_name) or advanced_stats_map.get(str(g['awayTeam']['teamId']), {})
            home_advanced = advanced_stats_map.get(home_full_name) or advanced_stats_map.get(str(g['homeTeam']['teamId']), {})

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

            refined_games.append({
                "sport": "nba",
                "gameId": g['gameId'],
                "status": g.get('gameStatusText', ''),
                "gameStatus": g.get('gameStatus', 1),  # 1=scheduled, 2=live, 3=final
                "period": g.get('period', 0),
                "gameClock": g.get('gameClock', ''),
                "time": g.get('gameEt', '').split('T')[-1][:5],  # Extract HH:MM
                "date": g.get('gameEt', '').split('T')[0],
                "venue": venue,
                "broadcast": "Local",
                "model": "EV-MODEL-V1",
                "spreadLine": spread_line,
                "spreadResult": spread_result,  # True = favorite covered, False = didn't cover, None = not final
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