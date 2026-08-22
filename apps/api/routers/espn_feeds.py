"""ESPN-backed team standings, efficiency ratings, and schedule lookahead.

stats.nba.com tarpits datacenter IPs. From Render both LeagueStandingsV3 and
LeagueDashTeamStats blow past their timeouts and return nothing — which degraded
silently to placeholder ratings — while answering in ~0.2s from a residential
connection. ESPN serves the same figures in well under a second from anywhere,
so these adapters replace those two nba_api calls for both NBA and WNBA.

Season resolution is deliberately left to ESPN: omitting the `season` parameter
returns the most recent season that actually has games, so the NBA off-season
doesn't hand back an empty current season.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import httpx

from .utils import slate_date_et

logger = logging.getLogger(__name__)

STANDINGS_URL = "https://site.api.espn.com/apis/v2/sports/basketball/{sport}/standings"
BYTEAM_URL = (
    "https://site.web.api.espn.com/apis/common/v3/sports/basketball/{sport}"
    "/statistics/byteam"
)
REQUEST_TIMEOUT = 10

# Free throws end a possession only some of the time; 0.44 is the standard
# coefficient for the share of FTA that do.
FTA_POSSESSION_WEIGHT = 0.44


def _get(url: str, params: dict | None = None) -> dict:
    response = httpx.get(
        url,
        params=params or {"region": "us", "lang": "en"},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    response.raise_for_status()
    return response.json()


def _stat_lookup(categories: list[dict], team_categories: list[dict]) -> dict:
    """Zip ESPN's separate label and value lists into {(category, name): value}.

    ESPN returns stat names once per category at the top level and values per
    team, positionally aligned. Some categories carry no names at all, hence the
    defensive .get calls.
    """
    values: dict[tuple[str, str], float] = {}
    for category, team_category in zip(categories, team_categories):
        name = category.get("name", "")
        for stat, value in zip(category.get("names", []), team_category.get("values", [])):
            values.setdefault((name, stat), value)
    return values


def fetch_standings(
    *,
    sport: str,
    canonical_tricode: Callable[[str], str],
    team_id_for: Callable[[str], str | None] | None = None,
    league_wide_seed: bool = False,
) -> dict:
    """Standings keyed by tricode, full display name, and canonical team ID.

    `league_wide_seed` ranks every team by win percentage instead of using
    ESPN's per-conference `playoffSeed`. The WNBA seeds its playoffs from the
    whole league, so its seed column means something different than the NBA's.
    """
    payload = _get(STANDINGS_URL.format(sport=sport))
    season = (payload.get("season") or {}).get("year")

    entries: list[dict] = []
    for group in payload.get("children") or []:
        conference = group.get("name") or "N/A"
        # "Eastern Conference" -> "East", matching what the UI showed before.
        short_conference = conference.split()[0].replace("ern", "") if conference else "N/A"
        for entry in (group.get("standings") or {}).get("entries") or []:
            stats = {stat.get("name"): stat for stat in entry.get("stats") or []}

            def value(key: str, field: str = "value"):
                return (stats.get(key) or {}).get(field)

            wins = value("wins") or 0
            losses = value("losses") or 0
            entries.append(
                {
                    "abbreviation": (entry.get("team") or {}).get("abbreviation", ""),
                    "display_name": (entry.get("team") or {}).get("displayName", ""),
                    "conference": short_conference,
                    "seed": value("playoffSeed") or 0,
                    "streak": value("streak", "displayValue") or "N/A",
                    "last10": value("Last Ten Games", "displayValue") or "N/A",
                    "wins": int(wins),
                    "losses": int(losses),
                }
            )

    if league_wide_seed:
        ranked = sorted(
            entries,
            key=lambda e: (e["wins"] + e["losses"]) and e["wins"] / (e["wins"] + e["losses"]),
            reverse=True,
        )
        for rank, entry in enumerate(ranked, start=1):
            entry["seed"] = rank

    standings_map: dict[str, dict] = {}
    for entry in entries:
        played = entry["wins"] + entry["losses"]
        team_data = {
            "record": f"{entry['wins']}-{entry['losses']}",
            "winPct": entry["wins"] / played if played else 0.5,
            "seed": int(entry["seed"]),
            "conference": entry["conference"],
            "streak": entry["streak"],
            "last10": entry["last10"],
        }
        tricode = canonical_tricode(entry["abbreviation"])
        for key in _keys_for(tricode, entry["display_name"], team_id_for):
            standings_map[key] = team_data

    logger.info(
        "ESPN %s standings: %d teams (season %s)", sport, len(entries), season
    )
    return standings_map


def fetch_efficiency(
    *,
    sport: str,
    canonical_tricode: Callable[[str], str],
    team_id_for: Callable[[str], str | None] | None = None,
) -> dict:
    """Offensive/defensive rating and pace, keyed like fetch_standings.

    ESPN publishes raw season totals rather than ratings, so we derive them:

        possessions = FGA - OREB + TOV + 0.44 * FTA
        offRating   = 100 * points / possessions
        defRating   = 100 * opponent points / possessions
        pace        = possessions / games played

    Opponent points come from the points differential, since ESPN's "defensive"
    category holds only rebounds/steals/blocks, not opponent scoring. Opponent
    possessions are taken to equal our own — both teams get essentially the same
    number in a game, which is the standard simplification.

    Note `pace` is possessions **per game**, so it is already on each league's
    own clock. LeagueDashTeamStats normalized to 48 minutes, which is why the
    WNBA path used to rescale it by 40/48; that adjustment must not be applied
    to this value.
    """
    payload = _get(BYTEAM_URL.format(sport=sport))
    categories = payload.get("categories") or []
    season = (payload.get("requestedSeason") or {}).get("year")

    efficiency_map: dict[str, dict] = {}
    teams = payload.get("teams") or []
    for team in teams:
        stats = _stat_lookup(categories, team.get("categories") or [])

        def offensive(name: str):
            return stats.get(("offensive", name))

        points = offensive("points")
        field_goals_attempted = offensive("fieldGoalsAttempted")
        offensive_rebounds = offensive("offensiveRebounds")
        turnovers = offensive("turnovers")
        free_throws_attempted = offensive("freeThrowsAttempted")
        games_played = stats.get(("general", "gamesPlayed"))
        point_differential = stats.get(("differential", "pointsDifferential"))

        required = (
            points,
            field_goals_attempted,
            offensive_rebounds,
            turnovers,
            free_throws_attempted,
            games_played,
            point_differential,
        )
        if any(item is None for item in required) or not games_played:
            logger.warning(
                "ESPN %s efficiency: incomplete stats for %s, leaving it to defaults",
                sport,
                (team.get("team") or {}).get("abbreviation"),
            )
            continue

        possessions = (
            field_goals_attempted
            - offensive_rebounds
            + turnovers
            + FTA_POSSESSION_WEIGHT * free_throws_attempted
        )
        if possessions <= 0:
            continue

        opponent_points = points - point_differential
        off_rating = 100 * points / possessions
        def_rating = 100 * opponent_points / possessions
        team_data = {
            "offRating": round(off_rating, 1),
            "defRating": round(def_rating, 1),
            "netRating": round(off_rating - def_rating, 1),
            "pace": round(possessions / games_played, 1),
        }

        espn_team = team.get("team") or {}
        tricode = canonical_tricode(espn_team.get("abbreviation", ""))
        for key in _keys_for(tricode, espn_team.get("displayName", ""), team_id_for):
            efficiency_map[key] = team_data

    logger.info(
        "ESPN %s efficiency: %d teams (season %s)", sport, len(teams), season
    )
    return efficiency_map


def _keys_for(
    tricode: str,
    display_name: str,
    team_id_for: Callable[[str], str | None] | None,
) -> list[str]:
    """Every alias a caller might look this team up by.

    Callers key off tricode, "City Nickname", or the league's own team ID
    depending on which feed built the game payload, and ESPN's own numeric IDs
    match none of them — hence team_id_for translating tricode to the league ID.
    """
    keys = [key for key in (tricode, display_name) if key]
    if team_id_for and tricode:
        team_id = team_id_for(tricode)
        if team_id:
            keys.append(str(team_id))
    return keys


SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/{sport}/scoreboard"
# ESPN caps page size; a two-week NBA window is well under this.
SCOREBOARD_LIMIT = 400
# Statuses that mean "no game happened", so the lookahead should skip the date.
_DEAD_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED"}


def _espn_record(competitor: dict) -> tuple[int, int]:
    for record in competitor.get("records") or []:
        if record.get("name") in ("overall", "Overall"):
            summary = record.get("summary") or ""
            wins, _, losses = summary.partition("-")
            try:
                return int(wins), int(losses)
            except ValueError:
                return 0, 0
    return 0, 0


def _scheduled_team(competitor: dict, canonical_tricode) -> dict:
    team = competitor.get("team") or {}
    wins, losses = _espn_record(competitor)
    score = competitor.get("score")
    return {
        "teamId": str(team.get("id") or ""),
        "teamCity": team.get("location", ""),
        "teamName": team.get("name", ""),
        "teamTricode": canonical_tricode(team.get("abbreviation", "")),
        "wins": wins,
        "losses": losses,
        "score": int(score) if str(score).isdigit() else 0,
    }


def fetch_next_slate(
    *,
    sport: str,
    start_date,
    lookahead_days: int,
    canonical_tricode,
    timezone,
) -> tuple[list[dict], object]:
    """Earliest date from start_date with games, in nba_api scoreboard shape.

    Replaces ScheduleLeagueV2, which lives on stats.nba.com and so never returns
    from a datacenter IP. ESPN accepts a YYYYMMDD-YYYYMMDD range, so the whole
    lookahead window costs one request instead of one per day.

    ESPN stamps `date` in UTC while the slate is keyed on Eastern days, so games
    are bucketed after converting — otherwise late tip-offs land on tomorrow.

    Bucketing goes through slate_date_et, the same function the /today routes use
    to pick which slate to show. A slate day runs 6am-6am ET, so a 9pm PT tip
    (midnight ET) belongs to the night it started rather than the calendar date
    it crossed into. Assignment and display must share one boundary: if they
    disagree, a game is either dropped mid-play or invisible at its own tip-off.
    """
    from datetime import datetime, timedelta

    end_date = start_date + timedelta(days=lookahead_days)
    payload = _get(
        SCOREBOARD_URL.format(sport=sport),
        {
            "dates": f"{start_date:%Y%m%d}-{end_date:%Y%m%d}",
            "limit": SCOREBOARD_LIMIT,
        },
    )

    by_date: dict[object, list[dict]] = {}
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
        if status_type.get("name") in _DEAD_STATUSES:
            continue

        raw_date = event.get("date") or competition.get("date") or ""
        try:
            tipoff = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(
                timezone
            )
        except ValueError:
            continue

        state = status_type.get("state")
        broadcasts = [
            name
            for broadcast in competition.get("broadcasts") or []
            for name in broadcast.get("names") or []
        ]
        by_date.setdefault(slate_date_et(tipoff), []).append(
            {
                "gameId": str(event.get("id") or competition.get("id") or ""),
                "gameEt": tipoff.isoformat(),
                "gameStatus": 3 if state == "post" else 2 if state == "in" else 1,
                "gameStatusText": (
                    status_type.get("description") or status_type.get("detail") or ""
                ),
                "period": status.get("period") or 0,
                "gameClock": "",
                "awayTeam": _scheduled_team(away, canonical_tricode),
                "homeTeam": _scheduled_team(home, canonical_tricode),
                "seriesText": "",
                "seriesGameNumber": "",
                "broadcasters": {
                    "nationalBroadcasters": [
                        {"broadcasterDisplay": name} for name in broadcasts
                    ]
                },
            }
        )

    for offset in range(lookahead_days + 1):
        target = start_date + timedelta(days=offset)
        if by_date.get(target):
            logger.info(
                "ESPN %s lookahead selected %s with %d games",
                sport,
                target.isoformat(),
                len(by_date[target]),
            )
            return by_date[target], target

    logger.info(
        "ESPN %s lookahead found no games within %d days of %s",
        sport,
        lookahead_days,
        start_date.isoformat(),
    )
    return [], None
