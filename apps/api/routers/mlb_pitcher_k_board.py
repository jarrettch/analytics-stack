"""Read endpoints for Oh-K Corral, the MLB pitcher strikeout odds board.

Serves the append-only `mlb_pitcher_k_odds` history back out as two views:
a current-state board (latest and first-observed main line per book, plus the
latest alternate ladder) and a per-pitcher intraday line history. Both views
are pure aggregations over one day of snapshots fetched with a short TTL
cache; no settlement, flags, or identity resolution in v1.
"""
from __future__ import annotations

import logging
import time
from datetime import date, datetime

from fastapi import APIRouter, HTTPException

from . import utils as router_utils
from .mlb import get_tricode_from_sgo_team_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mlb/pitcher-k-odds", tags=["MLB"])

PITCHER_K_TABLE = "mlb_pitcher_k_odds"
DAY_ROWS_CACHE_TTL = 60
DAY_ROWS_PAGE_SIZE = 1000
# Sanity ceiling: one day is ~2k rows today; stop long before a runaway loop.
DAY_ROWS_MAX_PAGES = 50

ROW_COLUMNS = (
    "captured_at,collection_id,event_id,starts_at,home_team_id,away_team_id,"
    "player_id,player_name,player_team_id,side,bookmaker_id,is_alternate,"
    "market_phase,line,american_odds,open_line,open_american_odds"
)

# Display order for the board's book columns; unknown books sort after, alphabetically.
BOOK_ORDER = ("fanduel", "draftkings", "betmgm", "caesars", "espnbet", "bovada")

_day_rows_cache: dict[str, tuple[float, list[dict]]] = {}


def _book_sort_key(bookmaker_id: str) -> tuple[int, str]:
    try:
        return (BOOK_ORDER.index(bookmaker_id), bookmaker_id)
    except ValueError:
        return (len(BOOK_ORDER), bookmaker_id)


def _parse_game_date(value: str | None) -> date:
    if value is None:
        return datetime.now(router_utils.ET).date()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")


def fetch_day_rows(game_date: date) -> tuple[list[dict], bool]:
    """One day of snapshot rows, paginated out of Supabase and briefly cached.

    Returns (rows, stale): stale is True only when a fresh read failed and an
    expired cached day was served instead, so callers can say so out loud —
    an odds board must never present old prices as current silently.
    """
    key = game_date.isoformat()
    now = time.monotonic()
    cached = _day_rows_cache.get(key)
    if cached and now - cached[0] < DAY_ROWS_CACHE_TTL:
        return cached[1], False

    if not router_utils.supabase:
        raise HTTPException(status_code=503, detail="Odds history is not configured")

    rows: list[dict] = []
    try:
        for page in range(DAY_ROWS_MAX_PAGES):
            start = page * DAY_ROWS_PAGE_SIZE
            response = (
                router_utils.supabase.table(PITCHER_K_TABLE)
                .select(ROW_COLUMNS)
                .eq("game_date", key)
                .order("id")
                .range(start, start + DAY_ROWS_PAGE_SIZE - 1)
                .execute()
            )
            chunk = response.data or []
            rows.extend(chunk)
            if len(chunk) < DAY_ROWS_PAGE_SIZE:
                break
        else:
            logger.warning(
                "Pitcher-K board: %s exceeded %d pages; serving truncated day",
                key,
                DAY_ROWS_MAX_PAGES,
            )
    except Exception as exc:
        # Serve a stale day rather than an error if we have one.
        if cached:
            logger.warning("Pitcher-K board: serving stale rows for %s: %s", key, exc)
            return cached[1], True
        logger.exception("Pitcher-K board: failed to load rows for %s", key)
        raise HTTPException(
            status_code=503, detail="Odds history is temporarily unavailable"
        ) from exc

    # Keep the cache from accumulating every date ever browsed.
    if len(_day_rows_cache) > 8:
        _day_rows_cache.clear()
    _day_rows_cache[key] = (now, rows)
    return rows, False


def _collections_by_time(rows: list[dict]) -> list[list[dict]]:
    """Group one book's rows by collection, ordered oldest to newest."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["collection_id"], []).append(row)
    return sorted(
        groups.values(), key=lambda group: min(row["captured_at"] for row in group)
    )


def _price_pair(collection_rows: list[dict]) -> dict:
    """Collapse a collection's over/under rows for one book into one observation.

    Sides are only paired when they quote the same line. On a transient
    mismatch (over already moved to 5.5 while under still shows 4.5) the over
    side's line wins and the other side is omitted rather than mispriced.
    """
    by_line: dict[float, dict[str, dict]] = {}
    for row in collection_rows:
        by_line.setdefault(row["line"], {})[row["side"]] = row
    complete = [
        sides for sides in by_line.values() if "over" in sides and "under" in sides
    ]
    if complete:
        pair = max(
            complete,
            key=lambda sides: max(row["captured_at"] for row in sides.values()),
        )
    else:
        overs = [row for row in collection_rows if row["side"] == "over"]
        primary_row = max(overs or collection_rows, key=lambda row: row["captured_at"])
        pair = by_line[primary_row["line"]]
    over = pair.get("over")
    under = pair.get("under")
    primary = over or under
    return {
        "line": primary["line"],
        "overOdds": over["american_odds"] if over else None,
        "underOdds": under["american_odds"] if under else None,
        "capturedAt": max(row["captured_at"] for row in collection_rows),
        "marketPhase": primary["market_phase"],
    }


def _sgo_open(collection_rows: list[dict]) -> dict | None:
    """SGO's own opening line/prices, when the provider returned them.

    A side's open price is only attached when that side opened at the same
    line the pair reports, mirroring _price_pair's no-mixed-lines rule.
    """
    over = next((row for row in collection_rows if row["side"] == "over"), None)
    under = next((row for row in collection_rows if row["side"] == "under"), None)
    primary = over or under
    if primary is None or primary.get("open_line") is None:
        return None
    open_line = primary["open_line"]
    return {
        "line": open_line,
        "overOdds": (
            over.get("open_american_odds")
            if over and over.get("open_line") == open_line
            else None
        ),
        "underOdds": (
            under.get("open_american_odds")
            if under and under.get("open_line") == open_line
            else None
        ),
    }


def _alternate_ladder(alt_rows: list[dict]) -> list[dict]:
    """Latest collection's alternate lines for one book, one entry per line."""
    collections = _collections_by_time(alt_rows)
    if not collections:
        return []
    ladder: dict[float, dict] = {}
    for row in collections[-1]:
        entry = ladder.setdefault(
            row["line"], {"line": row["line"], "overOdds": None, "underOdds": None}
        )
        entry["overOdds" if row["side"] == "over" else "underOdds"] = row[
            "american_odds"
        ]
    return [ladder[line] for line in sorted(ladder)]


def _book_view(book_rows: list[dict]) -> dict | None:
    main_rows = [row for row in book_rows if not row["is_alternate"]]
    alt_rows = [row for row in book_rows if row["is_alternate"]]
    collections = _collections_by_time(main_rows)
    if not collections:
        # A book that only ever posted alternates isn't a board column in v1.
        return None
    return {
        "bookmakerId": book_rows[0]["bookmaker_id"],
        "current": _price_pair(collections[-1]),
        "first": _price_pair(collections[0]),
        "open": _sgo_open(collections[-1]),
        "alternates": _alternate_ladder(alt_rows),
    }


def build_board(rows: list[dict]) -> list[dict]:
    """Current-state board: one entry per (event, pitcher) with per-book views."""
    pitchers: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        pitchers.setdefault((row["event_id"], row["player_id"]), []).append(row)

    board = []
    for (event_id, player_id), player_rows in pitchers.items():
        latest = max(player_rows, key=lambda row: row["captured_at"])
        by_book: dict[str, list[dict]] = {}
        for row in player_rows:
            by_book.setdefault(row["bookmaker_id"], []).append(row)
        books = [
            view
            for book_id in sorted(by_book, key=_book_sort_key)
            if (view := _book_view(by_book[book_id])) is not None
        ]
        if not books:
            continue
        home_tri = get_tricode_from_sgo_team_id(latest["home_team_id"] or "")
        away_tri = get_tricode_from_sgo_team_id(latest["away_team_id"] or "")
        player_tri = get_tricode_from_sgo_team_id(latest["player_team_id"] or "")
        board.append(
            {
                "eventId": event_id,
                "playerId": player_id,
                "playerName": latest["player_name"] or player_id,
                "playerTeam": player_tri,
                "opponentTeam": home_tri if player_tri == away_tri else away_tri,
                "homeTeam": home_tri,
                "awayTeam": away_tri,
                "startsAt": latest["starts_at"],
                "marketPhase": latest["market_phase"],
                "lastCapturedAt": latest["captured_at"],
                "books": books,
            }
        )

    board.sort(key=lambda entry: (entry["startsAt"] or "", entry["playerName"]))
    return board


def build_history(rows: list[dict], player_id: str, event_id: str) -> list[dict]:
    """Main-line time series per book for one pitcher's expandable chart.

    The board identifies a pitcher by (event, player); event_id is mandatory
    so a doubleheader's two starts can never merge into one series.
    """
    main_rows = [
        row
        for row in rows
        if row["player_id"] == player_id
        and not row["is_alternate"]
        and row["event_id"] == event_id
    ]
    by_book: dict[str, list[dict]] = {}
    for row in main_rows:
        by_book.setdefault(row["bookmaker_id"], []).append(row)
    return [
        {
            "bookmakerId": book_id,
            "points": [
                _price_pair(collection)
                for collection in _collections_by_time(by_book[book_id])
            ],
        }
        for book_id in sorted(by_book, key=_book_sort_key)
    ]


# Sync (`def`) routes: FastAPI runs them in its threadpool, so the synchronous
# Supabase pagination on a cold cache can't stall the event loop.
@router.get("/board")
def pitcher_k_board(date: str | None = None):
    game_date = _parse_game_date(date)
    rows, stale = fetch_day_rows(game_date)
    return {
        "date": game_date.isoformat(),
        "generatedAt": datetime.now(router_utils.UTC).isoformat(),
        "lastObservedAt": max((row["captured_at"] for row in rows), default=None),
        "stale": stale,
        "pitchers": build_board(rows),
    }


@router.get("/history/{player_id}")
def pitcher_k_history(player_id: str, event_id: str, date: str | None = None):
    game_date = _parse_game_date(date)
    rows, stale = fetch_day_rows(game_date)
    books = build_history(rows, player_id, event_id)
    if not books:
        raise HTTPException(
            status_code=404, detail="No odds history for this pitcher on this date"
        )
    return {
        "date": game_date.isoformat(),
        "playerId": player_id,
        "eventId": event_id,
        "stale": stale,
        "playerName": next(
            (
                row["player_name"]
                for row in rows
                if row["player_id"] == player_id and row["player_name"]
            ),
            player_id,
        ),
        "books": books,
    }
