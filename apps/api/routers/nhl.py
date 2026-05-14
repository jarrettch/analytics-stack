from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta

import httpx
from fastapi import APIRouter

from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    UTC,
    Cache,
    calculate_win_probability_from_odds,
    format_et_time,
    get_http_client,
    load_persisted_odds,
    save_persisted_odds,
    sgo_book_val,
    to_num,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/nhl", tags=["NHL"])

cache = Cache({
    "scoreboard":   CACHE_TTL_DEFAULT,
    "standings":    300,
    "team_summary": 600,
    "odds":         CACHE_TTL_ODDS,
    "assembled":    CACHE_TTL_DEFAULT,
})

# --- Team identifiers -------------------------------------------------------

# Spot-checked against a.espncdn.com/i/teamlogos/nhl/500 — validate each URL
# before shipping. Re-run when a team rebrands.
ESPN_SLUG_OVERRIDES = {
    "TBL": "tb",
    "LAK": "la",
    "SJS": "sj",
}

NHL_TRI_TO_FULL_NAME = {
    "ANA": "Anaheim Ducks",        "BOS": "Boston Bruins",
    "BUF": "Buffalo Sabres",       "CAR": "Carolina Hurricanes",
    "CBJ": "Columbus Blue Jackets", "CGY": "Calgary Flames",
    "CHI": "Chicago Blackhawks",   "COL": "Colorado Avalanche",
    "DAL": "Dallas Stars",         "DET": "Detroit Red Wings",
    "EDM": "Edmonton Oilers",      "FLA": "Florida Panthers",
    "LAK": "Los Angeles Kings",    "MIN": "Minnesota Wild",
    "MTL": "Montréal Canadiens",   "NJD": "New Jersey Devils",
    "NSH": "Nashville Predators",  "NYI": "New York Islanders",
    "NYR": "New York Rangers",     "OTT": "Ottawa Senators",
    "PHI": "Philadelphia Flyers",  "PIT": "Pittsburgh Penguins",
    "SEA": "Seattle Kraken",       "SJS": "San Jose Sharks",
    "STL": "St. Louis Blues",      "TBL": "Tampa Bay Lightning",
    "TOR": "Toronto Maple Leafs",  "UTA": "Utah Mammoth",
    "VAN": "Vancouver Canucks",    "VGK": "Vegas Golden Knights",
    "WPG": "Winnipeg Jets",        "WSH": "Washington Capitals",
}

NHL_FULL_NAME_TO_TRI = {v: k for k, v in NHL_TRI_TO_FULL_NAME.items()}

# SGO uses UPPER_SNAKE_CASE_NHL format (verified against a live response 2026-05-14)
SGO_TEAM_ID_TO_TRI = {
    "ANAHEIM_DUCKS_NHL": "ANA",         "BOSTON_BRUINS_NHL": "BOS",
    "BUFFALO_SABRES_NHL": "BUF",        "CAROLINA_HURRICANES_NHL": "CAR",
    "COLUMBUS_BLUE_JACKETS_NHL": "CBJ", "CALGARY_FLAMES_NHL": "CGY",
    "CHICAGO_BLACKHAWKS_NHL": "CHI",    "COLORADO_AVALANCHE_NHL": "COL",
    "DALLAS_STARS_NHL": "DAL",          "DETROIT_RED_WINGS_NHL": "DET",
    "EDMONTON_OILERS_NHL": "EDM",       "FLORIDA_PANTHERS_NHL": "FLA",
    "LOS_ANGELES_KINGS_NHL": "LAK",     "MINNESOTA_WILD_NHL": "MIN",
    "MONTREAL_CANADIENS_NHL": "MTL",    "NEW_JERSEY_DEVILS_NHL": "NJD",
    "NASHVILLE_PREDATORS_NHL": "NSH",   "NEW_YORK_ISLANDERS_NHL": "NYI",
    "NEW_YORK_RANGERS_NHL": "NYR",      "OTTAWA_SENATORS_NHL": "OTT",
    "PHILADELPHIA_FLYERS_NHL": "PHI",   "PITTSBURGH_PENGUINS_NHL": "PIT",
    "SEATTLE_KRAKEN_NHL": "SEA",        "SAN_JOSE_SHARKS_NHL": "SJS",
    "ST_LOUIS_BLUES_NHL": "STL",        "TAMPA_BAY_LIGHTNING_NHL": "TBL",
    "TORONTO_MAPLE_LEAFS_NHL": "TOR",   "UTAH_MAMMOTH_NHL": "UTA",
    "VANCOUVER_CANUCKS_NHL": "VAN",     "VEGAS_GOLDEN_KNIGHTS_NHL": "VGK",
    "WINNIPEG_JETS_NHL": "WPG",         "WASHINGTON_CAPITALS_NHL": "WSH",
}


def espn_slug(tri: str) -> str:
    return ESPN_SLUG_OVERRIDES.get(tri, tri.lower())


def get_tricode_from_sgo_team_id(team_id: str) -> str:
    return SGO_TEAM_ID_TO_TRI.get(team_id.upper(), "")


# --- Game state composer ----------------------------------------------------

def _ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(n, f"{n}th")


def _final_str(period_type: str) -> str:
    if period_type == "OT":
        return "F/OT"
    if period_type == "SO":
        return "F/SO"
    return "Final"


def compose_status(g: dict) -> tuple[int, str, int | None, str | None]:
    """Map an api-web.nhle.com game object to (gameStatus, statusText, period, gameClock).

    See nhl_dashboard.md "Game state → card status" for the full table.
    """
    state = g.get("gameState", "")
    sched = g.get("gameScheduleState", "OK")
    pd = g.get("periodDescriptor") or {}
    period_type = pd.get("periodType", "REG")
    period_num = pd.get("number")
    clock = g.get("clock") or {}
    time_left = clock.get("timeRemaining")

    if state in ("FUT", "PRE"):
        return 1, format_et_time(g.get("startTimeUTC", "")), None, None

    if state == "LIVE":
        if sched == "INT":
            return 2, "INT", period_num, None
        if period_type == "SO":
            return 2, "SO", 5, None
        if period_type == "OT":
            label = f"OT {time_left}" if time_left else "OT"
            return 2, label, 4, time_left
        # Regulation
        if period_num and time_left:
            return 2, f"{_ordinal(period_num)} {time_left}", period_num, time_left
        return 2, "LIVE", period_num, time_left

    if state in ("FINAL", "OFF"):
        return 3, _final_str(period_type), period_num, None

    return 1, "TBD", None, None


# --- Fetchers ---------------------------------------------------------------

async def get_standings_map() -> dict:
    """Returns {tricode: {record, winPct, points, gamesPlayed, conference, division, seed, streak, last10}}."""
    try:
        client = get_http_client()
        r = await client.get("https://api-web.nhle.com/v1/standings/now", follow_redirects=True)
        r.raise_for_status()
        data = r.json()
        out = {}
        for t in data.get("standings", []):
            tri = (t.get("teamAbbrev") or {}).get("default")
            if not tri:
                continue
            wins = t.get("wins", 0)
            losses = t.get("losses", 0)
            ot = t.get("otLosses", 0)
            gp = t.get("gamesPlayed", 0)
            pts = t.get("points", 0)
            l10w = t.get("l10Wins", 0)
            l10l = t.get("l10Losses", 0)
            l10ot = t.get("l10OtLosses", 0)

            sc = t.get("streakCode")
            scount = t.get("streakCount")
            streak = f"{sc}{scount}" if sc and scount is not None else "N/A"

            out[tri] = {
                "record": f"{wins}-{losses}-{ot}",
                "winPct": (pts / (gp * 2)) if gp > 0 else 0.5,
                "conference": t.get("conferenceAbbrev", "N/A"),
                "division": t.get("divisionAbbrev", "N/A"),
                "seed": t.get("divisionSequence", 0),
                "streak": streak,
                "last10": f"{l10w}-{l10l}-{l10ot}",
            }
        logger.info(f"NHL standings: {len(out)} teams")
        return out
    except Exception as e:
        logger.error(f"NHL standings fetch error: {e}", exc_info=True)
        return {}


async def get_team_summary_map() -> dict:
    """Returns {tricode: {goalsFor, goalsAgainst, powerPlayPct, penaltyKillPct, savesPct, shotsForPerGame}}.

    Missing source fields surface as None. PP%/PK% scaled from 0–1 → 0–100.
    """
    season = os.getenv("NHL_SEASON_ID", "20252026")
    url = f"https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId={season}"
    try:
        client = get_http_client()
        r = await client.get(url)
        r.raise_for_status()
        rows = r.json().get("data", [])
        out = {}
        for row in rows:
            name = row.get("teamFullName", "")
            tri = NHL_FULL_NAME_TO_TRI.get(name)
            if not tri:
                logger.warning(f"NHL team-summary row with unmapped name: {name!r}")
                continue

            gf = to_num(row.get("goalsForPerGame"))
            ga = to_num(row.get("goalsAgainstPerGame"))
            pp = to_num(row.get("powerPlayPct"))
            pk = to_num(row.get("penaltyKillPct"))
            sapg = to_num(row.get("shotsAgainstPerGame"))
            sfpg = to_num(row.get("shotsForPerGame"))

            if (
                sapg is not None
                and sapg > 0
                and ga is not None
            ):
                saves_pct = 1 - (ga / sapg)
            else:
                saves_pct = None

            out[tri] = {
                "goalsFor": gf,
                "goalsAgainst": ga,
                "powerPlayPct": pp * 100 if pp is not None else None,
                "penaltyKillPct": pk * 100 if pk is not None else None,
                "savesPct": saves_pct,
                "shotsForPerGame": sfpg,
            }
        logger.info(f"NHL team summary: {len(out)} teams (season {season})")
        return out
    except Exception as e:
        logger.error(f"NHL team-summary fetch error: {e}", exc_info=True)
        return {}


async def get_scoreboard_data() -> dict:
    """Fetches today's NHL slate from api-web.nhle.com/v1/score/now."""
    try:
        client = get_http_client()
        r = await client.get("https://api-web.nhle.com/v1/score/now", follow_redirects=True)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"NHL scoreboard fetch error: {e}", exc_info=True)
        return {}


# --- Odds (SGO) -------------------------------------------------------------

async def get_odds_map(target_date: date | None = None):
    """Fetch NHL odds from SGO and merge with persisted rows. Mirrors nba.get_odds_map."""
    odds_map = load_persisted_odds(table_name="nhl_odds")

    api_key = os.getenv("SPORTS_GAME_ODDS_KEY")
    if not api_key:
        logger.warning("SPORTS_GAME_ODDS_KEY not set, returning persisted NHL odds only")
        return odds_map, 0

    try:
        target = target_date or datetime.now(ET).date()
        starts_after_utc = datetime.combine(target, datetime.min.time(), ET).astimezone(UTC)
        starts_before_utc = datetime.combine(target + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)

        url = "https://api.sportsgameodds.com/v2/events"
        params = {
            "leagueID": "NHL",
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
        games = r.json().get("data", [])
        logger.info(f"SportsGameOdds: {len(games)} NHL events for {target.isoformat()}")

        fresh_odds = {}
        odds_by_date = {}

        for game in games:
            teams = game.get("teams", {})
            home_id = teams.get("home", {}).get("teamID", "")
            away_id = teams.get("away", {}).get("teamID", "")
            home_tri = get_tricode_from_sgo_team_id(home_id)
            away_tri = get_tricode_from_sgo_team_id(away_id)

            if not home_tri or not away_tri:
                logger.warning(
                    f"Skipping SGO NHL event {game.get('eventID', '?')} — "
                    f"home_team_id={home_id!r}, away_team_id={away_id!r}"
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

            away_fair_ml = to_num(odds.get("points-away-game-ml-away", {}).get("fairOdds"))
            home_fair_ml = to_num(odds.get("points-home-game-ml-home", {}).get("fairOdds"))

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
            odds_by_date.setdefault(game_date, {})[matchup_key] = odds_data

        odds_map.update(fresh_odds)
        for gd, gd_odds in odds_by_date.items():
            save_persisted_odds(gd_odds, game_date=gd, table_name="nhl_odds")

        logger.info(f"NHL odds map: {len(fresh_odds)} fresh + {len(odds_map) - len(fresh_odds)} persisted = {len(odds_map)} total")
        return odds_map, len(fresh_odds)
    except httpx.HTTPStatusError as e:
        logger.error(f"SportsGameOdds NHL HTTP error: {e.response.status_code} - {e.response.text}")
        return odds_map, 0
    except Exception as e:
        logger.error(f"SportsGameOdds NHL fetch error: {e}", exc_info=True)
        return odds_map, 0


# --- Endpoints --------------------------------------------------------------

@router.get("/cron/fetch-odds")
async def cron_fetch_odds(offset: int = 0):
    """Daily odds prefetch. offset=0 fetches today (ET), offset=1 fetches tomorrow."""
    try:
        target = datetime.now(ET).date() + timedelta(days=offset)
        odds, fresh_count = await get_odds_map(target_date=target)
        cache.set("odds", odds)
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:nhl_odds",
            "date": target.isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _project_score(
    away_gf, away_ga, home_gf, home_ga,
    market_total, market_spread_home_perspective,
) -> tuple[int, int, int | None]:
    """Naive GF/GA blend + 70% market / 30% naive lean. Returns (away, home, projected_total)."""
    if None in (away_gf, away_ga, home_gf, home_ga):
        return 0, 0, None

    naive_away = (away_gf + home_ga) / 2
    naive_home = (home_gf + away_ga) / 2
    naive_total = naive_away + naive_home
    naive_margin = naive_home - naive_away  # positive = home favored

    if market_spread_home_perspective is not None:
        market_margin = -market_spread_home_perspective  # positive = home favored
        blended_margin = naive_margin * 0.3 + market_margin * 0.7
    else:
        blended_margin = naive_margin

    if market_total is not None:
        blended_total = market_total * 0.7 + naive_total * 0.3
    else:
        blended_total = naive_total

    projected_away = round(max(0, (blended_total - blended_margin) / 2))
    projected_home = round(max(0, (blended_total + blended_margin) / 2))
    return projected_away, projected_home, round(blended_total)


@router.get("/today")
async def get_todays_games():
    cached_response = cache.get("assembled")
    if cached_response:
        return cached_response

    try:
        # Fetch only what's stale
        cached_scoreboard = cache.get("scoreboard")
        cached_standings = cache.get("standings")
        cached_summary = cache.get("team_summary")
        cached_odds = cache.get("odds")

        fetch_keys = []
        coros = []

        if not cached_scoreboard:
            coros.append(get_scoreboard_data())
            fetch_keys.append("scoreboard")
        if not cached_standings:
            coros.append(get_standings_map())
            fetch_keys.append("standings")
        if not cached_summary:
            coros.append(get_team_summary_map())
            fetch_keys.append("team_summary")
        if not cached_odds:
            coros.append(asyncio.to_thread(load_persisted_odds, table_name="nhl_odds"))
            fetch_keys.append("odds")

        if coros:
            results = await asyncio.gather(*coros, return_exceptions=True)
            for key, result in zip(fetch_keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch NHL {key}: {result}, using cached data")
                else:
                    cache.set(key, result)

        sb = cache.get("scoreboard") or cache.get_stale("scoreboard") or {}
        standings_map = cache.get("standings") or cache.get_stale("standings") or {}
        summary_map = cache.get("team_summary") or cache.get_stale("team_summary") or {}
        odds_map = cache.get("odds") or cache.get_stale("odds") or {}

        games = sb.get("games", [])
        # Filter to regular season (2) + playoffs (3). Skip preseason (1) and others.
        games = [g for g in games if g.get("gameType") in (2, 3)]
        logger.info(f"NHL: processing {len(games)} games (standings={len(standings_map)}, summary={len(summary_map)}, odds={len(odds_map)})")

        refined = []
        for g in games:
            away_team = g.get("awayTeam", {})
            home_team = g.get("homeTeam", {})
            away_tri = away_team.get("abbrev", "")
            home_tri = home_team.get("abbrev", "")
            if not away_tri or not home_tri:
                continue

            matchup_key = f"{away_tri}@{home_tri}"
            game_date_str = (g.get("gameDate") or "")[:10]
            game_odds = odds_map.get((game_date_str, matchup_key), {})

            game_status, status_text, period, game_clock = compose_status(g)

            # Spread line for display
            away_spread = game_odds.get("awaySpread")
            home_spread = game_odds.get("homeSpread")
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

            # Final-game grading
            spread_result = None
            total_result = None
            over_under = game_odds.get("overUnder")
            if game_status == 3:
                away_score = away_team.get("score", 0) or 0
                home_score = home_team.get("score", 0) or 0
                if spread_value is not None:
                    margin = away_score - home_score
                    if favorite == "away":
                        spread_result = margin > abs(spread_value)
                    else:
                        spread_result = (-margin) > abs(spread_value)
                if over_under is not None:
                    total = away_score + home_score
                    total_result = "over" if total > over_under else "under" if total < over_under else "push"

            away_std = standings_map.get(away_tri, {})
            home_std = standings_map.get(home_tri, {})
            away_sum = summary_map.get(away_tri, {})
            home_sum = summary_map.get(home_tri, {})

            market_total = game_odds.get("overUnder")
            market_spread_home_perspective = game_odds.get("homeSpread")
            projected_away, projected_home, projected_total = _project_score(
                away_sum.get("goalsFor"), away_sum.get("goalsAgainst"),
                home_sum.get("goalsFor"), home_sum.get("goalsAgainst"),
                market_total, market_spread_home_perspective,
            )

            def team_payload(tri: str, team_obj: dict, std: dict, summary: dict, projected: int) -> dict:
                return {
                    "name": (team_obj.get("name") or {}).get("default") or NHL_TRI_TO_FULL_NAME.get(tri, tri),
                    "abbreviation": tri,
                    "espnSlug": espn_slug(tri),
                    "record": std.get("record", "0-0-0"),
                    "winPct": std.get("winPct", 0.5),
                    "seed": std.get("seed", 0),
                    "conference": std.get("conference", "N/A"),
                    "streak": std.get("streak", "N/A"),
                    "last10": std.get("last10", "0-0-0"),
                    "score": team_obj.get("score", 0) or 0,
                    "predictedScore": projected,
                    "goalsFor": summary.get("goalsFor"),
                    "goalsAgainst": summary.get("goalsAgainst"),
                    "powerPlayPct": summary.get("powerPlayPct"),
                    "penaltyKillPct": summary.get("penaltyKillPct"),
                    "savesPct": summary.get("savesPct"),
                }

            away_payload = team_payload(away_tri, away_team, away_std, away_sum, projected_away)
            home_payload = team_payload(home_tri, home_team, home_std, home_sum, projected_home)
            away_payload["winProbability"] = game_odds.get("awayWinProbability")
            home_payload["winProbability"] = game_odds.get("homeWinProbability")

            refined.append({
                "sport": "nhl",
                "gameId": str(g.get("id", "")),
                "status": status_text,
                "gameStatus": game_status,
                "period": period,
                "gameClock": game_clock,
                "time": format_et_time(g.get("startTimeUTC", "")),
                "date": game_date_str,
                "venue": (g.get("venue") or {}).get("default", "TBD"),
                "broadcast": "Local",
                "model": "xG v5.0",
                "spreadLine": spread_line,
                "spreadResult": spread_result,
                "pregameSpread": game_odds.get("homeSpread"),
                "spreadOddsHome": game_odds.get("homeSpreadOdds"),
                "spreadOddsAway": game_odds.get("awaySpreadOdds"),
                "homeMoneyline": game_odds.get("homeMoneyline"),
                "awayMoneyline": game_odds.get("awayMoneyline"),
                "overUnder": over_under,
                "overOdds": game_odds.get("overOdds"),
                "underOdds": game_odds.get("underOdds"),
                "totalResult": total_result,
                "projectedTotal": projected_total,
                # Playoff context — typed in v1, populated by a later feature.
                "seriesGameNumber": None,
                "seriesStatus": None,
                "away": away_payload,
                "home": home_payload,
            })

        has_live = any(g.get("gameStatus") == 2 for g in refined)
        assembled_ttl = CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT
        if has_live:
            cache.set_ttl("scoreboard", CACHE_TTL_LIVE)
        cache.set("assembled", refined, ttl=assembled_ttl)

        logger.info(f"NHL assembled: {len(refined)} games, TTL={assembled_ttl}s, live={has_live}")
        return refined
    except Exception as e:
        logger.error(f"NHL /today error: {e}", exc_info=True)
        cached = cache.get_stale("assembled")
        if cached:
            return cached
        return []
