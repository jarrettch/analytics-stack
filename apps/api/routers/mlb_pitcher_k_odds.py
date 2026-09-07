"""Append-only SportsGameOdds snapshots for MLB pitcher strikeout props."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4, uuid5

from postgrest.types import ReturnMethod

from . import utils as router_utils

logger = logging.getLogger(__name__)

PITCHER_K_ODD_IDS = (
    "pitching_strikeouts-PLAYER_ID-game-ou-over",
    "pitching_strikeouts-PLAYER_ID-game-ou-under",
)
PITCHER_K_TABLE = "mlb_pitcher_k_odds"
INSERT_CHUNK_SIZE = 500
COLLECTION_NAMESPACE = UUID("27a90e55-9fb7-4fd7-9ad7-20bf144d3437")
IDEMPOTENCY_COLUMNS = (
    "collection_id,event_id,odd_id,bookmaker_id,is_alternate,line,american_odds"
)


@dataclass
class PitcherKCollectionStats:
    outcomes_found: int = 0
    rows_found: int = 0
    rows_inserted: int = 0
    malformed_prices: int = 0
    events_skipped: int = 0
    rows_deduplicated: int = 0
    identity_events_found: int = 0
    identity_events_resolved: int = 0
    identity_pitchers_found: int = 0
    identity_pitchers_resolved: int = 0
    identity_frozen_rows_skipped: int = 0
    identity_succeeded: bool = False
    succeeded: bool = False
    skipped: bool = False

    def copy_from(self, other: "PitcherKCollectionStats") -> None:
        self.outcomes_found = other.outcomes_found
        self.rows_found = other.rows_found
        self.rows_inserted = other.rows_inserted
        self.malformed_prices = other.malformed_prices
        self.events_skipped = other.events_skipped
        self.rows_deduplicated = other.rows_deduplicated
        self.identity_events_found = other.identity_events_found
        self.identity_events_resolved = other.identity_events_resolved
        self.identity_pitchers_found = other.identity_pitchers_found
        self.identity_pitchers_resolved = other.identity_pitchers_resolved
        self.identity_frozen_rows_skipped = other.identity_frozen_rows_skipped
        self.identity_succeeded = other.identity_succeeded
        self.succeeded = other.succeeded
        self.skipped = other.skipped

    def mark_skipped(self) -> None:
        self.skipped = True
        self.succeeded = False


def collection_id_for(collection_key: str | None) -> str:
    """Stable for a workflow rerun; unique for ad-hoc calls without a key."""
    if collection_key:
        return str(uuid5(COLLECTION_NAMESPACE, collection_key))
    return str(uuid4())


def _iso_or_none(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _american_odds(value) -> int | None:
    parsed = router_utils.to_num(value)
    if isinstance(parsed, int) and parsed != 0:
        return parsed
    return None


def _bool_or_none(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _market_phase(status: dict) -> str:
    if status.get("cancelled") is True:
        return "cancelled"
    if status.get("finalized") is True or status.get("ended") is True:
        return "final"
    if status.get("started") is True:
        return "live"
    if status.get("started") is False:
        return "pregame"
    return "unknown"


def _price_row(
    *,
    base: dict,
    price: dict,
    is_alternate: bool,
) -> dict | None:
    line = router_utils.to_num(price.get("overUnder"))
    odds = _american_odds(price.get("odds"))
    if line is None or odds is None:
        return None
    return {
        **base,
        "is_alternate": is_alternate,
        "line": line,
        "american_odds": odds,
        "open_line": router_utils.to_num(price.get("openOverUnder")),
        "open_american_odds": _american_odds(price.get("openOdds")),
        "close_line": router_utils.to_num(price.get("closeOverUnder")),
        "close_american_odds": _american_odds(price.get("closeOdds")),
        "bookmaker_updated_at": _iso_or_none(price.get("lastUpdatedAt")),
    }


def build_pitcher_k_snapshot_rows(
    events: list[dict],
    captured_at: datetime,
    *,
    collection_id: str | None = None,
) -> tuple[list[dict], int, int, int]:
    """Flatten available main and alternate pitcher-K prices from one SGO response.

    One row is emitted per event/player/side/bookmaker/line. Returns the rows,
    number of over/under outcomes found, malformed prices skipped, and events
    skipped because they lacked the required SGO event ID.
    """
    captured_at_utc = captured_at.astimezone(router_utils.UTC)
    captured_at_iso = captured_at_utc.isoformat()
    response_id = collection_id or collection_id_for(None)
    rows: list[dict] = []
    outcomes_found = 0
    malformed_prices = 0
    events_skipped = 0

    for event in events:
        event_id = event.get("eventID")
        if not event_id:
            events_skipped += 1
            continue
        status = event.get("status") or {}
        starts_at = _iso_or_none(status.get("startsAt"))
        if starts_at:
            game_date = (
                datetime.fromisoformat(starts_at)
                .astimezone(router_utils.ET)
                .date()
                .isoformat()
            )
        else:
            game_date = captured_at_utc.astimezone(router_utils.ET).date().isoformat()

        teams = event.get("teams") or {}
        home_team_id = (teams.get("home") or {}).get("teamID")
        away_team_id = (teams.get("away") or {}).get("teamID")
        players = event.get("players") or {}
        phase = _market_phase(status)

        for odd_id, odd in (event.get("odds") or {}).items():
            if not isinstance(odd, dict):
                continue
            if not (
                odd.get("statID") == "pitching_strikeouts"
                and odd.get("periodID") == "game"
                and odd.get("betTypeID") == "ou"
                and odd.get("sideID") in {"over", "under"}
            ):
                continue

            outcomes_found += 1
            player_id = odd.get("playerID") or odd.get("statEntityID")
            if not player_id:
                continue
            player = players.get(player_id) or {}

            for bookmaker_id, book in (odd.get("byBookmaker") or {}).items():
                if not isinstance(book, dict):
                    continue
                base = {
                    "collection_id": response_id,
                    "captured_at": captured_at_iso,
                    "game_date": game_date,
                    "event_id": event_id,
                    "starts_at": starts_at,
                    "home_team_id": home_team_id,
                    "away_team_id": away_team_id,
                    "player_id": player_id,
                    "player_name": player.get("name"),
                    "player_team_id": player.get("teamID"),
                    "odd_id": odd.get("oddID") or odd_id,
                    "market_name": odd.get("marketName"),
                    "side": odd["sideID"],
                    "bookmaker_id": bookmaker_id,
                    "market_phase": phase,
                    "event_started": _bool_or_none(status.get("started")),
                    "event_ended": _bool_or_none(status.get("ended")),
                    "event_finalized": _bool_or_none(status.get("finalized")),
                    "event_cancelled": _bool_or_none(status.get("cancelled")),
                }
                has_closing_price = (
                    book.get("closeOverUnder") is not None
                    and book.get("closeOdds") is not None
                )
                if book.get("available") or has_closing_price:
                    main_row = _price_row(
                        base=base, price=book, is_alternate=False
                    )
                    if main_row is None:
                        malformed_prices += 1
                    else:
                        rows.append(main_row)

                for alt_line in book.get("altLines") or []:
                    if not isinstance(alt_line, dict):
                        continue
                    alt_has_closing_price = (
                        alt_line.get("closeOverUnder") is not None
                        and alt_line.get("closeOdds") is not None
                    )
                    if not alt_line.get("available") and not alt_has_closing_price:
                        continue
                    alt_row = _price_row(
                        base=base, price=alt_line, is_alternate=True
                    )
                    if alt_row is None:
                        malformed_prices += 1
                    else:
                        rows.append(alt_row)

    return rows, outcomes_found, malformed_prices, events_skipped


def collect_pitcher_k_snapshots(
    events: list[dict], captured_at: datetime, collection_id: str | None = None
) -> PitcherKCollectionStats:
    """Parse and append one SGO response's pitcher-K snapshots to Supabase."""
    rows, outcomes_found, malformed_prices, events_skipped = (
        build_pitcher_k_snapshot_rows(
            events, captured_at, collection_id=collection_id
        )
    )
    stats = PitcherKCollectionStats(
        outcomes_found=outcomes_found,
        rows_found=len(rows),
        malformed_prices=malformed_prices,
        events_skipped=events_skipped,
    )
    if not rows:
        logger.info("MLB pitcher-K collector: no available prices in SGO response")
        stats.succeeded = stats.malformed_prices == 0 and stats.events_skipped == 0
        return stats
    if not router_utils.supabase:
        logger.warning(
            "Supabase not configured; skipping %d MLB pitcher-K snapshots", len(rows)
        )
        return stats

    for start in range(0, len(rows), INSERT_CHUNK_SIZE):
        chunk = rows[start:start + INSERT_CHUNK_SIZE]
        try:
            response = (
                router_utils.supabase.table(PITCHER_K_TABLE)
                .upsert(
                    chunk,
                    on_conflict=IDEMPOTENCY_COLUMNS,
                    ignore_duplicates=True,
                    returning=ReturnMethod.representation,
                )
                .execute()
            )
            inserted = len(response.data or [])
            stats.rows_inserted += inserted
            stats.rows_deduplicated += len(chunk) - inserted
        except Exception:
            # Never retry automatically: this table is append-only and a retry after an
            # ambiguous network failure could create duplicate observations.
            logger.exception(
                "Failed to append %d MLB pitcher-K snapshots to %s",
                len(chunk),
                PITCHER_K_TABLE,
            )

    # Success means every valid row was persisted or replay-deduplicated. A few
    # malformed book prices remain telemetry rather than failing an otherwise
    # complete collection; when no valid rows survive, malformed input does fail.
    stats.succeeded = (
        stats.rows_inserted + stats.rows_deduplicated == stats.rows_found
        and stats.events_skipped == 0
    )
    logger.info(
        "MLB pitcher-K collector: %d outcomes, %d prices, %d malformed, "
        "%d events skipped, "
        "%d appended, %d deduplicated (success=%s)",
        stats.outcomes_found,
        stats.rows_found,
        stats.malformed_prices,
        stats.events_skipped,
        stats.rows_inserted,
        stats.rows_deduplicated,
        stats.succeeded,
    )
    return stats
