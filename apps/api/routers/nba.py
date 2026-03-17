import asyncio
import os
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
from fastapi import APIRouter, HTTPException
from nba_api.live.nba.endpoints import scoreboard
from nba_api.stats.endpoints import leaguestandingsv3, leaguestandings, leaguedashteamstats
import time
import logging
import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# Supabase client for odds persistence
supabase = None
if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Setup basic logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nba", tags=["NBA"])

# Configuration for Dynamic TTL
CACHE_TTL_LIVE = 20     # 20 seconds during live action
CACHE_TTL_DEFAULT = 60  # 1 minute for pre/post-game
CACHE_TTL_ODDS = 1800   # 30 minutes - The Odds API free tier is 500 req/month (~16/day)

# Independent caches for each data source with appropriate TTLs
caches = {
    "scoreboard": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
    "standings": {"data": None, "last_fetch": 0, "ttl": 300},       # 5 minutes
    "advanced_stats": {"data": None, "last_fetch": 0, "ttl": 600},  # 10 minutes
    "odds": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_ODDS}, # 30 minutes (rate limited)
    "assembled": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
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
        # Formula: underdog_prob = 1 / (1 + 10^(|spread|/15)), favorite_prob = 1 - underdog_prob
        underdog_prob = 1 / (1 + 10 ** (abs(spread) / 15))
        favorite_prob = 1 - underdog_prob

        if away_spread < 0:  # Away is favorite
            return favorite_prob, underdog_prob
        else:  # Home is favorite
            return underdog_prob, favorite_prob

    # Default to 50/50
    return 0.5, 0.5

def get_team_tricode_from_name(team_name: str) -> str:
    """Map full team name to tricode for matching with scoreboard."""
    name_map = {
        "Atlanta Hawks": "ATL", "Boston Celtics": "BOS", "Brooklyn Nets": "BKN",
        "Charlotte Hornets": "CHA", "Chicago Bulls": "CHI", "Cleveland Cavaliers": "CLE",
        "Dallas Mavericks": "DAL", "Denver Nuggets": "DEN", "Detroit Pistons": "DET",
        "Golden State Warriors": "GSW", "Houston Rockets": "HOU", "Indiana Pacers": "IND",
        "Los Angeles Clippers": "LAC", "LA Clippers": "LAC",
        "Los Angeles Lakers": "LAL", "LA Lakers": "LAL",
        "Memphis Grizzlies": "MEM", "Miami Heat": "MIA", "Milwaukee Bucks": "MIL",
        "Minnesota Timberwolves": "MIN", "New Orleans Pelicans": "NOP",
        "New York Knicks": "NYK", "Oklahoma City Thunder": "OKC", "Orlando Magic": "ORL",
        "Philadelphia 76ers": "PHI", "Phoenix Suns": "PHX", "Portland Trail Blazers": "POR",
        "Sacramento Kings": "SAC", "San Antonio Spurs": "SAS", "Toronto Raptors": "TOR",
        "Utah Jazz": "UTA", "Washington Wizards": "WAS",
    }
    return name_map.get(team_name, team_name[:3].upper())

def load_persisted_odds(game_date: date = None) -> dict:
    """Load persisted odds from Supabase for a given date.

    When no date is specified, queries today AND tomorrow (UTC) to handle the
    ET/UTC boundary: a 7pm ET game has a UTC commence_time of midnight+, so it
    gets saved with tomorrow's UTC date on the Render server.
    """
    if not supabase:
        logger.warning("Supabase not configured, skipping odds load")
        return {}

    if game_date:
        dates_to_query = [game_date]
    else:
        today_et = datetime.now(ET).date()
        dates_to_query = [today_et, today_et + timedelta(days=1)]

    odds_map = {}
    for d in dates_to_query:
        try:
            response = supabase.table("nba_odds").select("matchup_key, odds_data").eq("game_date", d.isoformat()).execute()
            for row in response.data:
                odds_map[row["matchup_key"]] = row["odds_data"]
        except Exception as e:
            logger.warning(f"Failed to load persisted odds for {d}: {e}")
    logger.info(f"Loaded {len(odds_map)} odds from Supabase for {[d.isoformat() for d in dates_to_query]}")
    return odds_map

def save_persisted_odds(odds_map: dict, game_date: date = None):
    """Save odds to Supabase (upserts by game_date + matchup_key)."""
    if not supabase:
        logger.warning("Supabase not configured, skipping odds save")
        return

    target_date = game_date or date.today()
    try:
        rows = [
            {
                "game_date": target_date.isoformat(),
                "matchup_key": matchup_key,
                "odds_data": odds_data,
            }
            for matchup_key, odds_data in odds_map.items()
        ]
        # Upsert: insert or update on conflict
        supabase.table("nba_odds").upsert(rows, on_conflict="game_date,matchup_key").execute()
        logger.info(f"Persisted {len(rows)} matchup odds to Supabase for {target_date}")
    except Exception as e:
        logger.warning(f"Failed to persist odds: {e}")

async def get_odds_map():
    """Fetch odds from The Odds API (includes spreads, totals, moneylines).

    Merges fresh odds with persisted odds so finished games retain their lines.
    """
    # Start with persisted odds (keeps data for finished games)
    odds_map = load_persisted_odds()

    api_key = os.getenv("ODDS_API_KEY")
    if not api_key:
        logger.warning("ODDS_API_KEY not set, returning persisted odds only")
        return odds_map, 0

    try:
        url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds/"
        params = {
            "apiKey": api_key,
            "regions": "us",
            "markets": "spreads,totals,h2h",
            "oddsFormat": "american",
        }

        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            games = r.json()

            # Log remaining requests (in headers)
            remaining = r.headers.get("x-requests-remaining", "?")
            logger.info(f"Odds API: {len(games)} upcoming games, {remaining} requests remaining")

        fresh_odds = {}
        odds_by_date = {}  # Group odds by game date for saving
        for game in games:
            away_team = game.get("away_team", "")
            home_team = game.get("home_team", "")
            away_tri = get_team_tricode_from_name(away_team)
            home_tri = get_team_tricode_from_name(home_team)
            matchup_key = f"{away_tri}@{home_tri}"

            # Parse game date from commence_time in ET so late-evening games
            # (e.g., 8pm ET = midnight UTC) aren't assigned the next UTC date.
            commence_time = game.get("commence_time", "")
            try:
                game_date = datetime.fromisoformat(commence_time.replace("Z", "+00:00")).astimezone(ET).date()
            except:
                game_date = datetime.now(ET).date()

            away_spread = None
            home_spread = None
            away_spread_odds = None
            home_spread_odds = None
            away_ml = None
            home_ml = None
            over_under = None
            over_odds = None
            under_odds = None

            # Find BetOnline first (sharper lines), fallback to first available book
            bookmakers = game.get("bookmakers", [])
            preferred_book = None
            for book in bookmakers:
                if book.get("key") == "betonlineag":
                    preferred_book = book
                    break
            if not preferred_book and bookmakers:
                preferred_book = bookmakers[0]

            if preferred_book:
                for market in preferred_book.get("markets", []):
                    market_key = market.get("key")

                    if market_key == "spreads":
                        for outcome in market.get("outcomes", []):
                            if outcome.get("name") == away_team:
                                away_spread = outcome.get("point")
                                away_spread_odds = outcome.get("price")
                            elif outcome.get("name") == home_team:
                                home_spread = outcome.get("point")
                                home_spread_odds = outcome.get("price")

                    elif market_key == "totals":
                        for outcome in market.get("outcomes", []):
                            if outcome.get("name") == "Over":
                                over_under = outcome.get("point")
                                over_odds = outcome.get("price")
                            elif outcome.get("name") == "Under":
                                under_odds = outcome.get("price")

                    elif market_key == "h2h":
                        for outcome in market.get("outcomes", []):
                            if outcome.get("name") == away_team:
                                away_ml = outcome.get("price")
                            elif outcome.get("name") == home_team:
                                home_ml = outcome.get("price")

            # Calculate win probabilities
            away_prob, home_prob = calculate_win_probability_from_odds(
                away_ml, home_ml, away_spread, home_spread
            )

            odds_data = {
                "awaySpread": away_spread,
                "homeSpread": home_spread,
                "awaySpreadOdds": away_spread_odds,
                "homeSpreadOdds": home_spread_odds,
                "overUnder": over_under,
                "overOdds": over_odds,
                "underOdds": under_odds,
                "awayMoneyline": away_ml,
                "homeMoneyline": home_ml,
                "awayWinProbability": away_prob,
                "homeWinProbability": home_prob,
            }
            fresh_odds[matchup_key] = odds_data

            # Group by date for saving
            if game_date not in odds_by_date:
                odds_by_date[game_date] = {}
            odds_by_date[game_date][matchup_key] = odds_data

        # Merge fresh odds into persisted (fresh overwrites)
        odds_map.update(fresh_odds)

        # Save odds grouped by their actual game date
        for gd, gd_odds in odds_by_date.items():
            save_persisted_odds(gd_odds, game_date=gd)

        logger.info(f"Odds map: {len(fresh_odds)} fresh + {len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total")
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"Odds API HTTP error: {e.response.status_code} - {e.response.text}")
        return odds_map, 0
    except Exception as e:
        logger.error(f"Odds fetch error: {e}")
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
async def cron_fetch_odds():
    """Endpoint for GitHub Actions to trigger daily odds fetch."""
    try:
        odds, fresh_count = await get_odds_map()
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:nba_odds",
            "date": datetime.now(ET).date().isoformat(),
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
            tasks.append(loop.run_in_executor(None, load_persisted_odds))
            fetch_keys.append("odds")

        # Fetch only what's needed
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}, using cached data")
                elif key == "scoreboard":
                    set_cache("scoreboard", result)
                elif key == "odds":
                    set_cache("odds", result)
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
        # Persisted odds are the closing lines — use these for live/final games
        # so CLV calculations aren't contaminated by live line movement.
        persisted_odds_map = load_persisted_odds()

        refined_games = []
        for g in games:
            away_tri = g['awayTeam']['teamTricode']
            home_tri = g['homeTeam']['teamTricode']
            away_team_id = str(g['awayTeam']['teamId'])
            home_team_id = str(g['homeTeam']['teamId'])

            # Match odds by tricode (The Odds API uses team names -> tricodes)
            matchup_key = f"{away_tri}@{home_tri}"
            game_status = g.get('gameStatus', 1)
            # For live/final games use persisted (closing) odds; for scheduled use merged map
            if game_status >= 2:
                odds = persisted_odds_map.get(matchup_key, odds_map.get(matchup_key, {}))
            else:
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