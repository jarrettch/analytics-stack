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
CACHE_TTL_LIVE = 20     # 20 seconds during live action
CACHE_TTL_DEFAULT = 60  # 1 minute for pre/post-game

# Independent caches for each data source with appropriate TTLs
caches = {
    "scoreboard": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
    "standings": {"data": None, "last_fetch": 0, "ttl": 300},       # 5 minutes
    "advanced_stats": {"data": None, "last_fetch": 0, "ttl": 600},  # 10 minutes
    "odds": {"data": None, "last_fetch": 0, "ttl": 60},             # 1 minute
    "assembled": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},  # Final response
}

def get_cached(cache_key: str):
    """Get data from cache if still fresh."""
    cache = caches[cache_key]
    current_time = time.time()
    if cache["data"] and (current_time - cache["last_fetch"] < cache["ttl"]):
        return cache["data"]
    return None

def set_cache(cache_key: str, data, ttl: int = None):
    """Update cache with new data."""
    cache = caches[cache_key]
    if data:  # Only cache non-empty data
        cache["data"] = data
        cache["last_fetch"] = time.time()
        if ttl:
            cache["ttl"] = ttl

def get_cached_data(cache_key: str):
    """Get last known good data (even if stale) for fallback."""
    return caches[cache_key]["data"]

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

def calculate_win_probability_from_odds(away_ml, home_ml, away_spread, home_spread):
    """
    Calculate win probabilities from moneyline and spread.
    Priority: ML > Spread > 50/50
    """
    # Try moneyline first (if both are available and not a pick'em)
    if away_ml and home_ml:
        # Check if it's a pick'em (both around -110)
        if abs(away_ml - home_ml) < 20:  # Within 20 units = pick'em
            # Use spread instead
            pass
        else:
            # Convert American odds to probability
            away_prob = abs(away_ml) / (abs(away_ml) + 100) if away_ml < 0 else 100 / (away_ml + 100)
            home_prob = abs(home_ml) / (abs(home_ml) + 100) if home_ml < 0 else 100 / (home_ml + 100)

            # Normalize to remove vig (they won't sum to 1.0)
            total = away_prob + home_prob
            return away_prob / total, home_prob / total

    # Fallback to spread
    if away_spread is not None and home_spread is not None:
        # Use the favorite's spread (negative value)
        spread = away_spread if away_spread < 0 else home_spread
        # Formula: prob = 1 / (1 + 10^(spread/15))
        favorite_prob = 1 / (1 + 10 ** (abs(spread) / 15))
        underdog_prob = 1 - favorite_prob

        if away_spread < 0:  # Away is favorite
            return favorite_prob, underdog_prob
        else:  # Home is favorite
            return underdog_prob, favorite_prob

    # Default to 50/50
    return 0.5, 0.5

async def get_odds_map():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://cdn.nba.com/static/json/liveData/odds/odds_todaysGames.json")
            data = r.json()

        logger.info(f"Odds API returned {len(data.get('games', []))} games")

        odds_map = {}
        for game in data.get("games", []):
            # Use team IDs as the key instead of gameId
            home_team_id = str(game.get("homeTeamId", ""))
            away_team_id = str(game.get("awayTeamId", ""))
            matchup_key = f"{away_team_id}@{home_team_id}"

            away_spread = None
            home_spread = None
            away_ml = None
            home_ml = None

            for market in game.get("markets", []):
                # Extract spread
                if market["name"] == "spread":
                    for book in market.get("books", []):
                        if book.get("countryCode") == "US" and "FanDuel" in book.get("name", ""):
                            for outcome in book.get("outcomes", []):
                                if outcome["type"] == "away":
                                    away_spread = float(outcome.get("spread", 0))
                                elif outcome["type"] == "home":
                                    home_spread = float(outcome.get("spread", 0))

                            break

                # Extract moneyline (2way market)
                elif market["name"] == "2way":
                    for book in market.get("books", []):
                        if book.get("countryCode") == "US" and "FanDuel" in book.get("name", ""):
                            for outcome in book.get("outcomes", []):
                                odds_str = outcome.get("odds", "")
                                try:
                                    # Convert decimal odds to American
                                    decimal_odds = float(odds_str)
                                    if decimal_odds >= 2.0:
                                        american_odds = (decimal_odds - 1) * 100
                                    else:
                                        american_odds = -100 / (decimal_odds - 1)

                                    if outcome["type"] == "away":
                                        away_ml = american_odds
                                    elif outcome["type"] == "home":
                                        home_ml = american_odds
                                except (ValueError, ZeroDivisionError):
                                    pass
                            break

            # Calculate win probabilities
            away_prob, home_prob = calculate_win_probability_from_odds(
                away_ml, home_ml, away_spread, home_spread
            )

            odds_map[matchup_key] = {
                "awaySpread": away_spread,
                "homeSpread": home_spread,
                "awayWinProbability": away_prob,
                "homeWinProbability": home_prob,
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
            "sample_teams": list(result.keys())[:10] if result else [],
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
    # Check if assembled response is still fresh
    cached_response = get_cached("assembled")
    if cached_response:
        return cached_response

    try:
        loop = asyncio.get_event_loop()

        # Fetch each data source, using cache when fresh
        # Scoreboard always needs fresh data for live games
        cached_scoreboard = get_cached("scoreboard")
        cached_standings = get_cached("standings")
        cached_advanced = get_cached("advanced_stats")
        cached_odds = get_cached("odds")

        # Build list of fetches needed
        tasks = []
        fetch_keys = []

        if not cached_scoreboard:
            tasks.append(loop.run_in_executor(None, lambda: scoreboard.ScoreBoard()))
            fetch_keys.append("scoreboard")
        if not cached_standings:
            tasks.append(loop.run_in_executor(None, get_standings_map))
            fetch_keys.append("standings")
        if not cached_advanced:
            tasks.append(loop.run_in_executor(None, get_advanced_stats_map))
            fetch_keys.append("advanced_stats")
        if not cached_odds:
            tasks.append(get_odds_map())
            fetch_keys.append("odds")

        # Fetch only what's needed
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}, using cached data")
                elif key == "scoreboard":
                    set_cache("scoreboard", result)
                else:
                    set_cache(key, result)

        # Get data (fresh or cached, with fallback to stale)
        sb = get_cached("scoreboard") or get_cached_data("scoreboard")
        standings_map = get_cached("standings") or get_cached_data("standings") or {}
        advanced_stats_map = get_cached("advanced_stats") or get_cached_data("advanced_stats") or {}
        odds_map = get_cached("odds") or get_cached_data("odds") or {}

        if not sb:
            logger.error("No scoreboard data available")
            return get_cached_data("assembled") or []

        data = sb.get_dict()
        games = data.get('scoreboard', {}).get('games', [])

        logger.info(f"Processing {len(games)} games (standings: {len(standings_map)}, advanced: {len(advanced_stats_map)}, odds: {len(odds_map)})")

        arena_map = get_arena_map()
        refined_games = []
        for g in games:
            away_tri = g['awayTeam']['teamTricode']
            home_tri = g['homeTeam']['teamTricode']

            # Match odds by team IDs (works when odds API is current)
            away_team_id = str(g['awayTeam']['teamId'])
            home_team_id = str(g['homeTeam']['teamId'])
            matchup_key = f"{away_team_id}@{home_team_id}"
            odds = odds_map.get(matchup_key, {})

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
            if g.get('gameStatus') == 3 and spread_value is not None:
                away_score = g['awayTeam'].get('score', 0)
                home_score = g['homeTeam'].get('score', 0)
                margin = away_score - home_score  # positive = away won

                if favorite == "away":
                    # Away favored: they need to win by more than the spread
                    spread_result = margin > abs(spread_value)
                else:
                    # Home favored: they need to win by more than the spread
                    spread_result = (-margin) > abs(spread_value)

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
            caches["scoreboard"]["ttl"] = CACHE_TTL_LIVE

        set_cache("assembled", refined_games, ttl=assembled_ttl)

        logger.info(f"Response assembled with TTL={assembled_ttl}s (live games: {has_live_games})")
        return refined_games
    except Exception as e:
        logger.error(f"NBA API Fetch Error: {str(e)}", exc_info=True)

        # Fallback to stale cache so the frontend stays hydrated
        cached = get_cached_data("assembled")
        if cached:
            logger.info("Returning stale cached data due to error")
            return cached

        # Only return empty list if we have absolutely zero history
        return []