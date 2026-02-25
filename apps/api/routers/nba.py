from fastapi import APIRouter, HTTPException
from nba_api.live.nba.endpoints import scoreboard
import time
import logging

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

@router.get("/today")
async def get_todays_games():
    current_time = time.time()

    # 1. Return cached data if still fresh
    if cache["data"] and (current_time - cache["last_fetch"] < cache["ttl"]):
        return cache["data"]

    try:
        # 2. Fetch fresh data
        sb = scoreboard.ScoreBoard()
        data = sb.get_dict()
        games = data.get('scoreboard', {}).get('games', [])
        
        refined_games = []
        is_any_game_live = False

        for g in games:
            status_text = g.get('gameStatusText', '')
            
            # Detect live games: status contains "Q", "OT", or "Half"
            if any(indicator in status_text for indicator in ["Q", "OT", "Half"]):
                is_any_game_live = True

            # Safer Broadcaster extraction for nested lists
            broadcasters = g.get('broadcasters', {})
            national_list = broadcasters.get('nationalTvBroadcasters', [])
            tv_channel = national_list[0].get('broadcasterDisplay', 'Local') if national_list else 'Local'

            refined_games.append({
                "id": g['gameId'],
                "status": status_text,
                "time": g.get('gameEt', ''),
                "date": "Feb 24", # You can derive this from gameEt
                "venue": "NBA Arena",
                "broadcast": tv_channel,
                "model": "EV-MODEL-V1",
                "spreadLine": "TBD",
                "overUnder": 220.5,
                "away": {
                    "name": g['awayTeam']['teamName'],
                    "abbreviation": g['awayTeam']['teamTricode'],
                    "record": f"{g['awayTeam']['wins']}-{g['awayTeam']['losses']}",
                    "winPct": 0.500,
                    "seed": 1,
                    "conference": "West",
                    "streak": "L1",
                    "last10": "5-5",
                    "predictedScore": 110,
                    "winProbability": 0.5,
                    "offRating": 112.5,
                    "defRating": 110.2,
                    "pace": 98.5
                },
                "home": {
                    "name": g['homeTeam']['teamName'],
                    "abbreviation": g['homeTeam']['teamTricode'],
                    "record": f"{g['homeTeam']['wins']}-{g['homeTeam']['losses']}",
                    "winPct": 0.500,
                    "seed": 2,
                    "conference": "West",
                    "streak": "W2",
                    "last10": "6-4",
                    "predictedScore": 115,
                    "winProbability": 0.5,
                    "offRating": 115.1,
                    "defRating": 113.4,
                    "pace": 98.5
                }
            })
        
        # 3. Update cache and dynamic TTL
        cache["data"] = refined_games
        cache["last_fetch"] = current_time
        cache["ttl"] = CACHE_TTL_LIVE if is_any_game_live else CACHE_TTL_DEFAULT
        
        return refined_games

    except Exception as e:
        logger.error(f"NBA API Fetch Error: {str(e)}")
        
        # 4. CRITICAL: Fallback to stale cache so the frontend stays hydrated
        if cache["data"]:
            return cache["data"]
        
        # Only return empty list if we have absolutely zero history
        return []