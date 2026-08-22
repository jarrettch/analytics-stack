from __future__ import annotations

import unittest
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from routers.nfl import (
    _early_window_count,
    _early_window_matchup_keys,
    _project_score,
    _quarter_score,
    _status,
    _team_box_score,
    _to_float,
    _week_label,
    cron_fetch_odds,
    get_tricode_from_sgo_team_id,
)
from routers.utils import ET, odds_snapshot_hash, save_odds_snapshots


class _Query:
    def __init__(self, store, mode="select"):
        self.store = store
        self.mode = mode
        self.rows = []

    def select(self, *_args):
        self.mode = "select"
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, rows):
        self.mode = "insert"
        self.rows = rows
        return self

    def execute(self):
        if self.mode == "insert":
            self.store.inserted.extend(self.rows)
            return SimpleNamespace(data=self.rows)
        return SimpleNamespace(data=self.store.existing)


class _Supabase:
    def __init__(self, existing=None):
        self.existing = existing or []
        self.inserted = []

    def table(self, _name):
        return _Query(self)


def _snapshot(odds_data):
    return {
        "sport": "nfl",
        "provider": "sportsgameodds",
        "bookmaker": "betonline",
        "provider_event_id": "event-1",
        "game_date": "2026-08-13",
        "matchup_key": "DET@CIN",
        "starts_at": "2026-08-13T23:00:00Z",
        "captured_at": "2026-08-13T16:00:00Z",
        "odds_data": odds_data,
    }


class OddsHistoryTest(unittest.TestCase):
    def test_hash_is_stable_across_key_order(self):
        self.assertEqual(
            odds_snapshot_hash({"homeMoneyline": -120, "overUnder": 42.5}),
            odds_snapshot_hash({"overUnder": 42.5, "homeMoneyline": -120}),
        )

    def test_skips_only_the_consecutive_duplicate(self):
        previous = {"homeMoneyline": -120, "overUnder": 42.5}
        store = _Supabase(existing=[{
            "sport": "nfl",
            "provider": "sportsgameodds",
            "bookmaker": "betonline",
            "game_date": "2026-08-13",
            "matchup_key": "DET@CIN",
            "snapshot_hash": odds_snapshot_hash(previous),
            "captured_at": "2026-08-13T15:00:00Z",
        }])
        duplicate_stats = {}
        with patch("routers.utils.supabase", store):
            self.assertEqual(
                save_odds_snapshots([_snapshot(previous)], stats=duplicate_stats),
                0,
            )
            moved = {"homeMoneyline": -125, "overUnder": 42.5}
            self.assertEqual(save_odds_snapshots([_snapshot(moved)]), 1)
        self.assertTrue(duplicate_stats["persistence_ok"])
        self.assertEqual(duplicate_stats["deduplicated"], 1)
        self.assertEqual(store.inserted[0]["odds_data"]["homeMoneyline"], -125)

    def test_reports_snapshot_persistence_unavailable(self):
        stats = {}
        with patch("routers.utils.supabase", None):
            self.assertEqual(
                save_odds_snapshots(
                    [_snapshot({"homeMoneyline": -120})], stats=stats
                ),
                0,
            )
        self.assertEqual(stats["candidates"], 1)
        self.assertFalse(stats["persistence_ok"])


class NFLParsingTest(unittest.TestCase):
    def test_early_window_count_uses_eastern_start_time(self):
        starts = [
            "2026-09-13T16:30:00Z",
            "2026-09-13T17:00:00Z",
            "2026-09-13T20:05:00Z",
            "2026-09-13T17:00:00Z",
            "2026-09-14T00:20:00Z",
        ]
        self.assertEqual(_early_window_count(starts, date(2026, 9, 13)), 2)

    def test_early_window_matches_espn_team_keys(self):
        def event(starts_at, away, home):
            return {
                "date": starts_at,
                "competitions": [{"competitors": [
                    {"homeAway": "away", "team": {"abbreviation": away}},
                    {"homeAway": "home", "team": {"abbreviation": home}},
                ]}],
            }

        events = [
            event("2026-09-13T13:30:00Z", "JAX", "CHI"),  # 9:30am ET
            event("2026-09-13T17:00:00Z", "PIT", "CLE"),  # 1pm ET
            event("2026-09-13T20:05:00Z", "LV", "DEN"),   # 4:05pm ET
        ]
        self.assertEqual(
            _early_window_matchup_keys(events, date(2026, 9, 13)),
            {"PIT@CLE"},
        )

    def test_sgo_team_aliases(self):
        self.assertEqual(get_tricode_from_sgo_team_id("WASHINGTON_COMMANDERS_NFL"), "WSH")
        self.assertEqual(get_tricode_from_sgo_team_id("JAC"), "JAX")

    def test_preseason_week_labels(self):
        self.assertEqual(_week_label({"season": {"type": 1}, "week": {"number": 1}}), "HOF")
        self.assertEqual(_week_label({"season": {"type": 1}, "week": {"number": 2}}), "PRE W1")

    def test_live_and_final_status(self):
        live = {"status": {"period": 3, "displayClock": "7:12", "type": {"state": "in"}}}
        self.assertEqual(_status(live), (2, "Q3 7:12", 3, "7:12"))
        final_ot = {"status": {"period": 5, "type": {"state": "post"}}}
        self.assertEqual(_status(final_ot), (3, "F/OT", 5, None))

    def test_linescore_and_player_box_mapping(self):
        competitor = {
            "score": "23",
            "linescores": [{"displayValue": value} for value in ["0", "10", "7", "6"]],
        }
        self.assertEqual(_quarter_score(competitor)["q2"], 10)

        team_box = {"statistics": [
            {"name": "totalYards", "displayValue": "360"},
            {"name": "totalPenaltiesYards", "displayValue": "5-45"},
        ]}
        player_box = {"statistics": [{
            "name": "passing",
            "labels": ["C/ATT", "YDS", "TD", "INT", "SACKS", "RTG"],
            "athletes": [{
                "athlete": {"displayName": "Test QB"},
                "stats": ["17/24", "240", "2", "1", "3-18", "102.1"],
            }],
        }]}
        box = _team_box_score(team_box, player_box)
        self.assertIsNotNone(box)
        self.assertEqual(box["teamStats"]["penalties"], 5)
        self.assertEqual(box["passing"][0]["attempts"], 24)
        self.assertEqual(box["passing"][0]["sacks"], 3)

    def test_partial_box_score_placeholders_do_not_raise(self):
        self.assertEqual(_to_float("-"), 0.0)
        self.assertEqual(_to_float("--", 0.5), 0.5)
        self.assertEqual(_to_float("NaN", 0.5), 0.5)
        self.assertEqual(_to_float("Infinity", 0.5), 0.5)
        self.assertEqual(_to_float("-Infinity", 0.5), 0.5)
        self.assertEqual(_to_float("1,234"), 1234.0)

        player_box = {"statistics": [
            {
                "name": "passing",
                "labels": ["C/ATT", "YDS", "TD", "INT", "SACKS", "RTG"],
                "athletes": [{"athlete": {"displayName": "QB"}, "stats": ["-", "-", "-", "-", "-", "-"]}],
            },
            {
                "name": "rushing",
                "labels": ["CAR", "YDS", "AVG", "TD", "LONG"],
                "athletes": [{"athlete": {"displayName": "RB"}, "stats": ["-", "-", "--", "-", "-"]}],
            },
            {
                "name": "receiving",
                "labels": ["REC", "TGTS", "YDS", "AVG", "TD", "LONG"],
                "athletes": [{"athlete": {"displayName": "WR"}, "stats": ["-", "-", "-", "-", "-", "-"]}],
            },
        ]}
        box = _team_box_score(None, player_box)
        self.assertIsNotNone(box)
        self.assertEqual(box["passing"][0]["rating"], 0.0)
        self.assertEqual(box["rushing"][0]["average"], 0.0)
        self.assertEqual(box["receiving"][0]["average"], 0.0)

    def test_projection_ignores_malformed_team_context(self):
        away_stats = {"pointsFor": 24, "pointsAgainst": "--"}
        home_stats = {"pointsFor": 20, "pointsAgainst": 22}
        away, home, total = _project_score(away_stats, home_stats, {})
        self.assertEqual(total, 44)
        self.assertEqual(away + home, total)


class NFLVerificationTest(unittest.IsolatedAsyncioTestCase):
    async def test_verification_intersects_expected_and_candidate_matchups(self):
        target = datetime.now(ET).date()
        early_start = datetime.combine(target, time(13), ET).isoformat()

        def event(away, home):
            return {
                "date": early_start,
                "competitions": [{"competitors": [
                    {"homeAway": "away", "team": {"abbreviation": away}},
                    {"homeAway": "home", "team": {"abbreviation": home}},
                ]}],
            }

        async def fake_get_odds_map(*, target_date, collection_stats, request_time):
            self.assertEqual(target_date, target)
            self.assertEqual(request_time.date(), target)
            collection_stats.update({
                "pregame_snapshot_candidates": 2,
                "pregame_snapshot_matchups": ["PIT@CLE", "LV@DEN"],
                "snapshots_inserted": 1,
                "snapshots_deduplicated": 1,
                "snapshot_persistence_ok": True,
            })
            return {}, 2

        scoreboard = {"events": [
            event("PIT", "CLE"),
            event("BUF", "NYJ"),
        ]}
        with (
            patch("routers.nfl.get_odds_map", new=fake_get_odds_map),
            patch(
                "routers.nfl._scoreboard_for_date_with_status",
                new=AsyncMock(return_value=(scoreboard, True)),
            ),
        ):
            result = await cron_fetch_odds(verify_early_window=True)

        self.assertEqual(result["early_window_games"], 2)
        self.assertEqual(result["early_window_snapshot_candidates"], 1)

    async def test_verification_surfaces_scoreboard_failure(self):
        with (
            patch(
                "routers.nfl.get_odds_map",
                new=AsyncMock(return_value=({}, 0)),
            ),
            patch(
                "routers.nfl._scoreboard_for_date_with_status",
                new=AsyncMock(return_value=({}, False)),
            ),
        ):
            result = await cron_fetch_odds(verify_early_window=True)

        self.assertTrue(result["success"])
        self.assertFalse(result["early_window_scoreboard_ok"])

    async def test_ordinary_cron_does_not_fetch_scoreboard(self):
        scoreboard = AsyncMock()
        with (
            patch(
                "routers.nfl.get_odds_map",
                new=AsyncMock(return_value=({}, 0)),
            ),
            patch(
                "routers.nfl._scoreboard_for_date_with_status",
                new=scoreboard,
            ),
        ):
            result = await cron_fetch_odds()

        self.assertTrue(result["success"])
        self.assertFalse(result["early_window_verification_requested"])
        scoreboard.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
