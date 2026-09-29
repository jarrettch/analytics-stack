"""Reconcile provider pitcher props with official MLB games and boxscores."""
from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime

from . import utils as router_utils
from .mlb_team_ids import get_tricode_from_sgo_team_id, get_tricode_from_team_name

logger = logging.getLogger(__name__)

EVENT_MAP_TABLE = "mlb_pitcher_k_event_map"
PITCHER_MAP_TABLE = "mlb_pitcher_k_player_map"
RESULTS_TABLE = "mlb_pitcher_k_results"
STATUS_COUNTS_VIEW = "mlb_pitcher_k_settlement_status_counts"
RETRYABLE_SETTLEMENT_STATUSES = (
    "pending",
    "unresolved_identity",
    "data_pending",
)
SNAPSHOT_TABLE = "mlb_pitcher_k_odds"
SNAPSHOT_PAGE_SIZE = 1000
SNAPSHOT_MAX_PAGES = 50


@dataclass
class IdentityBatch:
    event_rows: list[dict]
    pitcher_rows: list[dict]

    @property
    def events_resolved(self) -> int:
        return sum(row["resolution_status"] == "resolved" for row in self.event_rows)

    @property
    def pitchers_resolved(self) -> int:
        return sum(row["resolution_status"] == "resolved" for row in self.pitcher_rows)


@dataclass
class IdentityPersistenceStats:
    events_found: int = 0
    events_resolved: int = 0
    pitchers_found: int = 0
    pitchers_resolved: int = 0
    frozen_rows_skipped: int = 0
    succeeded: bool = False


@dataclass
class ResultsPersistenceStats:
    result_rows: int = 0
    games_updated: int = 0
    final_games: int = 0
    frozen_events: int = 0
    succeeded: bool = False


def normalize_person_name(value: str | None) -> str:
    folded = unicodedata.normalize("NFKD", value or "")
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = folded.lower().replace(".", "").replace("'", "").replace("-", " ")
    parts = [
        part
        for part in folded.split()
        if part not in {"jr", "sr", "ii", "iii", "iv"}
    ]
    return " ".join(parts)


def _parse_datetime(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def identity_sources_from_sgo_events(
    events: list[dict],
    captured_at: datetime,
) -> list[dict]:
    """Keep event and pitcher identity data even when no book price survives."""
    rows = []
    for event in events:
        event_id = event.get("eventID")
        if not event_id:
            continue
        status = event.get("status") or {}
        starts_at = status.get("startsAt")
        parsed_start = _parse_datetime(starts_at)
        game_date = (
            parsed_start.astimezone(router_utils.ET).date()
            if parsed_start is not None
            else captured_at.astimezone(router_utils.ET).date()
        )
        teams = event.get("teams") or {}
        base = {
            "provider": "sportsgameodds",
            "event_id": str(event_id),
            "game_date": game_date.isoformat(),
            "starts_at": starts_at,
            "home_team_id": (teams.get("home") or {}).get("teamID"),
            "away_team_id": (teams.get("away") or {}).get("teamID"),
        }
        rows.append(base)
        players = event.get("players") or {}
        pitcher_ids = set()
        for odd in (event.get("odds") or {}).values():
            if not isinstance(odd, dict) or odd.get("statID") != "pitching_strikeouts":
                continue
            player_id = odd.get("playerID") or odd.get("statEntityID")
            if player_id:
                pitcher_ids.add(str(player_id))
        for player_id in pitcher_ids:
            player = players.get(player_id) or {}
            rows.append({
                **base,
                "player_id": player_id,
                "player_name": player.get("name"),
                "player_team_id": player.get("teamID"),
            })
    return rows


def _event_candidate(snapshot: dict, schedule: list[dict]) -> tuple[dict | None, str]:
    away = _team_tricode(snapshot.get("away_team_id"))
    home = _team_tricode(snapshot.get("home_team_id"))
    candidates = [
        game
        for game in schedule
        if game.get("awayAbbr") == away and game.get("homeAbbr") == home
    ]
    if not candidates:
        return None, "unresolved"
    if len(candidates) == 1:
        return candidates[0], "teams_date"

    sgo_start = _parse_datetime(snapshot.get("starts_at"))
    if sgo_start is None:
        return None, "ambiguous"
    timed = [
        (abs((mlb_start - sgo_start).total_seconds()), game)
        for game in candidates
        if (mlb_start := _parse_datetime(game.get("startsAt"))) is not None
    ]
    if not timed:
        return None, "ambiguous"
    timed.sort(key=lambda item: item[0])
    if len(timed) > 1 and timed[0][0] == timed[1][0]:
        return None, "ambiguous"
    return timed[0][1], "teams_start_time"


def _team_tricode(value: str | None) -> str:
    return (
        get_tricode_from_sgo_team_id(value or "")
        or get_tricode_from_team_name(value or "")
    )


def _probable_candidates(game: dict) -> list[tuple[int, str]]:
    candidates = []
    for side in ("away", "home"):
        player_id = game.get(f"{side}PitcherId")
        name = game.get(f"{side}Pitcher")
        try:
            parsed_id = int(player_id)
        except (TypeError, ValueError):
            continue
        if name:
            candidates.append((parsed_id, str(name)))
    return candidates


def build_identity_rows(
    snapshot_rows: list[dict],
    schedule: list[dict],
    attempted_at: datetime,
) -> IdentityBatch:
    """Build retryable event and pitcher mappings from one MLB slate."""
    attempted = attempted_at.isoformat()
    event_sources: dict[tuple[str, str], dict] = {}
    pitcher_sources: dict[tuple[str, str, str], dict] = {}
    for row in snapshot_rows:
        provider = str(row.get("provider") or "sportsgameodds")
        event_id = row.get("event_id")
        player_id = row.get("player_id")
        if not event_id:
            continue
        event_sources.setdefault((provider, str(event_id)), row)
        if player_id:
            pitcher_sources.setdefault(
                (provider, str(event_id), str(player_id)), row
            )

    event_rows = []
    matched_games: dict[tuple[str, str], dict] = {}
    for (provider, event_id), source in event_sources.items():
        game, method = _event_candidate(source, schedule)
        status = "resolved" if game is not None else method
        if game is not None:
            matched_games[(provider, event_id)] = game
        event_rows.append({
            "provider": provider,
            "provider_event_id": event_id,
            "mlb_game_pk": int(game["gamePk"]) if game is not None else None,
            "game_date": source.get("game_date"),
            "away_team": _team_tricode(source.get("away_team_id")) or None,
            "home_team": _team_tricode(source.get("home_team_id")) or None,
            "provider_starts_at": source.get("starts_at"),
            "mlb_starts_at": game.get("startsAt") if game is not None else None,
            "resolution_status": status,
            "resolution_method": method if game is not None else None,
            "mlb_abstract_state": (
                game.get("abstractGameState") if game is not None else None
            ),
            "mlb_detailed_state": (
                game.get("detailedState") if game is not None else None
            ),
            "last_attempted_at": attempted,
            "resolved_at": attempted if game is not None else None,
            "updated_at": attempted,
        })

    pitcher_rows = []
    for (provider, event_id, player_id), source in pitcher_sources.items():
        game = matched_games.get((provider, event_id))
        normalized = normalize_person_name(source.get("player_name"))
        candidates = [
            candidate
            for candidate in _probable_candidates(game or {})
            if normalize_person_name(candidate[1]) == normalized
        ]
        resolved = candidates[0] if len(candidates) == 1 else None
        status = "resolved" if resolved else "ambiguous" if len(candidates) > 1 else "unresolved"
        pitcher_rows.append({
            "provider": provider,
            "provider_event_id": event_id,
            "provider_player_id": player_id,
            "mlb_player_id": resolved[0] if resolved else None,
            "provider_player_name": source.get("player_name"),
            "mlb_player_name": resolved[1] if resolved else None,
            "resolution_status": status,
            "resolution_method": "probable_name" if resolved else None,
            "last_attempted_at": attempted,
            "resolved_at": attempted if resolved else None,
            "updated_at": attempted,
        })
    return IdentityBatch(event_rows=event_rows, pitcher_rows=pitcher_rows)


def _existing_rows(table: str, columns: str, event_ids: list[str]) -> list[dict]:
    if not event_ids:
        return []
    return (
        router_utils.supabase.table(table)
        .select(columns)
        .in_("provider_event_id", event_ids)
        .execute()
        .data
        or []
    )


def _merge_mapping(incoming: dict, existing: dict | None) -> dict | None:
    if existing and existing.get("frozen_at"):
        return None
    if (
        existing
        and existing.get("resolution_status") == "resolved"
        and incoming.get("resolution_status") != "resolved"
    ):
        preserved = dict(incoming)
        for key, value in existing.items():
            if key not in {"last_attempted_at", "updated_at", "frozen_at"}:
                preserved[key] = value
        return preserved
    return incoming


def persist_identity_batch(batch: IdentityBatch) -> IdentityPersistenceStats:
    stats = IdentityPersistenceStats(
        events_found=len(batch.event_rows),
        events_resolved=batch.events_resolved,
        pitchers_found=len(batch.pitcher_rows),
        pitchers_resolved=batch.pitchers_resolved,
    )
    if not batch.event_rows:
        stats.succeeded = True
        return stats
    if not router_utils.supabase:
        logger.warning("Supabase not configured; skipping MLB pitcher-K identity maps")
        return stats

    event_ids = [row["provider_event_id"] for row in batch.event_rows]
    try:
        existing_events = {
            (row["provider"], row["provider_event_id"]): row
            for row in _existing_rows(
                EVENT_MAP_TABLE,
                "provider,provider_event_id,mlb_game_pk,resolution_status,resolution_method,"
                "mlb_starts_at,mlb_abstract_state,mlb_detailed_state,resolved_at,frozen_at",
                event_ids,
            )
        }
        event_rows = []
        for row in batch.event_rows:
            key = (row["provider"], row["provider_event_id"])
            merged = _merge_mapping(row, existing_events.get(key))
            if merged is None:
                stats.frozen_rows_skipped += 1
            else:
                event_rows.append(merged)
        if event_rows:
            (
                router_utils.supabase.table(EVENT_MAP_TABLE)
                .upsert(event_rows, on_conflict="provider,provider_event_id")
                .execute()
            )

        existing_pitchers = {
            (
                row["provider"],
                row["provider_event_id"],
                row["provider_player_id"],
            ): row
            for row in _existing_rows(
                PITCHER_MAP_TABLE,
                "provider,provider_event_id,provider_player_id,mlb_player_id,mlb_player_name,"
                "resolution_status,resolution_method,resolved_at,frozen_at",
                event_ids,
            )
        }
        pitcher_rows = []
        for row in batch.pitcher_rows:
            key = (
                row["provider"],
                row["provider_event_id"],
                row["provider_player_id"],
            )
            merged = _merge_mapping(row, existing_pitchers.get(key))
            if merged is None:
                stats.frozen_rows_skipped += 1
            else:
                pitcher_rows.append(merged)
        if pitcher_rows:
            (
                router_utils.supabase.table(PITCHER_MAP_TABLE)
                .upsert(
                    pitcher_rows,
                    on_conflict="provider,provider_event_id,provider_player_id",
                )
                .execute()
            )
        stats.succeeded = True
    except Exception:
        logger.exception("Failed to persist MLB pitcher-K identity maps")
    return stats


def reconcile_pitcher_k_identities(
    snapshot_rows: list[dict],
    schedule: list[dict],
    attempted_at: datetime,
) -> IdentityPersistenceStats:
    return persist_identity_batch(
        build_identity_rows(snapshot_rows, schedule, attempted_at)
    )


def load_snapshot_identity_sources(game_date: date) -> list[dict]:
    if not router_utils.supabase:
        raise RuntimeError("Supabase is not configured")
    rows = []
    columns = (
        "provider,event_id,game_date,starts_at,home_team_id,away_team_id,"
        "player_id,player_name,player_team_id"
    )
    for page in range(SNAPSHOT_MAX_PAGES):
        start = page * SNAPSHOT_PAGE_SIZE
        chunk = (
            router_utils.supabase.table(SNAPSHOT_TABLE)
            .select(columns)
            .eq("game_date", game_date.isoformat())
            .order("id")
            .range(start, start + SNAPSHOT_PAGE_SIZE - 1)
            .execute()
            .data
            or []
        )
        rows.extend(chunk)
        if len(chunk) < SNAPSHOT_PAGE_SIZE:
            return rows
    raise RuntimeError(
        f"Pitcher-K identity source exceeded {SNAPSHOT_MAX_PAGES} pages"
    )


def _safe_int(value, default: int | None = 0) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _innings_to_outs(value) -> int | None:
    if value is None:
        return None
    pieces = str(value).split(".", 1)
    try:
        innings = int(pieces[0])
        partial = int(pieces[1]) if len(pieces) > 1 else 0
    except ValueError:
        return None
    if partial not in {0, 1, 2}:
        return None
    return innings * 3 + partial


def is_cancelled_game(game: dict) -> bool:
    """True when MLB has terminally cancelled a game without a box score."""
    detailed_state = str(game.get("detailedState") or "").casefold()
    return detailed_state.startswith(("cancelled", "canceled"))


def build_pitching_result_rows(
    game_pk: int,
    game_date: date,
    feed: dict,
    fetched_at: datetime,
) -> list[dict]:
    """Project every pitcher who appeared into correction-safe MLB facts."""
    status = (feed.get("gameData") or {}).get("status") or {}
    abstract_state = status.get("abstractGameState") or "Unknown"
    detailed_state = status.get("detailedState") or "Unknown"
    teams = ((feed.get("liveData") or {}).get("boxscore") or {}).get("teams") or {}
    rows = []
    for side in ("away", "home"):
        team = teams.get(side) or {}
        pitcher_ids = team.get("pitchers") or []
        starter_id = _safe_int(pitcher_ids[0], None) if pitcher_ids else None
        players = team.get("players") or {}
        for raw_id in pitcher_ids:
            player_id = _safe_int(raw_id, None)
            if player_id is None:
                continue
            player = players.get(f"ID{player_id}") or {}
            person = player.get("person") or {}
            pitching = (player.get("stats") or {}).get("pitching") or {}
            innings_pitched = pitching.get("inningsPitched")
            strikeouts = _safe_int(pitching.get("strikeOuts"), None)
            rows.append({
                "mlb_game_pk": game_pk,
                "mlb_player_id": player_id,
                "game_date": game_date.isoformat(),
                "pitcher_name": person.get("fullName") or str(player_id),
                "strikeouts": strikeouts,
                "did_start": player_id == starter_id,
                "appeared": True,
                "innings_pitched": (
                    str(innings_pitched) if innings_pitched is not None else None
                ),
                "outs_recorded": _innings_to_outs(innings_pitched),
                "batters_faced": _safe_int(pitching.get("battersFaced"), None),
                "pitches_thrown": _safe_int(
                    pitching.get("numberOfPitches", pitching.get("pitchesThrown")),
                    None,
                ),
                "mlb_abstract_state": abstract_state,
                "mlb_detailed_state": detailed_state,
                "fetched_at": fetched_at.isoformat(),
                "updated_at": fetched_at.isoformat(),
            })
    return rows


def persist_pitching_results(
    rows: list[dict],
    schedule: list[dict],
    persisted_at: datetime,
) -> ResultsPersistenceStats:
    stats = ResultsPersistenceStats(result_rows=len(rows))
    if not router_utils.supabase:
        logger.warning("Supabase not configured; skipping MLB pitcher-K results")
        return stats
    timestamp = persisted_at.isoformat()
    fetched_game_pks = {
        row["mlb_game_pk"]
        for row in rows
        if isinstance(row.get("mlb_game_pk"), int)
    }
    try:
        if rows:
            (
                router_utils.supabase.table(RESULTS_TABLE)
                .upsert(rows, on_conflict="mlb_game_pk,mlb_player_id")
                .execute()
            )
        for game in schedule:
            try:
                game_pk = int(game["gamePk"])
            except (KeyError, TypeError, ValueError):
                continue
            is_final = game.get("abstractGameState") == "Final"
            is_cancelled = is_cancelled_game(game)
            if is_final and game_pk not in fetched_game_pks and not is_cancelled:
                # A final schedule state does not prove its boxscore was read.
                # Leave the mapping pending so an absent result cannot look
                # like a pitcher who never appeared.
                continue
            update = {
                "mlb_abstract_state": game.get("abstractGameState"),
                "mlb_detailed_state": game.get("detailedState"),
                "updated_at": timestamp,
            }
            if game_pk in fetched_game_pks:
                update["results_fetched_at"] = timestamp
            if is_final:
                update["frozen_at"] = timestamp
                stats.final_games += 1
            mapped_events = (
                router_utils.supabase.table(EVENT_MAP_TABLE)
                .select("provider,provider_event_id")
                .eq("mlb_game_pk", game_pk)
                .execute()
                .data
                or []
            )
            (
                router_utils.supabase.table(EVENT_MAP_TABLE)
                .update(update)
                .eq("mlb_game_pk", game_pk)
                .execute()
            )
            stats.games_updated += 1
            if is_final and mapped_events:
                for mapped in mapped_events:
                    if not mapped.get("provider_event_id"):
                        continue
                    (
                        router_utils.supabase.table(PITCHER_MAP_TABLE)
                        .update({"frozen_at": timestamp, "updated_at": timestamp})
                        .eq("provider", mapped["provider"])
                        .eq("provider_event_id", mapped["provider_event_id"])
                        .execute()
                    )
                stats.frozen_events += len(mapped_events)
        stats.succeeded = True
    except Exception:
        logger.exception("Failed to persist MLB pitcher-K results")
    return stats


def load_settlement_status_counts(game_date: date) -> dict[str, dict[str, int]]:
    if not router_utils.supabase:
        raise RuntimeError("Supabase is not configured")
    response = (
        router_utils.supabase.table(STATUS_COUNTS_VIEW)
        .select("settlement_status,observation_count,game_count")
        .eq("game_date", game_date.isoformat())
        .execute()
    )
    return {
        row["settlement_status"]: {
            "observations": int(row.get("observation_count") or 0),
            "games": int(row.get("game_count") or 0),
        }
        for row in (response.data or [])
        if row.get("settlement_status")
    }


def load_stale_settlement_counts(before_date: date) -> dict:
    """Count retryable settlements old enough to fall outside the cron window."""
    if not router_utils.supabase:
        raise RuntimeError("Supabase is not configured")
    response = (
        router_utils.supabase.table(STATUS_COUNTS_VIEW)
        .select("game_date,settlement_status,observation_count,game_count")
        .lt("game_date", before_date.isoformat())
        .in_("settlement_status", list(RETRYABLE_SETTLEMENT_STATUSES))
        .order("game_date")
        .execute()
    )
    rows = response.data or []
    by_status: dict[str, dict[str, int]] = {}
    for row in rows:
        status = row.get("settlement_status")
        if not status:
            continue
        bucket = by_status.setdefault(status, {"observations": 0, "games": 0})
        bucket["observations"] += int(row.get("observation_count") or 0)
        bucket["games"] += int(row.get("game_count") or 0)
    return {
        "beforeDate": before_date.isoformat(),
        "oldestDate": min(
            (row.get("game_date") for row in rows if row.get("game_date")),
            default=None,
        ),
        "observations": sum(
            bucket["observations"] for bucket in by_status.values()
        ),
        "byStatus": by_status,
    }
