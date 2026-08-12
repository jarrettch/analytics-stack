from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from routers.mlb_pitcher_k_odds import (
    INSERT_CHUNK_SIZE,
    IDEMPOTENCY_COLUMNS,
    _market_phase,
    build_pitcher_k_snapshot_rows,
    collection_id_for,
    collect_pitcher_k_snapshots,
)


class _InsertQuery:
    def __init__(self, inserted, upsert_kwargs, fail=False, deduplicate=False):
        self.inserted = inserted
        self.upsert_kwargs = upsert_kwargs
        self.fail = fail
        self.deduplicate = deduplicate
        self.rows = []

    def upsert(self, rows, **kwargs):
        self.inserted.append(rows)
        self.upsert_kwargs.append(kwargs)
        self.rows = rows
        self.kwargs = kwargs
        return self

    def execute(self):
        if self.fail:
            raise RuntimeError("insert failed")
        return SimpleNamespace(data=[] if self.deduplicate else self.rows)


class _Supabase:
    def __init__(self, fail=False, deduplicate=False):
        self.tables = []
        self.inserted = []
        self.upsert_kwargs = []
        self.fail = fail
        self.deduplicate = deduplicate

    def table(self, table_name):
        self.tables.append(table_name)
        return _InsertQuery(
            self.inserted,
            self.upsert_kwargs,
            self.fail,
            self.deduplicate,
        )


class PitcherKOddsRowsTest(unittest.TestCase):
    def test_saved_live_sgo_fixture_matches_parser(self):
        fixture_path = (
            Path(__file__).parent / "fixtures" / "sgo_mlb_pitcher_k_event.json"
        )
        event = json.loads(fixture_path.read_text())

        rows, outcomes, malformed, events_skipped = build_pitcher_k_snapshot_rows(
            [event], datetime(2026, 8, 12, 1, tzinfo=timezone.utc)
        )

        self.assertEqual(outcomes, 1)
        self.assertEqual(malformed, 0)
        self.assertEqual(events_skipped, 0)
        # The finalized main price is unavailable, but the live response retained
        # its open/close fields, so the closing observation must still be captured.
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["is_alternate"])
        self.assertEqual(rows[0]["market_phase"], "final")
        self.assertEqual(rows[0]["open_line"], 4.5)
        self.assertEqual(rows[0]["close_line"], 5.5)
        self.assertEqual(rows[0]["close_american_odds"], -110)

    def test_collection_key_is_stable_for_workflow_reruns(self):
        first = collection_id_for("123456-today")
        self.assertEqual(first, collection_id_for("123456-today"))
        self.assertNotEqual(first, collection_id_for("123457-today"))

    def test_market_phase_covers_all_sgo_states(self):
        cases = [
            ({"started": False}, "pregame"),
            ({"started": True}, "live"),
            ({"started": True, "ended": True}, "final"),
            ({"started": True, "finalized": True}, "final"),
            ({"started": True, "cancelled": True}, "cancelled"),
            ({}, "unknown"),
        ]
        for status, expected in cases:
            with self.subTest(status=status):
                self.assertEqual(_market_phase(status), expected)

    def test_flattens_available_pitcher_k_books_only(self):
        events = [{
            "eventID": "MLB_EVENT_1",
            "status": {
                "startsAt": "2026-08-12T02:10:00Z",
                "started": False,
                "ended": False,
                "finalized": False,
                "cancelled": False,
            },
            "teams": {
                "away": {"teamID": "COLORADO_ROCKIES_MLB"},
                "home": {"teamID": "LOS_ANGELES_DODGERS_MLB"},
            },
            "players": {
                "BLAKE_SNELL_1_MLB": {
                    "name": "Blake Snell",
                    "teamID": "LOS_ANGELES_DODGERS_MLB",
                }
            },
            "odds": {
                "pitching_strikeouts-BLAKE_SNELL_1_MLB-game-ou-over": {
                    "oddID": "pitching_strikeouts-BLAKE_SNELL_1_MLB-game-ou-over",
                    "marketName": "Blake Snell Strikeouts Over/Under",
                    "statID": "pitching_strikeouts",
                    "statEntityID": "BLAKE_SNELL_1_MLB",
                    "playerID": "BLAKE_SNELL_1_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "over",
                    "byBookmaker": {
                        "betonline": {
                            "available": True,
                            "overUnder": "6.5",
                            "odds": "-115",
                            "openOverUnder": "5.5",
                            "openOdds": "+105",
                            "closeOverUnder": "6.5",
                            "closeOdds": "-110",
                            "lastUpdatedAt": "2026-08-11T17:02:03Z",
                            "altLines": [{
                                "available": True,
                                "overUnder": "7.5",
                                "odds": "+125",
                                "openOverUnder": "7.5",
                                "openOdds": "+130",
                            }],
                        },
                        "closedbook": {
                            "available": False,
                            "overUnder": "6.5",
                            "odds": "+100",
                        },
                    },
                },
                "points-home-game-ml-home": {
                    "statID": "points",
                    "periodID": "game",
                    "betTypeID": "ml",
                    "sideID": "home",
                },
            },
        }]

        rows, outcomes, malformed, events_skipped = build_pitcher_k_snapshot_rows(
            events,
            datetime(2026, 8, 11, 17, 5, tzinfo=timezone.utc),
            collection_id="11111111-1111-1111-1111-111111111111",
        )

        self.assertEqual(outcomes, 1)
        self.assertEqual(malformed, 0)
        self.assertEqual(events_skipped, 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["game_date"], "2026-08-11")
        self.assertEqual(rows[0]["player_name"], "Blake Snell")
        self.assertEqual(rows[0]["bookmaker_id"], "betonline")
        self.assertEqual(rows[0]["line"], 6.5)
        self.assertEqual(rows[0]["american_odds"], -115)
        self.assertEqual(rows[0]["open_line"], 5.5)
        self.assertEqual(rows[0]["open_american_odds"], 105)
        self.assertEqual(rows[0]["close_line"], 6.5)
        self.assertEqual(rows[0]["close_american_odds"], -110)
        self.assertEqual(rows[0]["market_phase"], "pregame")
        self.assertFalse(rows[0]["event_started"])
        self.assertFalse(rows[0]["is_alternate"])
        self.assertTrue(rows[1]["is_alternate"])
        self.assertEqual(rows[1]["line"], 7.5)
        self.assertEqual(
            rows[0]["collection_id"], "11111111-1111-1111-1111-111111111111"
        )

    def test_skips_and_counts_malformed_prices(self):
        events = [{
            "eventID": "MLB_EVENT_MALFORMED_PRICE",
            "status": {},
            "players": {},
            "odds": {
                "pitching_strikeouts-PITCHER_1_MLB-game-ou-under": {
                    "statID": "pitching_strikeouts",
                    "statEntityID": "PITCHER_1_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "under",
                    "byBookmaker": {
                        "missing-line": {"available": True, "odds": "-105"},
                        "bad-odds": {
                            "available": True,
                            "overUnder": "5.5",
                            "odds": "EVEN",
                        },
                    },
                }
            },
        }]

        rows, outcomes, malformed, events_skipped = build_pitcher_k_snapshot_rows(
            events, datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
        )

        self.assertEqual(outcomes, 1)
        self.assertEqual(malformed, 2)
        self.assertEqual(events_skipped, 0)
        self.assertEqual(rows, [])

        with patch("routers.mlb_pitcher_k_odds.router_utils.supabase", _Supabase()):
            stats = collect_pitcher_k_snapshots(
                events, datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
            )
        self.assertFalse(stats.succeeded)

    def test_missing_event_id_is_skipped_before_persistence(self):
        event = {
            "status": {"started": False},
            "odds": {
                "pitching_strikeouts-PITCHER_0_MLB-game-ou-over": {
                    "statID": "pitching_strikeouts",
                    "statEntityID": "PITCHER_0_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "over",
                    "byBookmaker": {
                        "betonline": {
                            "available": True,
                            "overUnder": "5.5",
                            "odds": "-110",
                        }
                    },
                }
            },
        }

        rows, outcomes, malformed, events_skipped = build_pitcher_k_snapshot_rows(
            [event], datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
        )

        self.assertEqual(rows, [])
        self.assertEqual(outcomes, 0)
        self.assertEqual(malformed, 0)
        self.assertEqual(events_skipped, 1)

    def test_collector_uses_insert_in_bounded_chunks(self):
        book_count = INSERT_CHUNK_SIZE + 1
        events = [{
            "eventID": "MLB_EVENT_2",
            "status": {"startsAt": "2026-08-12T02:10:00Z"},
            "players": {"PITCHER_2_MLB": {"name": "Test Pitcher"}},
            "odds": {
                "pitching_strikeouts-PITCHER_2_MLB-game-ou-over": {
                    "statID": "pitching_strikeouts",
                    "statEntityID": "PITCHER_2_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "over",
                    "byBookmaker": {
                        f"book-{index}": {
                            "available": True,
                            "overUnder": "5.5",
                            "odds": "-110",
                        }
                        for index in range(book_count)
                    },
                }
            },
        }]
        fake_supabase = _Supabase()

        with patch("routers.mlb_pitcher_k_odds.router_utils.supabase", fake_supabase):
            stats = collect_pitcher_k_snapshots(
                events, datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
            )

        self.assertEqual(stats.rows_found, book_count)
        self.assertEqual(stats.rows_inserted, book_count)
        self.assertTrue(stats.succeeded)
        self.assertEqual([len(chunk) for chunk in fake_supabase.inserted], [500, 1])
        self.assertEqual(fake_supabase.tables, ["mlb_pitcher_k_odds"] * 2)
        for kwargs in fake_supabase.upsert_kwargs:
            self.assertEqual(kwargs["on_conflict"], IDEMPOTENCY_COLUMNS)
            self.assertTrue(kwargs["ignore_duplicates"])

    def test_collector_reports_insert_failure(self):
        events = [{
            "eventID": "MLB_EVENT_3",
            "status": {"started": False},
            "players": {},
            "odds": {
                "pitching_strikeouts-PITCHER_3_MLB-game-ou-over": {
                    "statID": "pitching_strikeouts",
                    "statEntityID": "PITCHER_3_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "over",
                    "byBookmaker": {
                        "betonline": {
                            "available": True,
                            "overUnder": "5.5",
                            "odds": "-110",
                        }
                    },
                }
            },
        }]

        with patch(
            "routers.mlb_pitcher_k_odds.router_utils.supabase", _Supabase(fail=True)
        ):
            stats = collect_pitcher_k_snapshots(
                events, datetime(2026, 8, 11, 12, tzinfo=timezone.utc)
            )

        self.assertEqual(stats.rows_found, 1)
        self.assertEqual(stats.rows_inserted, 0)
        self.assertFalse(stats.succeeded)

    def test_exact_workflow_replay_is_deduplicated_and_successful(self):
        events = [{
            "eventID": "MLB_EVENT_4",
            "status": {"started": False},
            "players": {},
            "odds": {
                "pitching_strikeouts-PITCHER_4_MLB-game-ou-over": {
                    "statID": "pitching_strikeouts",
                    "statEntityID": "PITCHER_4_MLB",
                    "periodID": "game",
                    "betTypeID": "ou",
                    "sideID": "over",
                    "byBookmaker": {
                        "betonline": {
                            "available": True,
                            "overUnder": "5.5",
                            "odds": "-110",
                        }
                    },
                }
            },
        }]

        with patch(
            "routers.mlb_pitcher_k_odds.router_utils.supabase",
            _Supabase(deduplicate=True),
        ):
            stats = collect_pitcher_k_snapshots(
                events,
                datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
                collection_id_for("123456-today"),
            )

        self.assertEqual(stats.rows_inserted, 0)
        self.assertEqual(stats.rows_deduplicated, 1)
        self.assertTrue(stats.succeeded)


if __name__ == "__main__":
    unittest.main()
