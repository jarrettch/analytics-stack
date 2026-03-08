import asyncio
from fastapi import APIRouter, HTTPException
from nba_api.live.nba.endpoints import scoreboard, odds
from nba_api.stats.endpoints import leaguestandingsv3
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
        logger.info("Fetching standings...")
        standings = leaguestandingsv3.LeagueStandingsV3(
            timeout=10,
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://www.nba.com/',
                'Origin': 'https://www.nba.com'
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
        return {}

async def get_odds_map():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json")
            data = r.json()

        logger.info(f"Odds API returned {len(data.get('games', []))} games")
        odds_map = {}
        for game in data.get("games", []):
            game_id = game["gameId"]
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
            
            odds_map[game_id] = {"spreadLine": str(spread_line)}

        logger.info(f"Odds map keys: {list(odds_map.keys())[:5]}...")  # Show first 5 gameIds
        return odds_map
    except Exception as e:
        logger.error(f"Odds fetch error: {e}")
        return {}

@router.get("/today")
async def get_todays_games():
    current_time = time.time()
    if cache["data"] and (current_time - cache["last_fetch"] < cache["ttl"]):
        return cache["data"]

    try:
        loop = asyncio.get_event_loop()
        sb, standings_map, odds_map = await asyncio.gather(
            loop.run_in_executor(None, lambda: scoreboard.ScoreBoard()),
            loop.run_in_executor(None, get_standings_map),
            get_odds_map()
        )

        logger.info(f"Data fetched - Standings: {len(standings_map)} teams, Odds: {len(odds_map)} games")

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

            game_id = g['gameId']
            odds = odds_map.get(game_id, {})
            if not odds:
                logger.warning(f"No odds found for gameId: {game_id}")
            
            # Get real stats or use your existing defaults as fallbacks
            away_stats = standings_map.get(away_tri, {})
            home_stats = standings_map.get(home_tri, {})

            refined_games.append({
                "id": g['gameId'],
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
                    "winPct": away_stats.get("winPct", 0.500),
                    "seed": away_stats.get("seed", 0),
                    "conference": away_stats.get("conference", "N/A"),
                    "streak": away_stats.get("streak", "N/A"),
                    "last10": away_stats.get("last10", "0-0"),
                    # Keep your model's predicted stats here
                    "predictedScore": 110, 
                    "winProbability": 0.5,
                    "offRating": 112.5,
                    "defRating": 110.2,
                    "pace": 98.5
                },
                "home": {
                    "name": g['homeTeam']['teamName'],
                    "abbreviation": home_tri,
                    "record": f"{g['homeTeam']['wins']}-{g['homeTeam']['losses']}",
                    "winPct": home_stats.get("winPct", 0.500),
                    "seed": home_stats.get("seed", 0),
                    "conference": home_stats.get("conference", "N/A"),
                    "streak": home_stats.get("streak", "N/A"),
                    "last10": home_stats.get("last10", "0-0"),
                    "predictedScore": 115,
                    "winProbability": 0.5,
                    "offRating": 115.1,
                    "defRating": 113.4,
                    "pace": 98.5
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