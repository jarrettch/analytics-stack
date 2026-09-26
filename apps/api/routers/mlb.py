from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .auth import CronOffset, require_cron_auth
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    CACHE_TTL_ODDS,
    ET,
    UTC,
    Cache,
    de_vig_ml,
    fetch_sgo_odds,
    format_et_time,
    grade_spread_result,
    get_http_client,
    load_persisted_odds,
    local_time_hhmm,
    slate_date_et,
)
from .mlb_pitcher_k_odds import (
    PITCHER_K_ODD_IDS,
    PitcherKCollectionStats,
    collection_id_for,
    collect_pitcher_k_snapshots,
)
from .mlb_pitcher_k_settlement import (
    build_pitching_result_rows,
    identity_sources_from_sgo_events,
    load_snapshot_identity_sources,
    load_settlement_status_counts,
    load_stale_settlement_counts,
    persist_pitching_results,
    reconcile_pitcher_k_identities,
)
from .mlb_team_ids import MLB_TEAM_ID_TO_TRI, get_tricode_from_sgo_team_id

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mlb", tags=["MLB"])
SettlementLookback = Annotated[
    int,
    Query(ge=1, le=7, description="Completed ET dates to recheck."),
]
DEFAULT_SETTLEMENT_LOOKBACK_DAYS = 3

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

def _detail_status(game_status: int) -> str:
    if game_status == 2:
        return "live"
    if game_status == 3:
        return "final"
    return "preview"


def _safe_int(value, default=0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None or value in ("", ".---", "-.--", "-"):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mlb_inning_score(linescore: dict, side: str) -> dict:
    scheduled = _safe_int(linescore.get("scheduledInnings"), 9)
    innings = []
    for inning in linescore.get("innings", []):
        value = (inning.get(side) or {}).get("runs")
        innings.append(None if value is None else _safe_int(value))
    while len(innings) < scheduled:
        innings.append(None)
    team_totals = (linescore.get("teams") or {}).get(side) or {}
    return {
        "innings": innings,
        "runs": _safe_int(team_totals.get("runs")),
        "hits": _safe_int(team_totals.get("hits")),
        "errors": _safe_int(team_totals.get("errors")),
    }


def _mlb_player(team: dict, player_id: int | str) -> dict:
    return (team.get("players") or {}).get(f"ID{player_id}", {})


def _mlb_hitter(row: dict) -> dict:
    game = ((row.get("stats") or {}).get("batting") or {})
    season = ((row.get("seasonStats") or {}).get("batting") or {})
    position = row.get("position") or {}
    return {
        "name": (row.get("person") or {}).get("fullName", "Unknown"),
        "position": position.get("abbreviation", ""),
        "ab": _safe_int(game.get("atBats")),
        "runs": _safe_int(game.get("runs")),
        "hits": _safe_int(game.get("hits")),
        "rbi": _safe_int(game.get("rbi")),
        "hr": _safe_int(game.get("homeRuns")),
        "bb": _safe_int(game.get("baseOnBalls")),
        "so": _safe_int(game.get("strikeOuts")),
        "avg": season.get("avg") or ".000",
        "obp": season.get("obp") or ".000",
        "slg": season.get("slg") or ".000",
    }


def _mlb_pitcher(row: dict) -> dict:
    game = ((row.get("stats") or {}).get("pitching") or {})
    season = ((row.get("seasonStats") or {}).get("pitching") or {})
    return {
        "name": (row.get("person") or {}).get("fullName", "Unknown"),
        "ip": str(game.get("inningsPitched") or "0.0"),
        "hits": _safe_int(game.get("hits")),
        "runs": _safe_int(game.get("runs")),
        "earnedRuns": _safe_int(game.get("earnedRuns")),
        "bb": _safe_int(game.get("baseOnBalls")),
        "so": _safe_int(game.get("strikeOuts")),
        "hr": _safe_int(game.get("homeRuns")),
        "pitchCount": _safe_int(game.get("numberOfPitches") or game.get("pitchesThrown")),
        "strikes": _safe_int(game.get("strikes")),
        "era": season.get("era") or "-.--",
    }


def _mlb_team_box(boxscore: dict, side: str) -> dict:
    team = (boxscore.get("teams") or {}).get(side) or {}
    hitters = [_mlb_hitter(_mlb_player(team, pid)) for pid in team.get("batters", [])]
    pitchers = [_mlb_pitcher(_mlb_player(team, pid)) for pid in team.get("pitchers", [])]
    team_batting = {
        "ab": sum(p["ab"] for p in hitters),
        "runs": sum(p["runs"] for p in hitters),
        "hits": sum(p["hits"] for p in hitters),
        "rbi": sum(p["rbi"] for p in hitters),
        "hr": sum(p["hr"] for p in hitters),
        "bb": sum(p["bb"] for p in hitters),
        "so": sum(p["so"] for p in hitters),
        "lob": sum(_safe_int((((_mlb_player(team, pid).get("stats") or {}).get("batting") or {}).get("leftOnBase"))) for pid in team.get("batters", [])),
        "risp": "--",
    }
    return {"hitting": hitters, "pitching": pitchers, "teamBatting": team_batting}


def _mlb_half_label(half: str | None) -> str:
    if half == "top":
        return "Top"
    if half == "bottom":
        return "Bot"
    return ""


def _mlb_play_team(play: dict, matchup: dict) -> str | None:
    half = ((play.get("about") or {}).get("halfInning") or "").lower()
    if half == "top":
        return (matchup.get("away") or {}).get("abbreviation")
    if half == "bottom":
        return (matchup.get("home") or {}).get("abbreviation")
    return None


def _mlb_plays(all_plays: list[dict], matchup: dict) -> list[dict]:
    out = []
    for index, play in enumerate(all_plays):
        about = play.get("about") or {}
        result = play.get("result") or {}
        inning = about.get("inning")
        half = _mlb_half_label((about.get("halfInning") or "").lower())
        out.append({
            "id": str(play.get("atBatIndex", index)),
            "eventId": str(play.get("atBatIndex", index)),
            "sequence": index,
            "clock": f"{half} {inning}".strip() if inning else "",
            "quarter": f"{inning}{'st' if inning == 1 else 'nd' if inning == 2 else 'rd' if inning == 3 else 'th'}" if inning else "",
            "team": _mlb_play_team(play, matchup),
            "event": result.get("description") or result.get("event") or "Game event",
            "awayScore": _safe_int(result.get("awayScore")),
            "homeScore": _safe_int(result.get("homeScore")),
        })
    return out


def _mlb_win_probability(points: list[dict]) -> list[dict]:
    if not points:
        return []
    max_index = max(_safe_int(p.get("atBatIndex")) for p in points) or 1
    out = []
    for point in points:
        home = point.get("homeTeamWinProbability")
        away = point.get("awayTeamWinProbability")
        if home is None or away is None:
            continue
        idx = _safe_int(point.get("atBatIndex"))
        about = point.get("about") or {}
        inning = about.get("inning")
        half = _mlb_half_label((about.get("halfInning") or "").lower())
        out.append({
            "x": round((idx / max_index) * 100, 1),
            "awayProb": round(_safe_float(away), 1),
            "homeProb": round(_safe_float(home), 1),
            "label": f"{half} {inning}".strip() if inning else None,
        })
    return out

async def get_schedule(date_str: str, *, raise_on_error: bool = False) -> list:
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
                    "abstractGameState": abstract_state,
                    "detailedState": detailed_state,
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
        if raise_on_error:
            raise
        return []


async def get_mlb_game_feed(game_pk: int) -> dict | None:
    try:
        response = await get_http_client().get(
            f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
            timeout=20,
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        logger.warning("MLB game feed failed for %s: %s", game_pk, exc)
        return None


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

async def get_odds_map(
    target_date: date | None = None,
    *,
    request_time: datetime | None = None,
    pitcher_k_stats: PitcherKCollectionStats | None = None,
    pitcher_k_collection_id: str | None = None,
):
    """Fetch MLB odds and append pitcher-K prices from the same SGO response."""
    async def collect_pitcher_ks(events: list[dict], captured_at: datetime) -> None:
        identity_date = target_date or captured_at.astimezone(ET).date()
        schedule = await get_schedule(identity_date.isoformat())
        collected = await asyncio.to_thread(
            collect_pitcher_k_snapshots,
            events,
            captured_at,
            pitcher_k_collection_id,
        )
        identity_stats = await asyncio.to_thread(
            reconcile_pitcher_k_identities,
            identity_sources_from_sgo_events(events, captured_at),
            schedule,
            captured_at,
        )
        collected.identity_events_found = identity_stats.events_found
        collected.identity_events_resolved = identity_stats.events_resolved
        collected.identity_pitchers_found = identity_stats.pitchers_found
        collected.identity_pitchers_resolved = identity_stats.pitchers_resolved
        collected.identity_frozen_rows_skipped = identity_stats.frozen_rows_skipped
        collected.identity_succeeded = identity_stats.succeeded
        if pitcher_k_stats is not None:
            pitcher_k_stats.copy_from(collected)

    return await fetch_sgo_odds(
        sport="mlb",
        league_id="MLB",
        table_name="mlb_odds",
        tricode_for=get_tricode_from_sgo_team_id,
        target_date=target_date,
        request_time=request_time,
        extra_odd_ids=PITCHER_K_ODD_IDS,
        include_alt_lines=True,
        include_open_close_odds=True,
        response_consumer=collect_pitcher_ks,
        optional_market_fallback_consumer=(
            pitcher_k_stats.mark_skipped if pitcher_k_stats is not None else None
        ),
    )

@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds(
    offset: CronOffset = 0,
    # Workflows pass "<github.run_id>-today" / "-tomorrow", well under 128.
    collection_key: Annotated[str | None, Query(max_length=128)] = None,
):
    """GitHub Actions cron: fetch and persist a single ET day's MLB odds.

    `offset` selects which ET day to fetch: 0 = today (default), 1 = tomorrow.
    Used by the 2am ET workflow to pre-fetch tomorrow's opening lines so the
    UI has data when it rolls the slate forward after the last game ends.
    """
    try:
        request_time = datetime.now(ET)
        target = request_time.date() + timedelta(days=offset)
        pitcher_k_stats = PitcherKCollectionStats()
        odds, fresh_count = await get_odds_map(
            target_date=target,
            request_time=request_time,
            pitcher_k_stats=pitcher_k_stats,
            pitcher_k_collection_id=collection_id_for(collection_key),
        )
        cache.set("odds", odds)
        return {
            "success": True,
            "fresh_from_api": fresh_count,
            "persisted_loaded": len(odds) - fresh_count,
            "total_matchups": len(odds),
            "persisted_to": "supabase:mlb_odds",
            "pitcher_k_success": pitcher_k_stats.succeeded,
            "pitcher_k_outcomes_found": pitcher_k_stats.outcomes_found,
            "pitcher_k_rows_found": pitcher_k_stats.rows_found,
            "pitcher_k_rows_inserted": pitcher_k_stats.rows_inserted,
            "pitcher_k_rows_deduplicated": pitcher_k_stats.rows_deduplicated,
            "pitcher_k_malformed_prices": pitcher_k_stats.malformed_prices,
            "pitcher_k_events_skipped": pitcher_k_stats.events_skipped,
            "pitcher_k_skipped": pitcher_k_stats.skipped,
            "pitcher_k_persisted_to": "supabase:mlb_pitcher_k_odds",
            "pitcher_k_identity_success": pitcher_k_stats.identity_succeeded,
            "pitcher_k_identity_events": {
                "found": pitcher_k_stats.identity_events_found,
                "resolved": pitcher_k_stats.identity_events_resolved,
            },
            "pitcher_k_identity_pitchers": {
                "found": pitcher_k_stats.identity_pitchers_found,
                "resolved": pitcher_k_stats.identity_pitchers_resolved,
            },
            "pitcher_k_identity_frozen_rows_skipped": (
                pitcher_k_stats.identity_frozen_rows_skipped
            ),
            "date": target.isoformat(),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


async def _settle_pitcher_k_date(target: date) -> dict:
    fetched_at = datetime.now(UTC)
    schedule = await get_schedule(target.isoformat(), raise_on_error=True)
    sources = await asyncio.to_thread(load_snapshot_identity_sources, target)
    identity_stats = await asyncio.to_thread(
        reconcile_pitcher_k_identities,
        sources,
        schedule,
        fetched_at,
    )

    final_games = [
        game for game in schedule if game.get("abstractGameState") == "Final"
    ]
    feeds = await asyncio.gather(
        *(get_mlb_game_feed(int(game["gamePk"])) for game in final_games)
    )
    result_rows = []
    feed_failures = []
    projection_failures = []
    for game, feed in zip(final_games, feeds):
        game_pk = int(game["gamePk"])
        if feed is None:
            feed_failures.append(game_pk)
            continue
        game_rows = build_pitching_result_rows(game_pk, target, feed, fetched_at)
        if not game_rows:
            projection_failures.append(game_pk)
            continue
        result_rows.extend(game_rows)
    result_stats = await asyncio.to_thread(
        persist_pitching_results,
        result_rows,
        schedule,
        fetched_at,
    )
    status_counts = await asyncio.to_thread(load_settlement_status_counts, target)
    return {
        "date": target.isoformat(),
        "scheduled_games": len(schedule),
        "final_games": len(final_games),
        "feed_failures": feed_failures,
        "projection_failures": projection_failures,
        "result_rows": result_stats.result_rows,
        "identity_events": {
            "found": identity_stats.events_found,
            "resolved": identity_stats.events_resolved,
        },
        "identity_pitchers": {
            "found": identity_stats.pitchers_found,
            "resolved": identity_stats.pitchers_resolved,
        },
        "identity_succeeded": identity_stats.succeeded,
        "results_succeeded": result_stats.succeeded,
        "frozen_events": result_stats.frozen_events,
        "status_counts": status_counts,
        "success": (
            identity_stats.succeeded
            and result_stats.succeeded
            and not feed_failures
            and not projection_failures
        ),
    }


@router.post(
    "/cron/settle-pitcher-ks",
    dependencies=[Depends(require_cron_auth)],
)
async def cron_settle_pitcher_ks(
    game_date: Annotated[date | None, Query(alias="date")] = None,
    lookback_days: SettlementLookback = DEFAULT_SETTLEMENT_LOOKBACK_DAYS,
):
    """Reconcile identities and upsert official results for completed games."""
    try:
        if game_date is not None:
            targets = [game_date]
        else:
            yesterday = datetime.now(ET).date() - timedelta(days=1)
            targets = [
                yesterday - timedelta(days=offset)
                for offset in reversed(range(lookback_days))
            ]
        dates = []
        for target in targets:
            dates.append(await _settle_pitcher_k_date(target))
        stale_before = (
            min(targets)
            if game_date is None
            else datetime.now(ET).date() - timedelta(
                days=DEFAULT_SETTLEMENT_LOOKBACK_DAYS
            )
        )
        stale_settlements = await asyncio.to_thread(
            load_stale_settlement_counts,
            stale_before,
        )
        return {
            "success": all(result["success"] for result in dates),
            "dates": dates,
            "stale_settlements": stale_settlements,
            "persisted_to": (
                "supabase:mlb_sgo_event_map+mlb_sgo_pitcher_map+"
                "mlb_pitcher_k_results"
            ),
        }
    except Exception as exc:
        logger.exception("MLB pitcher-K settlement cron failed")
        return {"success": False, "error": str(exc)}

@router.get("/today")
async def get_todays_games():
    cached_response = cache.get("assembled")
    if cached_response:
        return cached_response

    try:
        today_et = slate_date_et()
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

        odds_load_failed = False
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for key, result in zip(keys, results):
                if isinstance(result, Exception):
                    logger.warning(f"Failed to fetch {key}: {result}")
                    if key == "odds":
                        odds_load_failed = True
                else:
                    cache.set(key, result)

        if odds_load_failed:
            raise HTTPException(status_code=503, detail="MLB odds feed unavailable")

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
                    spread_result = grade_spread_result(
                        away_score,
                        home_score,
                        favorite,
                        spread_value,
                    )

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"MLB API error: {e}", exc_info=True)
        cached = cache.get_stale("assembled")
        if cached:
            return cached
        return []


@router.get("/game/{game_pk}")
async def get_game_detail(game_pk: str):
    matchups = await get_todays_games()
    matchup = next((g for g in matchups if str(g.get("gameId")) == str(game_pk)), None)
    if not matchup:
        raise HTTPException(status_code=404, detail="MLB game not found in today's schedule")

    game_status = int(matchup.get("gameStatus", 1) or 1)
    status = _detail_status(game_status)
    raw_status = matchup.get("status") or ""
    client = get_http_client()

    async def fetch_json(url: str):
        try:
            response = await client.get(url, timeout=15.0)
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            logger.warning(f"MLB detail fetch failed for {game_pk}: {url} {exc}")
            return None

    feed_data, wp_data = await asyncio.gather(
        fetch_json(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"),
        fetch_json(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/winProbability"),
    )

    live = (feed_data or {}).get("liveData") or {}
    linescore = live.get("linescore") or {}
    boxscore = live.get("boxscore") or {}
    all_plays = (live.get("plays") or {}).get("allPlays") or []

    away_score = _mlb_inning_score(linescore, "away")
    home_score = _mlb_inning_score(linescore, "home")
    if not away_score["runs"]:
        away_score["runs"] = _safe_int((matchup.get("away") or {}).get("score"))
    if not home_score["runs"]:
        home_score["runs"] = _safe_int((matchup.get("home") or {}).get("score"))

    win_probability = _mlb_win_probability(wp_data if isinstance(wp_data, list) else [])

    return {
        "sport": "mlb",
        "gameId": game_pk,
        "matchup": matchup,
        "status": status,
        "rawStatus": raw_status,
        "awayScore": away_score,
        "homeScore": home_score,
        "awayBoxScore": _mlb_team_box(boxscore, "away") if boxscore else None,
        "homeBoxScore": _mlb_team_box(boxscore, "home") if boxscore else None,
        "playByPlay": _mlb_plays(all_plays, matchup),
        "winProbability": win_probability,
    }
