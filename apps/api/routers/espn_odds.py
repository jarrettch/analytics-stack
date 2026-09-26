"""DraftKings odds carried inside ESPN scoreboard responses.

Keyless and uncapped, but pregame only: ESPN drops a competition's `odds`
block at kickoff, so a line not captured before then is unrecoverable. See
specs/odds_providers.md and specs/ncaaf_dashboard.md.

Wired only to NCAAF. The extractor is generic so another sport can move onto
it later, but the SGO sports depend on de-vigged `fairOdds`, which ESPN does
not publish.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime

from .utils import (
    ET,
    UTC,
    calculate_win_probability_from_odds,
    get_http_client,
    load_persisted_odds,
    save_odds_snapshots,
    save_persisted_odds,
    to_num,
)

logger = logging.getLogger(__name__)

# Match on the id, never the name: ESPN spells this provider "Draft Kings" on
# the football feeds and "DraftKings" on MLB.
ESPN_DRAFTKINGS_PROVIDER_ID = "100"

# (odds_data field, ESPN market, side, value) for each leg.
_LEGS = (
    ("homeMoneyline", "moneyline", "home", "odds"),
    ("awayMoneyline", "moneyline", "away", "odds"),
    ("homeSpread", "pointSpread", "home", "line"),
    ("awaySpread", "pointSpread", "away", "line"),
    ("homeSpreadOdds", "pointSpread", "home", "odds"),
    ("awaySpreadOdds", "pointSpread", "away", "odds"),
    ("overUnder", "total", "over", "line"),
    ("overOdds", "total", "over", "odds"),
    ("underOdds", "total", "under", "odds"),
)
MARKET_FIELDS = tuple(field for field, *_ in _LEGS)


def _open_field(field: str) -> str:
    return f"open{field[0].upper()}{field[1:]}"


def _parse(value) -> int | float | None:
    """Parse ESPN's string prices: "+170", "-3.5", "o50.5", "u50.5", "EVEN"."""
    if value is None:
        return None
    text = str(value).strip()
    if text.upper() == "EVEN":
        return 100
    if text[:1] in {"o", "u", "O", "U"}:
        text = text[1:]
    return to_num(text)


def select_draftkings_odds(competition: dict) -> dict | None:
    """The DraftKings entry, selected by provider id rather than array position.

    Falling back to index zero would persist another book's price labelled
    DraftKings into append-only history, so a missing entry means no line.
    """
    entries = competition.get("odds") or []
    for entry in entries:
        if str((entry.get("provider") or {}).get("id")) == ESPN_DRAFTKINGS_PROVIDER_ID:
            return entry
    if entries:
        logger.warning(
            "ESPN competition %s has odds but no DraftKings entry (providers: %s)",
            competition.get("id", "?"),
            [str((entry.get("provider") or {}).get("id")) for entry in entries],
        )
    return None


def normalize_espn_odds(entry: dict | None, persisted: dict | None = None) -> dict:
    """Map an ESPN odds entry onto the shared `odds_data` contract.

    Each leg reads `close`, falls back to that leg's `open`, then to the
    persisted value so a line temporarily off the board doesn't wipe the prior
    one. Opening prices ride along as `open*` fields in the same bundle: ESPN
    gives no timestamp for them, so they are never a separate snapshot.
    """
    entry = entry or {}
    persisted = persisted or {}
    odds_data: dict = {}
    for field, market, side, value in _LEGS:
        leg = (entry.get(market) or {}).get(side) or {}
        close = _parse((leg.get("close") or {}).get(value))
        opening = _parse((leg.get("open") or {}).get(value))
        if close is not None:
            odds_data[field] = close
        elif opening is not None:
            odds_data[field] = opening
        else:
            odds_data[field] = persisted.get(field)
        odds_data[_open_field(field)] = (
            opening if opening is not None else persisted.get(_open_field(field))
        )

    # ESPN publishes raw book prices only; this de-vigs the moneyline pair.
    away_prob, home_prob = calculate_win_probability_from_odds(
        odds_data["awayMoneyline"],
        odds_data["homeMoneyline"],
        odds_data["awaySpread"],
        odds_data["homeSpread"],
    )
    odds_data["awayWinProbability"] = away_prob
    odds_data["homeWinProbability"] = home_prob
    return odds_data


def has_market(odds_data: dict | None) -> bool:
    return bool(odds_data) and any(
        odds_data.get(field) is not None for field in MARKET_FIELDS
    )


def _event_date(event: dict) -> tuple[date | None, datetime | None]:
    try:
        starts_at = datetime.fromisoformat(
            str(event.get("date", "")).replace("Z", "+00:00")
        )
    except ValueError:
        return None, None
    return starts_at.astimezone(ET).date(), starts_at


def extract_espn_odds(
    events: list[dict],
    persisted: dict,
    *,
    sport: str,
    tricode_for: Callable[[str], str] = lambda abbreviation: abbreviation,
    captured_at: datetime | None = None,
) -> tuple[dict, list[dict]]:
    """Normalize fresh odds from scoreboard events.

    Returns ({(game_date, matchup_key): odds_data}, pregame snapshot rows).
    Started games and games without a DraftKings entry are absent from the
    fresh map, so callers fall through to whatever was persisted for them.
    """
    captured_at = captured_at or datetime.now(UTC)
    fresh: dict = {}
    snapshots: list[dict] = []
    for event in events:
        competition = (event.get("competitions") or [{}])[0]
        entry = select_draftkings_odds(competition)
        if entry is None:
            continue

        competitors = {
            item.get("homeAway"): item for item in competition.get("competitors", [])
        }
        away_team = (competitors.get("away") or {}).get("team") or {}
        home_team = (competitors.get("home") or {}).get("team") or {}
        away_tri = tricode_for(away_team.get("abbreviation", ""))
        home_tri = tricode_for(home_team.get("abbreviation", ""))
        game_date, starts_at = _event_date(event)
        if not away_tri or not home_tri or game_date is None:
            logger.warning(
                "Skipping ESPN %s event %s: away=%r home=%r date=%r",
                sport, event.get("id", "?"), away_tri, home_tri, event.get("date"),
            )
            continue

        # Pregame only. A started game gets neither a fresh value nor a
        # snapshot, so callers fall through to its final persisted pregame line.
        state = ((competition.get("status") or event.get("status") or {})
                 .get("type") or {}).get("state", "pre")
        if state != "pre" or (starts_at is not None and starts_at <= captured_at):
            continue

        matchup_key = f"{away_tri}@{home_tri}"
        key = (game_date.isoformat(), matchup_key)
        odds_data = normalize_espn_odds(entry, persisted.get(key))
        if not has_market(odds_data):
            continue
        # Numeric ids keep identity reconstructable if an abbreviation changes;
        # odds_snapshots can never rewrite a matchup_key once written.
        odds_data["awayTeamId"] = str(away_team.get("id") or "") or None
        odds_data["homeTeamId"] = str(home_team.get("id") or "") or None
        fresh[key] = odds_data
        snapshots.append({
            "sport": sport,
            "provider": "espn",
            "bookmaker": "draftkings",
            "provider_event_id": str(event.get("id") or "") or None,
            "game_date": game_date.isoformat(),
            "matchup_key": matchup_key,
            "starts_at": starts_at.astimezone(UTC).isoformat() if starts_at else None,
            "captured_at": captured_at.isoformat(),
            "odds_data": odds_data,
        })
    return fresh, snapshots


async def fetch_espn_scoreboard(url: str, target_date: date, params: dict | None = None) -> dict:
    response = await get_http_client().get(
        url, params={"dates": target_date.strftime("%Y%m%d"), **(params or {})}
    )
    response.raise_for_status()
    return response.json()


async def fetch_espn_odds(
    *,
    sport: str,
    scoreboard_url: str,
    table_name: str,
    target_date: date | None = None,
    scoreboard_params: dict | None = None,
    tricode_for: Callable[[str], str] = lambda abbreviation: abbreviation,
    collection_stats: dict | None = None,
) -> tuple[dict, int]:
    """Fetch a scoreboard's DraftKings odds, merge with persisted rows, persist fresh.

    Parallel to fetch_sgo_odds: returns (merged_odds_map, fresh_count). Unlike
    it, a failed persisted-odds read propagates, because load_persisted_odds
    raises rather than returning a partial map and merging would be unsafe.
    A failed latest-value write does not stop other dates from being written,
    but leaves `latest_persistence_ok` false so the collection reports failure.
    """
    target = target_date or datetime.now(ET).date()
    stats = {
        "pregame_snapshot_candidates": 0,
        "snapshots_inserted": 0,
        "snapshots_deduplicated": 0,
        "snapshot_persistence_ok": False,
        "latest_persistence_ok": False,
    }
    if collection_stats is not None:
        collection_stats.clear()
        collection_stats.update(stats)

    odds_map = load_persisted_odds(table_name=table_name)
    scoreboard = await fetch_espn_scoreboard(scoreboard_url, target, scoreboard_params)
    events = scoreboard.get("events", [])
    fresh, snapshots = extract_espn_odds(
        events, odds_map, sport=sport, tricode_for=tricode_for
    )
    logger.info(
        "ESPN %s odds for %s: %d of %d events priced",
        sport, target.isoformat(), len(fresh), len(events),
    )

    by_date: dict[date, dict] = {}
    for (game_date, matchup_key), odds_data in fresh.items():
        by_date.setdefault(date.fromisoformat(game_date), {})[matchup_key] = odds_data
    # Attempt every date even after a failure: dates are independent, and a
    # skipped write loses lines ESPN may already have dropped.
    write_failures = 0
    for game_date, date_odds in by_date.items():
        if not save_persisted_odds(
            date_odds,
            game_date=game_date,
            table_name=table_name,
        ):
            write_failures += 1

    latest_persistence_ok = write_failures == 0
    snapshot_stats: dict = {}
    save_odds_snapshots(snapshots, stats=snapshot_stats)
    odds_map.update(fresh)

    if collection_stats is not None:
        collection_stats.update({
            "pregame_snapshot_candidates": len(snapshots),
            "snapshots_inserted": snapshot_stats.get("inserted", 0),
            "snapshots_deduplicated": snapshot_stats.get("deduplicated", 0),
            "snapshot_persistence_ok": snapshot_stats.get("persistence_ok", False),
            "latest_persistence_ok": latest_persistence_ok,
        })
    return odds_map, len(fresh)
