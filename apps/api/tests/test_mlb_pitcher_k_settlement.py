import asyncio
from datetime import date, datetime, timezone

import pytest

from routers import mlb
from routers.mlb_pitcher_k_settlement import (
    IdentityBatch,
    build_identity_rows,
    build_pitching_result_rows,
    load_settlement_status_counts,
    load_stale_settlement_counts,
    persist_identity_batch,
    persist_pitching_results,
)
from routers import mlb_pitcher_k_settlement as settlement


CAPTURED_AT = datetime(2026, 8, 17, 16, tzinfo=timezone.utc)


def test_settlement_schedule_fetch_can_fail_closed(monkeypatch):
    class FailingClient:
        async def get(self, *_args, **_kwargs):
            raise RuntimeError("schedule unavailable")

    monkeypatch.setattr(mlb, "get_http_client", lambda: FailingClient())

    with pytest.raises(RuntimeError, match="schedule unavailable"):
        asyncio.run(mlb.get_schedule("2026-08-17", raise_on_error=True))


def test_schedule_includes_postseason_and_excludes_spring_games(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            def game(game_pk, game_type):
                return {
                    "gamePk": game_pk,
                    "gameType": game_type,
                    "gameDate": "2026-09-29T18:00:00Z",
                    "officialDate": "2026-09-29",
                    "status": {
                        "abstractGameState": "Preview",
                        "detailedState": "Scheduled",
                    },
                    "teams": {
                        "away": {"team": {"id": 143, "abbreviation": "PHI"}},
                        "home": {"team": {"id": 144, "abbreviation": "ATL"}},
                    },
                }

            return {"dates": [{"games": [game(849845, "F"), game(1, "S")]}]}

    class Client:
        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(mlb, "get_http_client", lambda: Client())

    games = asyncio.run(mlb.get_schedule("2026-09-29", raise_on_error=True))

    assert [game["gamePk"] for game in games] == ["849845"]


def snapshot(
    event_id: str,
    player_id: str,
    player_name: str,
    starts_at: str,
) -> dict:
    return {
        "event_id": event_id,
        "game_date": "2026-08-17",
        "starts_at": starts_at,
        "home_team_id": "CINCINNATI_REDS_MLB",
        "away_team_id": "STLOUIS_CARDINALS_MLB",
        "player_id": player_id,
        "player_name": player_name,
    }


def scheduled_game(game_pk: str, starts_at: str, pitcher_id: int, pitcher: str) -> dict:
    return {
        "gamePk": game_pk,
        "officialDate": "2026-08-17",
        "startsAt": starts_at,
        "awayAbbr": "STL",
        "homeAbbr": "CIN",
        "awayPitcherId": pitcher_id,
        "awayPitcher": pitcher,
        "homePitcherId": 999,
        "homePitcher": "Other Pitcher",
        "abstractGameState": "Preview",
        "detailedState": "Scheduled",
    }


def test_doubleheader_events_resolve_to_distinct_game_ids_by_start_time():
    rows = [
        snapshot("SGO-1", "P-1", "Andre Pallante", "2026-08-17T17:10:00Z"),
        snapshot("SGO-2", "P-2", "Miles Mikolas", "2026-08-17T23:10:00Z"),
    ]
    schedule = [
        scheduled_game("801", "2026-08-17T17:10:00Z", 1001, "Andre Pallante"),
        scheduled_game("802", "2026-08-17T23:10:00Z", 1002, "Miles Mikolas"),
    ]

    batch = build_identity_rows(rows, schedule, CAPTURED_AT)

    assert [row["mlb_game_pk"] for row in batch.event_rows] == [801, 802]
    assert all(row["resolution_method"] == "teams_start_time" for row in batch.event_rows)
    assert [row["mlb_player_id"] for row in batch.pitcher_rows] == [1001, 1002]


def test_pitcher_resolution_uses_event_candidates_not_stale_sgo_team():
    row = snapshot("SGO-1", "STALE-TEAM", "José Berríos Jr.", "2026-08-17T17:10:00Z")
    row["player_team_id"] = "DETROIT_TIGERS_MLB"
    game = scheduled_game("801", "2026-08-17T17:10:00Z", 1001, "Jose Berrios")

    batch = build_identity_rows([row], [game], CAPTURED_AT)

    assert batch.pitcher_rows[0]["mlb_player_id"] == 1001
    assert batch.pitcher_rows[0]["resolution_status"] == "resolved"


def test_unannounced_probable_remains_retryable():
    row = snapshot("SGO-1", "P-1", "Andre Pallante", "2026-08-17T17:10:00Z")
    game = scheduled_game("801", "2026-08-17T17:10:00Z", 1001, "Andre Pallante")
    game["awayPitcherId"] = None
    game["awayPitcher"] = None

    batch = build_identity_rows([row], [game], CAPTURED_AT)

    pitcher = batch.pitcher_rows[0]
    assert pitcher["mlb_player_id"] is None
    assert pitcher["resolution_status"] == "unresolved"
    assert pitcher["last_attempted_at"] == CAPTURED_AT.isoformat()


def test_boxscore_results_include_starter_and_reliever():
    feed = {
        "gameData": {
            "status": {
                "abstractGameState": "Final",
                "detailedState": "Final",
            }
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "pitchers": [1001, 1002],
                        "players": {
                            "ID1001": {
                                "person": {"id": 1001, "fullName": "Starter One"},
                                "stats": {"pitching": {
                                    "strikeOuts": 7,
                                    "inningsPitched": "6.1",
                                    "battersFaced": 24,
                                    "numberOfPitches": 96,
                                }},
                            },
                            "ID1002": {
                                "person": {"id": 1002, "fullName": "Reliever One"},
                                "stats": {"pitching": {
                                    "strikeOuts": 2,
                                    "inningsPitched": "1.2",
                                    "battersFaced": 6,
                                    "pitchesThrown": 22,
                                }},
                            },
                        },
                    },
                    "home": {"pitchers": [], "players": {}},
                }
            }
        },
    }

    rows = build_pitching_result_rows(
        801,
        date(2026, 8, 17),
        feed,
        CAPTURED_AT,
    )

    assert len(rows) == 2
    assert rows[0]["did_start"] is True
    assert rows[0]["outs_recorded"] == 19
    assert rows[0]["pitches_thrown"] == 96
    assert rows[1]["did_start"] is False
    assert rows[1]["outs_recorded"] == 5
    assert rows[1]["pitches_thrown"] == 22


def test_missing_strikeout_stat_is_not_coerced_to_zero():
    feed = {
        "gameData": {
            "status": {
                "abstractGameState": "Final",
                "detailedState": "Final",
            }
        },
        "liveData": {
            "boxscore": {
                "teams": {
                    "away": {
                        "pitchers": [1001],
                        "players": {
                            "ID1001": {
                                "person": {"fullName": "Starter One"},
                                "stats": {"pitching": {}},
                            }
                        },
                    },
                    "home": {"pitchers": [], "players": {}},
                }
            }
        },
    }

    rows = build_pitching_result_rows(
        801,
        date(2026, 8, 17),
        feed,
        CAPTURED_AT,
    )

    assert len(rows) == 1
    assert rows[0]["strikeouts"] is None
    assert rows[0]["did_start"] is True


class Query:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.mode = "select"
        self.rows = []
        self.filters = []

    def select(self, *_args):
        self.mode = "select"
        return self

    def in_(self, column, values):
        self.filters.append(("in", column, values))
        return self

    def eq(self, column, value):
        self.filters.append(("eq", column, value))
        return self

    def lt(self, column, value):
        self.filters.append(("lt", column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def upsert(self, rows, **_kwargs):
        self.mode = "upsert"
        self.rows = rows
        return self

    def update(self, values):
        self.mode = "update"
        self.rows = values
        return self

    def execute(self):
        if self.mode == "upsert":
            self.database.writes.setdefault(self.table, []).extend(self.rows)
            return type("Response", (), {"data": self.rows})()
        selected = self.database.existing.get(self.table, [])
        for operator, column, value in self.filters:
            if operator == "eq":
                selected = [row for row in selected if row.get(column) == value]
            elif operator == "in":
                selected = [row for row in selected if row.get(column) in value]
            else:
                selected = [row for row in selected if row.get(column) < value]
        if self.mode == "update":
            self.database.updates.setdefault(self.table, []).append({
                "values": self.rows,
                "filters": self.filters,
            })
            # Supabase updates need not return a representation. Production
            # code must not depend on response.data to find mapped events.
            return type("Response", (), {"data": []})()
        return type(
            "Response",
            (),
            {"data": selected},
        )()


class Database:
    def __init__(self, existing):
        self.existing = existing
        self.writes = {}
        self.updates = {}

    def table(self, name):
        return Query(self, name)


def test_persistence_does_not_downgrade_a_resolved_mapping(monkeypatch):
    existing = {
        "mlb_pitcher_k_event_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "mlb_game_pk": 801,
            "resolution_status": "resolved",
            "resolution_method": "teams_date",
            "resolved_at": "earlier",
            "frozen_at": None,
        }],
        "mlb_pitcher_k_player_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "provider_player_id": "P-1",
            "mlb_player_id": 1001,
            "mlb_player_name": "Andre Pallante",
            "resolution_status": "resolved",
            "resolution_method": "probable_name",
            "resolved_at": "earlier",
            "frozen_at": None,
        }],
    }
    database = Database(existing)
    monkeypatch.setattr(settlement.router_utils, "supabase", database)
    batch = IdentityBatch(
        event_rows=[{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "mlb_game_pk": None,
            "resolution_status": "unresolved",
            "last_attempted_at": "later",
            "updated_at": "later",
        }],
        pitcher_rows=[{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "provider_player_id": "P-1",
            "mlb_player_id": None,
            "resolution_status": "unresolved",
            "last_attempted_at": "later",
            "updated_at": "later",
        }],
    )

    stats = persist_identity_batch(batch)

    event = database.writes["mlb_pitcher_k_event_map"][0]
    pitcher = database.writes["mlb_pitcher_k_player_map"][0]
    assert stats.succeeded is True
    assert event["mlb_game_pk"] == 801
    assert event["resolution_status"] == "resolved"
    assert pitcher["mlb_player_id"] == 1001
    assert pitcher["resolution_status"] == "resolved"


def test_persistence_skips_frozen_mappings(monkeypatch):
    existing = {
        "mlb_pitcher_k_event_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "resolution_status": "resolved",
            "frozen_at": "2026-08-18T03:00:00Z",
        }],
        "mlb_pitcher_k_player_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "provider_player_id": "P-1",
            "resolution_status": "resolved",
            "frozen_at": "2026-08-18T03:00:00Z",
        }],
    }
    database = Database(existing)
    monkeypatch.setattr(settlement.router_utils, "supabase", database)
    batch = IdentityBatch(
        event_rows=[{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "resolution_status": "resolved",
        }],
        pitcher_rows=[{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "provider_player_id": "P-1",
            "resolution_status": "resolved",
        }],
    )

    stats = persist_identity_batch(batch)

    assert stats.succeeded is True
    assert stats.frozen_rows_skipped == 2
    assert database.writes == {}


def test_final_game_without_feed_rows_stays_unfetched_and_unfrozen(monkeypatch):
    database = Database({
        "mlb_pitcher_k_event_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "mlb_game_pk": 801,
        }],
    })
    monkeypatch.setattr(settlement.router_utils, "supabase", database)
    schedule = [{
        "gamePk": "801",
        "abstractGameState": "Final",
        "detailedState": "Final",
    }]

    stats = persist_pitching_results([], schedule, CAPTURED_AT)

    assert stats.succeeded is True
    assert stats.final_games == 0
    assert stats.frozen_events == 0
    assert database.updates == {}


def test_final_game_with_feed_rows_marks_results_fetched_and_freezes(monkeypatch):
    database = Database({
        "mlb_pitcher_k_event_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-1",
            "mlb_game_pk": 801,
        }],
    })
    monkeypatch.setattr(settlement.router_utils, "supabase", database)
    schedule = [{
        "gamePk": "801",
        "abstractGameState": "Final",
        "detailedState": "Final",
    }]
    rows = [{"mlb_game_pk": 801, "mlb_player_id": 1001, "strikeouts": 7}]

    stats = persist_pitching_results(rows, schedule, CAPTURED_AT)

    assert stats.succeeded is True
    assert stats.final_games == 1
    assert stats.frozen_events == 1
    event_update = database.updates["mlb_pitcher_k_event_map"][0]
    pitcher_update = database.updates["mlb_pitcher_k_player_map"][0]
    assert event_update["values"]["frozen_at"] == CAPTURED_AT.isoformat()
    assert event_update["values"]["results_fetched_at"] == CAPTURED_AT.isoformat()
    assert pitcher_update["filters"] == [
        ("eq", "provider", "sportsgameodds"),
        ("eq", "provider_event_id", "SGO-1"),
    ]


def test_cancelled_game_without_feed_rows_marks_terminal_and_freezes(monkeypatch):
    database = Database({
        "mlb_pitcher_k_event_map": [{
            "provider": "sportsgameodds",
            "provider_event_id": "SGO-CANCELLED",
            "mlb_game_pk": 801,
        }],
    })
    monkeypatch.setattr(settlement.router_utils, "supabase", database)
    schedule = [{
        "gamePk": "801",
        "abstractGameState": "Final",
        "detailedState": "Cancelled: Rain",
    }]

    stats = persist_pitching_results([], schedule, CAPTURED_AT)

    assert stats.succeeded is True
    assert stats.final_games == 1
    assert stats.frozen_events == 1
    event_update = database.updates["mlb_pitcher_k_event_map"][0]
    assert event_update["values"]["mlb_detailed_state"] == "Cancelled: Rain"
    assert "results_fetched_at" not in event_update["values"]


def test_cancelled_game_does_not_fail_settlement_projection(monkeypatch):
    schedule = [{
        "gamePk": "823490",
        "abstractGameState": "Final",
        "detailedState": "Cancelled: Rain",
    }]
    feed_calls = []

    async def fake_schedule(*_args, **_kwargs):
        return schedule

    async def fake_feed(game_pk):
        feed_calls.append(game_pk)
        return None

    monkeypatch.setattr(mlb, "get_schedule", fake_schedule)
    monkeypatch.setattr(mlb, "get_mlb_game_feed", fake_feed)
    monkeypatch.setattr(mlb, "load_snapshot_identity_sources", lambda _date: [])
    monkeypatch.setattr(
        mlb,
        "reconcile_pitcher_k_identities",
        lambda *_args: type("IdentityStats", (), {
            "events_found": 0,
            "events_resolved": 0,
            "pitchers_found": 0,
            "pitchers_resolved": 0,
            "succeeded": True,
        })(),
    )
    monkeypatch.setattr(
        mlb,
        "persist_pitching_results",
        lambda *_args: type("ResultStats", (), {
            "result_rows": 0,
            "succeeded": True,
            "frozen_events": 1,
        })(),
    )
    monkeypatch.setattr(mlb, "load_settlement_status_counts", lambda _date: {})

    result = asyncio.run(mlb._settle_pitcher_k_date(date(2026, 9, 27)))

    assert feed_calls == []
    assert result["cancelled_games"] == 1
    assert result["projection_failures"] == []
    assert result["success"] is True


def test_status_counts_surface_policy_backlog(monkeypatch):
    database = Database({
        "mlb_pitcher_k_settlement_status_counts": [{
            "game_date": "2026-08-17",
            "settlement_status": "policy_pending",
            "observation_count": 12,
            "game_count": 1,
        }],
    })
    monkeypatch.setattr(settlement.router_utils, "supabase", database)

    counts = load_settlement_status_counts(date(2026, 8, 17))

    assert counts == {"policy_pending": {"observations": 12, "games": 1}}


def test_stale_counts_find_retryable_rows_outside_lookback(monkeypatch):
    database = Database({
        "mlb_pitcher_k_settlement_status_counts": [
            {
                "game_date": "2026-08-12",
                "settlement_status": "pending",
                "observation_count": 8,
                "game_count": 1,
            },
            {
                "game_date": "2026-08-13",
                "settlement_status": "unresolved_identity",
                "observation_count": 4,
                "game_count": 1,
            },
            {
                "game_date": "2026-08-12",
                "settlement_status": "policy_pending",
                "observation_count": 20,
                "game_count": 2,
            },
            {
                "game_date": "2026-08-17",
                "settlement_status": "pending",
                "observation_count": 6,
                "game_count": 1,
            },
        ],
    })
    monkeypatch.setattr(settlement.router_utils, "supabase", database)

    counts = load_stale_settlement_counts(date(2026, 8, 15))

    assert counts == {
        "beforeDate": "2026-08-15",
        "oldestDate": "2026-08-12",
        "observations": 12,
        "byStatus": {
            "pending": {"observations": 8, "games": 1},
            "unresolved_identity": {"observations": 4, "games": 1},
        },
    }
