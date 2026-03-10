import asyncio
from fastapi import APIRouter, HTTPException
from nba_api.live.nba.endpoints import scoreboard, odds
from nba_api.stats.endpoints import leaguestandingsv3, leaguestandings, leaguedashteamstats
import time
import logging
import httpx

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nba", tags=["NBA"])

# Configuration for Dynamic TTL
CACHE_TTL_DEFAULT = 60  # 1 minute for pre/post-game
CACHE_TTL_LIVE = 20     # 20 seconds during live action

cache = {
    "data": None,
    "last_fetch": 0,
    "ttl": CACHE_TTL_DEFAULT
}

# Get the standings map
# def get_standings_map():
#   try:
def get_standings_map():
    try:
        logger.info("Fetching standings (trying v3)...")
        try:
            standings = leaguestandingsv3.LeagueStandingsV3(
                timeout=10,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Referer': 'https://www.nba.com/',
                    'Origin': 'https://www.nba.com',
                    'Connection': 'keep-alive'
                }
            ).get_dict()
        except Exception as v3_error:
            logger.warning(f"LeagueStandingsV3 failed: {v3_error}, trying older endpoint...")
            standings = leaguestandings.LeagueStandings(
                timeout=10,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                    'Accept': 'application/json, text/plain, */*',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Referer': 'https://www.nba.com/',
                    'Origin': 'https://www.nba.com',
                    'Connection': 'keep-alive'
                }
            ).get_dict()
        logger.info(f"Standings fetched successfully, processing {len(standings['resultSets'][0]['rowSet'])} teams")
        headers = standings['resultSets'][0]['headers']
        rows = standings['resultSets'][0]['rowSet']

        # Map headers to indices for safer access
        h = {header: i for i, header in enumerate(headers)}

        stats_map = {}
        for row in rows:
            tricode = row[h['TeamAbbreviation']]
            stats_map[tricode] = {
                "record": f"{row[h['Wins']]}-{row[h['Losses']]}",
                "winPct": row[h['WinPCT']],
                "seed": row[h['PlayoffSeeding']],
                "conference": row[h['Conference']],
                "streak": row[h['strCurrentStreak']],
                "last10": f"{row[h['L10Wins']]}-{row[h['L10Losses']]}"
            }

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

        advanced_map = {}
        for row in rows:
            team_name = row[h['TEAM_NAME']]
            # Map full team name to tricode (we'll need to handle this mapping)
            advanced_map[team_name] = {
                "offRating": row[h['OFF_RATING']],
                "defRating": row[h['DEF_RATING']],
                "netRating": row[h['NET_RATING']],
                "pace": row[h.get('PACE', 0)] if 'PACE' in h else 98.5
            }

        logger.info(f"Built advanced stats map with {len(advanced_map)} teams")
        return advanced_map
    except Exception as e:
        logger.error(f"Advanced Stats Fetch Error: {e}", exc_info=True)
        return {}

async def get_odds_map():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json")
            data = r.json()

        logger.info(f"Odds API returned {len(data.get('games', []))} games")

        # Log the actual gameIds from odds API for debugging
        odds_game_ids = [g.get("gameId") for g in data.get("games", [])]
        logger.info(f"Odds API gameIds: {odds_game_ids[:5]}..." if odds_game_ids else "No gameIds in odds")

        odds_map = {}
        for game in data.get("games", []):
            # Use team IDs as the key instead of gameId (they don't match between APIs)
            home_team_id = str(game.get("homeTeamId", ""))
            away_team_id = str(game.get("awayTeamId", ""))
            matchup_key = f"{away_team_id}@{home_team_id}"

            spread_line = "N/A"

            for market in game.get("markets", []):
                if market["name"] == "spread":
                    for book in market.get("books", []):
                        if book.get("countryCode") == "US" and "FanDuel" in book.get("name", ""):
                            away_spread = None
                            home_spread = None
                            for outcome in book.get("outcomes", []):
                                if outcome["type"] == "away":
                                    away_spread = float(outcome.get("spread", 0))
                                elif outcome["type"] == "home":
                                    home_spread = float(outcome.get("spread", 0))

                            if away_spread is not None and home_spread is not None:
                                if away_spread < 0:
                                    spread_line = f"Away -{abs(away_spread)}"
                                else:
                                    spread_line = f"Home -{abs(home_spread)}"
                            break

            odds_map[matchup_key] = {
                "spreadLine": str(spread_line),
                "oddsGameId": game.get("gameId", "unknown")
            }

        logger.info(f"Odds map created with {len(odds_map)} matchups")
        return odds_map
    except Exception as e:
        logger.error(f"Odds fetch error: {e}")
        return {}

@router.get("/debug/standings")
def debug_standings():
    """Debug endpoint to test standings API directly"""
    try:
        result = get_standings_map()
        return {
            "success": True,
            "team_count": len(result),
            "sample_teams": list(result.keys())[:5] if result else [],
            "sample_data": {k: result[k] for k in list(result.keys())[:2]} if result else {}
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "error_type": type(e).__name__
        }

@router.get("/debug/scoreboard")
def debug_scoreboard():
    """See what data the scoreboard actually provides"""
    try:
        sb = scoreboard.ScoreBoard()
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
    current_time = time.time()
    if cache["data"] and (current_time - cache["last_fetch"] < cache["ttl"]):
        return cache["data"]

    try:
        loop = asyncio.get_event_loop()
        sb, standings_map, advanced_stats_map, odds_map = await asyncio.gather(
            loop.run_in_executor(None, lambda: scoreboard.ScoreBoard()),
            loop.run_in_executor(None, get_standings_map),
            loop.run_in_executor(None, get_advanced_stats_map),
            get_odds_map()
        )

        logger.info(f"Data fetched - Standings: {len(standings_map)} teams, Advanced: {len(advanced_stats_map)} teams, Odds: {len(odds_map)} matchups")

        # Fetch scoreboard and standings simultaneously
        # sb = scoreboard.ScoreBoard()
        # standings_map = get_standings_map() # Fresh standings

        data = sb.get_dict()
        games = data.get('scoreboard', {}).get('games', [])

        logger.info(f"Scoreboard has {len(games)} games")
        if games:
            logger.info(f"Sample scoreboard gameId: {games[0].get('gameId')}")

        refined_games = []
        for g in games:
            logger.info(f"Game keys: {g.keys()}")
            away_tri = g['awayTeam']['teamTricode']
            home_tri = g['homeTeam']['teamTricode']

            # Match odds by team IDs (works when odds API is current)
            away_team_id = str(g['awayTeam']['teamId'])
            home_team_id = str(g['homeTeam']['teamId'])
            matchup_key = f"{away_team_id}@{home_team_id}"
            odds = odds_map.get(matchup_key, {})
            
            # Get real stats or use your existing defaults as fallbacks
            away_stats = standings_map.get(away_tri, {})
            home_stats = standings_map.get(home_tri, {})

            # Get advanced stats - try to find by full team name
            away_full_name = f"{g['awayTeam']['teamCity']} {g['awayTeam']['teamName']}"
            home_full_name = f"{g['homeTeam']['teamCity']} {g['homeTeam']['teamName']}"
            away_advanced = advanced_stats_map.get(away_full_name, {})
            home_advanced = advanced_stats_map.get(home_full_name, {})

            # Calculate win percentage from scoreboard data
            away_wins = g['awayTeam']['wins']
            away_losses = g['awayTeam']['losses']
            away_win_pct = away_wins / (away_wins + away_losses) if (away_wins + away_losses) > 0 else 0.500

            home_wins = g['homeTeam']['wins']
            home_losses = g['homeTeam']['losses']
            home_win_pct = home_wins / (home_wins + home_losses) if (home_wins + home_losses) > 0 else 0.500

            refined_games.append({
                "sport": "nba",
                "gameId": g['gameId'],
                "status": g.get('gameStatusText', ''),
                "time": g.get('gameEt', '').split('T')[-1][:5], # Extract HH:MM
                "date": g.get('gameEt', '').split('T')[0],
                # "venue": f"{g['arenaName']}, {g['arenaCity']}",
                # "venue": f"{g.get('arenaName', g.get('arena', {}).get('arenaName', 'TBD'))}, {g.get('arenaCity', '')}",
                "venue": f"{g['homeTeam'].get('teamCity', 'TBD')} Arena",
                "broadcast": "Local", 
                "model": "EV-MODEL-V1",
                "spreadLine": odds.get("spreadLine", "N/A"),
                "away": {
                    "name": g['awayTeam']['teamName'],
                    "abbreviation": away_tri,
                    "record": f"{g['awayTeam']['wins']}-{g['awayTeam']['losses']}",
                    "winPct": away_win_pct,
                    "seed": away_stats.get("seed", 0),
                    "conference": away_stats.get("conference", "N/A"),
                    "streak": away_stats.get("streak", "N/A"),
                    "last10": away_stats.get("last10", "0-0"),
                    # Keep your model's predicted stats here
                    "predictedScore": 110,
                    "winProbability": 0.5,
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
                    "predictedScore": 115,
                    "winProbability": 0.5,
                    "offRating": home_advanced.get("offRating", 115.1),
                    "defRating": home_advanced.get("defRating", 113.4),
                    "pace": home_advanced.get("pace", 98.5)
                }
            })
        
        cache["data"] = refined_games
        cache["last_fetch"] = current_time
        return refined_games
    except Exception as e:
        # ... your existing error handling
        logger.error(f"NBA API Fetch Error: {str(e)}", exc_info=True)
        # logger.error(f"NBA API Fetch Error: {str(e)}")
        
        # 4. CRITICAL: Fallback to stale cache so the frontend stays hydrated
        if cache["data"]:
            return cache["data"]
        
        # Only return empty list if we have absolutely zero history
        return []