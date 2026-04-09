import asyncio
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
from fastapi import APIRouter
import time
import logging
import httpx
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

supabase = None
if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
    supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mlb", tags=["MLB"])

CACHE_TTL_LIVE    = 20
CACHE_TTL_DEFAULT = 60
CACHE_TTL_ODDS    = 1800

caches = {
    "mlb_schedule":  {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
    "mlb_standings": {"data": None, "last_fetch": 0, "ttl": 300},
    "mlb_stats":     {"data": None, "last_fetch": 0, "ttl": 600},
    "mlb_odds":      {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_ODDS},
    "mlb_assembled": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
}

def get_cached(cache_key: str):
    cache = caches[cache_key]
    if cache["data"] and (time.time() - cache["last_fetch"] < cache["ttl"]):
        return cache["data"]
    return None

def set_cache(cache_key: str, data, ttl: int = None):
    cache = caches[cache_key]
    if data:
        cache["data"] = data
        cache["last_fetch"] = time.time()
        if ttl:
            cache["ttl"] = ttl

def get_cached_data(cache_key: str):
    return caches[cache_key]["data"]

# MLB Stats API team ID → tricode
def _implied_prob(odds: float) -> float:
    return -odds / (-odds + 100) if odds < 0 else 100 / (odds + 100)

def _de_vig_ml(home_ml: float, away_ml: float):
    raw = _implied_prob(home_ml) + _implied_prob(away_ml)
    return _implied_prob(home_ml) / raw, _implied_prob(away_ml) / raw

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

def _sgo_book_val(odds: dict, odd_key: str, field: str):
    """Extract value from SGO odds object. Prefers Circa; falls back to consensus."""
    odd = odds.get(odd_key, {})
    circa = odd.get("byBookmaker", {}).get("circa", {})
    if circa.get("available"):
        return circa.get(field)
    fallback_key = {"odds": "bookOdds", "spread": "bookSpread", "overUnder": "bookOverUnder"}.get(field, "bookOdds")
    return odd.get(fallback_key)

def _to_num(val):
    if val is None:
        return None
    try:
        f = float(val)
        return int(f) if f == int(f) else f
    except (ValueError, TypeError):
        return None

def _format_et_time(utc_str: str) -> str:
    """Convert UTC ISO string to '7:05 PM ET' display format."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%-I:%M %p ET")
    except Exception:
        return ""

def _local_time_hhmm(utc_str: str) -> str:
    """Convert UTC ISO string to 'HH:MM' in ET."""
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%H:%M")
    except Exception:
        return ""

def load_persisted_odds(game_date: date = None) -> dict:
    """Load persisted MLB odds from Supabase (reuses nba_odds table)."""
    if not supabase:
        logger.warning("Supabase not configured, skipping odds load")
        return {}

    if game_date:
        dates_to_query = [game_date]
    else:
        today_et = datetime.now(ET).date()
        dates_to_query = [today_et - timedelta(days=1), today_et, today_et + timedelta(days=1)]

    odds_map = {}
    for d in dates_to_query:
        try:
            response = supabase.table("nba_odds").select("matchup_key, odds_data").eq("game_date", d.isoformat()).execute()
            for row in response.data:
                odds_map[(d.isoformat(), row["matchup_key"])] = row["odds_data"]
        except Exception as e:
            logger.warning(f"Failed to load persisted MLB odds for {d}: {e}")
    logger.info(f"Loaded {len(odds_map)} MLB odds from Supabase for {[d.isoformat() for d in dates_to_query]}")
    return odds_map

def save_persisted_odds(odds_map: dict, game_date: date = None):
    """Save MLB odds to Supabase (reuses nba_odds table; MLB keys won't collide with NBA)."""
    if not supabase:
        return

    target_date = game_date or date.today()
    try:
        rows = [
            {"game_date": target_date.isoformat(), "matchup_key": matchup_key, "odds_data": odds_data}
            for matchup_key, odds_data in odds_map.items()
        ]
        supabase.table("nba_odds").upsert(rows, on_conflict="game_date,matchup_key").execute()
        logger.info(f"Persisted {len(rows)} MLB matchup odds to Supabase for {target_date}")
    except Exception as e:
        logger.warning(f"Failed to persist MLB odds: {e}")

def calculate_win_probability_from_odds(away_ml, home_ml, away_spread, home_spread):
    if away_ml and home_ml:
        if abs(away_ml - home_ml) >= 20:
            away_prob = abs(away_ml) / (abs(away_ml) + 100) if away_ml < 0 else 100 / (away_ml + 100)
            home_prob = abs(home_ml) / (abs(home_ml) + 100) if home_ml < 0 else 100 / (home_ml + 100)
            return away_prob, home_prob

    if away_spread is not None and home_spread is not None:
        spread = away_spread if away_spread < 0 else home_spread
        underdog_prob = 1 / (1 + 10 ** (abs(spread) / 15))
        favorite_prob = 1 - underdog_prob
        if away_spread < 0:
            return favorite_prob, underdog_prob
        else:
            return underdog_prob, favorite_prob

    return 0.5, 0.5

async def get_schedule(date_str: str) -> list:
    """Fetch today's MLB schedule from the MLB Stats API."""
    try:
        url = "https://statsapi.mlb.com/api/v1/schedule"
        params = {
            "sportId": 1,
            "date": date_str,
            "hydrate": "probablePitcher,linescore,team",
        }
        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, timeout=15)
            r.raise_for_status()
            data = r.json()

        games = []
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                # Spring training filter — flip to "R" for regular season
                if game.get("gameType") not in ("S", "R"):
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
                    status_text = f"Delayed ({_format_et_time(starts_at)})"
                elif game_status == 1:
                    status_text = _format_et_time(starts_at)
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
                    "localTimeHHMM": _local_time_hhmm(starts_at),
                    "startsAt": starts_at,
                    "homeTeamId": home_team["team"]["id"],
                    "awayTeamId": away_team["team"]["id"],
                    "homeAbbr": home_team["team"].get("abbreviation", ""),
                    "awayAbbr": away_team["team"].get("abbreviation", ""),
                    "homePitcher": home_team.get("probablePitcher", {}).get("fullName"),
                    "awayPitcher": away_team.get("probablePitcher", {}).get("fullName"),
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
        async with httpx.AsyncClient() as client:
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

        async with httpx.AsyncClient() as client:
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
            async with httpx.AsyncClient() as client:
                r = await client.get(base, params={**base_params, "season": current_year - 1, "group": "hitting"}, timeout=15)
                r.raise_for_status()
                hitting_records = r.json().get("stats", [{}])[0].get("splits", [])
        if len(pitching_records) < 5:
            logger.warning(f"MLB pitching stats sparse for {current_year} ({len(pitching_records)} teams), falling back to {current_year - 1}")
            async with httpx.AsyncClient() as client:
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
    """Fetch MLB odds from SportsGameOdds. Same oddIDs as NBA (confirmed)."""
    odds_map = load_persisted_odds()

    api_key = os.getenv("SPORTS_GAME_ODDS_KEY")
    if not api_key:
        logger.warning("SPORTS_GAME_ODDS_KEY not set, returning persisted MLB odds only")
        return odds_map, 0

    try:
        # Scope to a single ET day's window so SGO only returns that day's slate.
        # Without these filters, SGO returns up to `limit` upcoming events
        # spanning multiple days, billing entities for games we don't need.
        target = target_date or datetime.now(ET).date()
        starts_after_utc = datetime.combine(target, datetime.min.time(), ET).astimezone(ZoneInfo("UTC"))
        starts_before_utc = datetime.combine(target + timedelta(days=1), datetime.min.time(), ET).astimezone(ZoneInfo("UTC"))

        url = "https://api.sportsgameodds.com/v2/events"
        params = {
            "leagueID": "MLB",
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

        async with httpx.AsyncClient() as client:
            r = await client.get(url, params=params, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()

        games = data.get("data", [])
        logger.info(f"SportsGameOdds: {len(games)} MLB games")

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
                    f"Skipping SGO MLB event {game.get('eventID', '?')} — "
                    f"home_team_id={home_team_id!r}, away_team_id={away_team_id!r}"
                )
                continue

            matchup_key = f"{away_tri}@{home_tri}"

            starts_at = game.get("status", {}).get("startsAt", "")
            try:
                game_date = datetime.fromisoformat(starts_at.replace("Z", "+00:00")).astimezone(ET).date()
            except Exception:
                game_date = datetime.now(ET).date()

            odds = game.get("odds", {})

            away_spread      = _to_num(_sgo_book_val(odds, "points-away-game-sp-away", "spread"))
            home_spread      = _to_num(_sgo_book_val(odds, "points-home-game-sp-home", "spread"))
            away_spread_odds = _to_num(_sgo_book_val(odds, "points-away-game-sp-away", "odds"))
            home_spread_odds = _to_num(_sgo_book_val(odds, "points-home-game-sp-home", "odds"))
            away_ml          = _to_num(_sgo_book_val(odds, "points-away-game-ml-away", "odds"))
            home_ml          = _to_num(_sgo_book_val(odds, "points-home-game-ml-home", "odds"))
            over_under       = _to_num(_sgo_book_val(odds, "points-all-game-ou-over",  "overUnder"))
            over_odds        = _to_num(_sgo_book_val(odds, "points-all-game-ou-over",  "odds"))
            under_odds       = _to_num(_sgo_book_val(odds, "points-all-game-ou-under", "odds"))

            away_fair_ml = _to_num(odds.get("points-away-game-ml-away", {}).get("fairOdds"))
            home_fair_ml = _to_num(odds.get("points-home-game-ml-home", {}).get("fairOdds"))

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

        logger.info(f"MLB odds map: {len(fresh_odds)} fresh + {len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total")
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"SportsGameOdds MLB HTTP error: {e.response.status_code} - {e.response.text}")
        return odds_map, 0
    except Exception as e:
        logger.error(f"SportsGameOdds MLB fetch error: {e}")
        return odds_map, 0

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
        set_cache("mlb_odds", odds)
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:nba_odds",
            "date": target.isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/today")
async def get_todays_games():
    cached_response = get_cached("mlb_assembled")
    if cached_response:
        return cached_response

    try:
        today_et = datetime.now(ET).date()
        date_str = today_et.isoformat()

        # Fetch uncached data sources in parallel
        cached_schedule  = get_cached("mlb_schedule")
        cached_standings = get_cached("mlb_standings")
        cached_stats     = get_cached("mlb_stats")
        cached_odds      = get_cached("mlb_odds")

        tasks = []
        keys  = []

        if not cached_schedule:
            tasks.append(get_schedule(date_str))
            keys.append("mlb_schedule")
        if not cached_standings:
            tasks.append(get_standings_map())
            keys.append("mlb_standings")
        if not cached_stats:
            tasks.append(get_stats_map())
            keys.append("mlb_stats")
        if not cached_odds:
            tasks.append(asyncio.get_event_loop().run_in_executor(None, load_persisted_odds))
            keys.append("mlb_odds")

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}")
                else:
                    set_cache(key, result)

        schedule  = get_cached("mlb_schedule")  or get_cached_data("mlb_schedule")  or []
        standings = get_cached("mlb_standings") or get_cached_data("mlb_standings") or {}
        stats     = get_cached("mlb_stats")     or get_cached_data("mlb_stats")     or {}
        odds_map  = get_cached("mlb_odds")      or get_cached_data("mlb_odds")      or {}

        logger.info(f"MLB: {len(schedule)} games, {len(standings)} standings, {len(stats)} stats, {len(odds_map)} odds")

        # Fetch contextMetrics for live games (win probability)
        live_pks = [g["gamePk"] for g in schedule if g.get("gameStatus") == 2]
        context_metrics_map: dict[int, dict] = {}
        if live_pks:
            async with httpx.AsyncClient() as client:
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
                home_prob, away_prob = _de_vig_ml(home_ml, away_ml)
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
                    live_home_prob, live_away_prob = _de_vig_ml(home_ml, away_ml)

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
                    "pitcherRecord": None,
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
                    "pitcherRecord": None,
                },
            })

        assembled_ttl = CACHE_TTL_LIVE if live_pks else CACHE_TTL_DEFAULT
        set_cache("mlb_assembled", refined_games, ttl=assembled_ttl)
        logger.info(f"MLB response assembled: {len(refined_games)} games")
        return refined_games

    except Exception as e:
        logger.error(f"MLB API error: {e}", exc_info=True)
        cached = get_cached_data("mlb_assembled")
        if cached:
            return cached
        return []
