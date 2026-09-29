"""Normalize SharpAPI game lines into the shared dashboard odds shape."""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime

from .sharp_odds import SharpSweep, fetch_sharp_odds
from .utils import (
    ET,
    UTC,
    calculate_win_probability_from_odds,
    save_odds_snapshots,
    save_persisted_odds,
    to_num,
)

logger = logging.getLogger(__name__)

SHARP_GAME_MARKETS = ("moneyline", "point_spread", "total_points")
SHARP_GAME_MARKETS_BY_LEAGUE = {
    "nhl": ("moneyline", "puck_line", "total_goals"),
}
SHARP_GAME_BOOKS = ("draftkings", "fanduel")


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def _unique_value(rows: list[dict], side: str, field: str):
    values = {
        to_num(row.get(field))
        for row in rows
        if row.get("selection_type") == side
    }
    values.discard(None)
    return values.pop() if len(values) == 1 else None


def _book_values(markets: dict[str, list[dict]]) -> dict:
    moneyline = markets.get("moneyline", [])
    spread = markets.get("point_spread") or markets.get("puck_line", [])
    total = markets.get("total_points") or markets.get("total_goals", [])

    over_line = _unique_value(total, "over", "line")
    under_line = _unique_value(total, "under", "line")
    if over_line is None:
        total_line = under_line
    elif under_line is None or over_line == under_line:
        total_line = over_line
    else:
        total_line = None

    return {
        "awaySpread": _unique_value(spread, "away", "line"),
        "homeSpread": _unique_value(spread, "home", "line"),
        "awaySpreadOdds": _unique_value(spread, "away", "odds_american"),
        "homeSpreadOdds": _unique_value(spread, "home", "odds_american"),
        "overUnder": total_line,
        "overOdds": _unique_value(total, "over", "odds_american"),
        "underOdds": _unique_value(total, "under", "odds_american"),
        "awayMoneyline": _unique_value(moneyline, "away", "odds_american"),
        "homeMoneyline": _unique_value(moneyline, "home", "odds_american"),
    }


def _book_rank(item: tuple[str, dict]) -> tuple[int, int]:
    book, group = item
    values = _book_values(group["markets"])
    complete_moneyline = (
        values["awayMoneyline"] is not None
        and values["homeMoneyline"] is not None
    )
    populated = sum(value is not None for value in values.values())
    preference = len(SHARP_GAME_BOOKS) - SHARP_GAME_BOOKS.index(book)
    return (100 if complete_moneyline else 0) + populated, preference


async def fetch_sharp_game_odds(
    *,
    sport: str,
    league: str,
    table_name: str,
    tricode_for: Callable[[str], str],
    target_date: date,
    persisted_odds: dict,
    collection_stats: dict | None = None,
    market_rows: dict[str, list[dict]] | None = None,
) -> tuple[dict, int]:
    """Fetch one league's pregame DK/FD lines and persist normalized matchups.

    Sharp returns league-wide market rows, so a multi-date caller may pass a
    request-local cache and reuse one complete sweep while filtering each date.
    """
    sweep = SharpSweep()
    rows_by_market = dict(market_rows or {})
    fetched_rows: dict[str, list[dict]] = {}
    game_markets = SHARP_GAME_MARKETS_BY_LEAGUE.get(
        league.lower(), SHARP_GAME_MARKETS
    )
    for market in game_markets:
        if market in rows_by_market:
            continue
        fetched_rows[market] = await fetch_sharp_odds(league, market, sweep=sweep)
    rows_by_market.update(fetched_rows)
    if market_rows is not None:
        # Publish only after every missing market completed. A failed partial
        # sweep must not become the source for later dates in the same job.
        market_rows.update(fetched_rows)

    captured_at = datetime.now(UTC)
    groups: dict[tuple[str, str, str], dict] = {}
    for market, rows in rows_by_market.items():
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("league", "").lower() != league.lower():
                continue
            if row.get("market_type") != market:
                continue
            if row.get("is_live") is not False or row.get("is_main_line") is not True:
                continue
            if row.get("is_alternate_line") is True or row.get("is_active") is False:
                continue

            starts_at = _parse_time(row.get("event_start_time"))
            if starts_at is None or starts_at <= captured_at:
                continue
            game_date = starts_at.astimezone(ET).date()
            if game_date != target_date:
                continue

            home_tri = tricode_for(str(row.get("home_team") or ""))
            away_tri = tricode_for(str(row.get("away_team") or ""))
            book = row.get("sportsbook")
            if not home_tri or not away_tri or home_tri == away_tri:
                logger.warning(
                    "Skipping SharpAPI %s row with unmapped teams home=%r away=%r",
                    league,
                    row.get("home_team"),
                    row.get("away_team"),
                )
                continue
            if book not in SHARP_GAME_BOOKS:
                continue

            matchup_key = f"{away_tri}@{home_tri}"
            key = (game_date.isoformat(), matchup_key, book)
            group = groups.setdefault(
                key,
                {
                    "starts_at": starts_at,
                    "provider_event_id": str(row.get("event_id") or "") or None,
                    "markets": {},
                },
            )
            group["starts_at"] = min(group["starts_at"], starts_at)
            group["markets"].setdefault(market, []).append(row)

    by_matchup: dict[tuple[str, str], dict[str, dict]] = {}
    for (game_date_value, matchup_key, book), group in groups.items():
        by_matchup.setdefault((game_date_value, matchup_key), {})[book] = group

    odds_map = dict(persisted_odds)
    fresh_odds: dict[tuple[str, str], dict] = {}
    snapshots = []
    for (game_date_value, matchup_key), books in by_matchup.items():
        book, group = max(books.items(), key=_book_rank)
        values = _book_values(group["markets"])
        persisted = odds_map.get((game_date_value, matchup_key), {})

        for field in (
            "awaySpread", "homeSpread", "awaySpreadOdds", "homeSpreadOdds",
            "overUnder", "overOdds", "underOdds",
        ):
            if values[field] is None:
                values[field] = persisted.get(field)

        away_prob, home_prob = calculate_win_probability_from_odds(
            values["awayMoneyline"],
            values["homeMoneyline"],
            values["awaySpread"],
            values["homeSpread"],
        )
        odds_data = {
            **values,
            "awayWinProbability": away_prob,
            "homeWinProbability": home_prob,
        }
        fresh_odds[(game_date_value, matchup_key)] = odds_data
        snapshots.append(
            {
                "sport": sport,
                "provider": "sharpapi",
                "bookmaker": book,
                "provider_event_id": group["provider_event_id"],
                "game_date": game_date_value,
                "matchup_key": matchup_key,
                "starts_at": group["starts_at"].isoformat(),
                "captured_at": captured_at.isoformat(),
                "odds_data": odds_data,
            }
        )

    odds_map.update(fresh_odds)
    save_persisted_odds(
        {matchup: data for (_, matchup), data in fresh_odds.items()},
        game_date=target_date,
        table_name=table_name,
    )
    snapshot_stats: dict = {}
    save_odds_snapshots(snapshots, stats=snapshot_stats)
    if collection_stats is not None:
        collection_stats.update(
            {
                "provider": "sharpapi",
                "pregame_snapshot_candidates": len(snapshots),
                "pregame_snapshot_matchups": [
                    snapshot["matchup_key"] for snapshot in snapshots
                ],
                "snapshots_inserted": snapshot_stats.get("inserted", 0),
                "snapshots_deduplicated": snapshot_stats.get("deduplicated", 0),
                "snapshot_persistence_ok": snapshot_stats.get(
                    "persistence_ok", False
                ),
            }
        )

    logger.info(
        "SharpAPI fallback: %d fresh %s matchups for %s from %d requests",
        len(fresh_odds),
        league,
        target_date.isoformat(),
        sweep.requests_made,
    )
    return odds_map, len(fresh_odds)
