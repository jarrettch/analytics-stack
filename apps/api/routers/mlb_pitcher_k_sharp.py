"""SharpAPI adapter for MLB pitcher strikeout snapshots."""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

from . import utils as router_utils
from .mlb_pitcher_k_odds import (
    PitcherKCollectionStats,
    persist_pitcher_k_rows,
)
from .mlb_pitcher_k_settlement import (
    normalize_person_name,
    reconcile_pitcher_k_identities,
)
from .mlb_team_ids import get_tricode_from_team_name
from .sharp_odds import SharpSweep, fetch_sharp_odds

logger = logging.getLogger(__name__)

SHARP_PITCHER_K_MARKET = "player_strikeouts"
SHARP_PITCHER_K_BOOKS = {"draftkings", "fanduel"}


def _timestamp(value) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("Missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp has no timezone")
    return parsed.astimezone(router_utils.UTC)


def _number(value, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field}") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"Invalid {field}")
    return parsed


def _text(row: dict, field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing {field}")
    return value.strip()


def _player_team(row: dict, schedule: list[dict]) -> str | None:
    away = get_tricode_from_team_name(_text(row, "away_team"))
    home = get_tricode_from_team_name(_text(row, "home_team"))
    player = normalize_person_name(_text(row, "player_name"))
    starts_at = _timestamp(row.get("event_start_time"))
    candidates = [
        game
        for game in schedule
        if game.get("awayAbbr") == away and game.get("homeAbbr") == home
    ]
    if len(candidates) > 1:
        def start_distance(game: dict) -> float:
            try:
                return abs(
                    (_timestamp(game.get("startsAt")) - starts_at).total_seconds()
                )
            except ValueError:
                return math.inf

        candidates.sort(key=start_distance)
    game = candidates[0] if candidates else None
    if not game:
        return None
    if normalize_person_name(game.get("awayPitcher")) == player:
        return away
    if normalize_person_name(game.get("homePitcher")) == player:
        return home
    return None


def normalize_sharp_pitcher_k_row(
    row: dict,
    *,
    target_date: date,
    schedule: list[dict],
    collection_id: str,
    captured_at: datetime,
) -> dict | None:
    """Normalize one target-date main-line row, or ignore another date."""
    if not isinstance(row, dict):
        raise ValueError("Invalid odds row")
    if row.get("league") != "mlb" or row.get("market_type") != SHARP_PITCHER_K_MARKET:
        raise ValueError("Response does not match requested MLB market")

    # The Sharp endpoint can return multiple upcoming slates. Ignore rows outside
    # this collection's ET date before applying strict validation so a malformed
    # future row cannot fail an otherwise valid current-day snapshot.
    starts_at = _timestamp(row.get("event_start_time"))
    if starts_at.astimezone(router_utils.ET).date() != target_date:
        return None

    if row.get("sportsbook") not in SHARP_PITCHER_K_BOOKS:
        raise ValueError("Unexpected sportsbook")
    for field in (
        "is_live",
        "is_main_line",
        "is_alternate_line",
        "is_active",
        "is_stale_pregame_price",
    ):
        if type(row.get(field)) is not bool:
            raise ValueError(f"Missing or invalid {field}")
    if (
        row["is_live"]
        or not row["is_main_line"]
        or row["is_alternate_line"]
        or not row["is_active"]
        or row["is_stale_pregame_price"]
    ):
        raise ValueError("Response contains unavailable or non-main pregame price")

    provider_time = _timestamp(row.get("timestamp"))
    if provider_time >= starts_at or captured_at >= starts_at:
        raise ValueError("SharpAPI price is at or after first pitch")

    away_name = _text(row, "away_team")
    home_name = _text(row, "home_team")
    away = get_tricode_from_team_name(away_name)
    home = get_tricode_from_team_name(home_name)
    if not away or not home or away == home:
        raise ValueError("Unmapped or invalid MLB matchup")
    player_name = _text(row, "player_name")
    normalized_player = normalize_person_name(player_name)
    if not normalized_player:
        raise ValueError("Invalid player_name")
    side = row.get("selection_type")
    if side not in {"over", "under"}:
        raise ValueError("Invalid strikeout side")
    line = _number(row.get("line"), "line")
    american = _number(row.get("odds_american"), "odds_american")
    if line < 0 or not american.is_integer() or abs(american) < 100:
        raise ValueError("Invalid strikeout line or American odds")

    event_id = _text(row, "event_id")
    selection_id = _text(row, "selection_id")
    return {
        "collection_id": collection_id,
        "provider": "sharpapi",
        "captured_at": captured_at.isoformat(),
        "game_date": target_date.isoformat(),
        "event_id": event_id,
        "starts_at": starts_at.isoformat(),
        "home_team_id": home,
        "away_team_id": away,
        "player_id": f"name:{normalized_player}",
        "player_name": player_name,
        "player_team_id": _player_team(row, schedule),
        "odd_id": f"{SHARP_PITCHER_K_MARKET}:{selection_id}",
        "market_name": "Pitcher Strikeouts",
        "side": side,
        "bookmaker_id": row["sportsbook"],
        "is_alternate": False,
        "market_phase": "pregame",
        "event_started": False,
        "event_ended": False,
        "event_finalized": False,
        "event_cancelled": False,
        "line": line,
        "american_odds": int(american),
        "open_line": None,
        "open_american_odds": None,
        "close_line": None,
        "close_american_odds": None,
        "bookmaker_updated_at": provider_time.isoformat(),
    }


async def collect_sharp_pitcher_k_snapshots(
    *,
    target_date: date,
    schedule: list[dict],
    collection_id: str,
) -> PitcherKCollectionStats:
    """Fetch, normalize, identify, and append one complete SharpAPI snapshot."""
    stats = PitcherKCollectionStats()
    try:
        sweep = SharpSweep()
        raw_rows = await fetch_sharp_odds(
            "mlb", SHARP_PITCHER_K_MARKET, sweep=sweep
        )
        captured_at = datetime.now(router_utils.UTC)
        rows = []
        for raw in raw_rows:
            try:
                normalized = normalize_sharp_pitcher_k_row(
                    raw,
                    target_date=target_date,
                    schedule=schedule,
                    collection_id=collection_id,
                    captured_at=captured_at,
                )
                if normalized is not None:
                    rows.append(normalized)
            except (KeyError, TypeError, ValueError) as exc:
                stats.malformed_prices += 1
                if stats.malformed_prices <= 10:
                    logger.warning(
                        "SharpAPI MLB pitcher-K row rejected event=%s selection=%s: %s",
                        raw.get("event_id") if isinstance(raw, dict) else None,
                        raw.get("selection_id") if isinstance(raw, dict) else None,
                        exc,
                    )

        stats.outcomes_found = len(rows)
        identity = reconcile_pitcher_k_identities(rows, schedule, captured_at)
        stats.identity_events_found = identity.events_found
        stats.identity_events_resolved = identity.events_resolved
        stats.identity_pitchers_found = identity.pitchers_found
        stats.identity_pitchers_resolved = identity.pitchers_resolved
        stats.identity_frozen_rows_skipped = identity.frozen_rows_skipped
        stats.identity_succeeded = identity.succeeded
        persist_pitcher_k_rows(rows, stats)
        logger.info(
            "SharpAPI MLB pitcher-K collector: %d rows, %d rejected, %d appended, "
            "%d deduplicated, identity=%d/%d pitchers",
            stats.rows_found,
            stats.malformed_prices,
            stats.rows_inserted,
            stats.rows_deduplicated,
            stats.identity_pitchers_resolved,
            stats.identity_pitchers_found,
        )
    except Exception:
        logger.exception("SharpAPI MLB pitcher-K collection failed")
    return stats
