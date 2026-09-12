from __future__ import annotations

import time
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from routers import mlb_pitcher_k_board as board


def _row(
    *,
    captured_at: str,
    collection_id: str,
    side: str,
    line: float,
    odds: int,
    bookmaker_id: str = "fanduel",
    is_alternate: bool = False,
    player_id: str = "ANDRE_PALLANTE_1_MLB",
    player_name: str = "Andre Pallante",
    event_id: str = "EVT1",
    market_phase: str = "pregame",
    open_line: float | None = None,
    open_odds: int | None = None,
) -> dict:
    return {
        "captured_at": captured_at,
        "collection_id": collection_id,
        "event_id": event_id,
        "starts_at": "2026-08-17T22:40:00+00:00",
        "home_team_id": "CINCINNATI_REDS_MLB",
        "away_team_id": "STLOUIS_CARDINALS_MLB",
        "player_id": player_id,
        "player_name": player_name,
        "player_team_id": "STLOUIS_CARDINALS_MLB",
        "side": side,
        "bookmaker_id": bookmaker_id,
        "is_alternate": is_alternate,
        "market_phase": market_phase,
        "line": line,
        "american_odds": odds,
        "open_line": open_line,
        "open_american_odds": open_odds,
    }


def _standard_day() -> list[dict]:
    """Two collections for one pitcher: fanduel moves 4.5 -> 5.5, dk holds."""
    early = "2026-08-17T16:00:00+00:00"
    late = "2026-08-17T22:00:00+00:00"
    return [
        _row(captured_at=early, collection_id="c1", side="over", line=4.5, odds=-108,
             open_line=4.5, open_odds=-111),
        _row(captured_at=early, collection_id="c1", side="under", line=4.5, odds=-112),
        _row(captured_at=early, collection_id="c1", side="over", line=4.5, odds=-105,
             bookmaker_id="draftkings"),
        _row(captured_at=early, collection_id="c1", side="under", line=4.5, odds=-115,
             bookmaker_id="draftkings"),
        _row(captured_at=late, collection_id="c2", side="over", line=5.5, odds=110,
             open_line=4.5, open_odds=-111),
        _row(captured_at=late, collection_id="c2", side="under", line=5.5, odds=-135),
        _row(captured_at=late, collection_id="c2", side="over", line=4.5, odds=-120,
             bookmaker_id="draftkings"),
        _row(captured_at=late, collection_id="c2", side="under", line=4.5, odds=-102,
             bookmaker_id="draftkings"),
        # Alternates: only the latest collection's ladder should surface.
        _row(captured_at=early, collection_id="c1", side="over", line=6.5, odds=200,
             is_alternate=True),
        _row(captured_at=late, collection_id="c2", side="over", line=6.5, odds=230,
             is_alternate=True),
        _row(captured_at=late, collection_id="c2", side="under", line=6.5, odds=-310,
             is_alternate=True),
        _row(captured_at=late, collection_id="c2", side="over", line=3.5, odds=-250,
             is_alternate=True),
    ]


class BuildBoardTest(unittest.TestCase):
    def test_latest_and_first_observations_per_book(self):
        entries = board.build_board(_standard_day())

        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["playerName"], "Andre Pallante")
        self.assertEqual(entry["playerTeam"], "STL")
        self.assertEqual(entry["opponentTeam"], "CIN")
        self.assertEqual(entry["homeTeam"], "CIN")

        fanduel, draftkings = entry["books"]
        self.assertEqual(fanduel["bookmakerId"], "fanduel")
        self.assertEqual(fanduel["current"],
                         {"line": 5.5, "overOdds": 110, "underOdds": -135,
                          "capturedAt": "2026-08-17T22:00:00+00:00",
                          "marketPhase": "pregame"})
        self.assertEqual(fanduel["first"]["line"], 4.5)
        self.assertEqual(fanduel["first"]["overOdds"], -108)
        self.assertEqual(fanduel["open"],
                         {"line": 4.5, "overOdds": -111, "underOdds": None})
        self.assertEqual(draftkings["bookmakerId"], "draftkings")
        self.assertEqual(draftkings["current"]["line"], 4.5)
        self.assertIsNone(draftkings["open"])

    def test_alternate_ladder_uses_latest_collection_only(self):
        entry = board.build_board(_standard_day())[0]
        fanduel = entry["books"][0]

        self.assertEqual(fanduel["alternates"], [
            {"line": 3.5, "overOdds": -250, "underOdds": None},
            {"line": 6.5, "overOdds": 230, "underOdds": -310},
        ])

    def test_mismatched_side_lines_are_not_paired(self):
        # Over already moved to 5.5 while under still shows 4.5: report the
        # over's line and omit the under price instead of mispricing it.
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=5.5, odds=-120),
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="under", line=4.5, odds=-110),
        ]
        current = board.build_board(rows)[0]["books"][0]["current"]

        self.assertEqual(current["line"], 5.5)
        self.assertEqual(current["overOdds"], -120)
        self.assertIsNone(current["underOdds"])

    def test_complete_pair_beats_stray_extra_side(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=5.5, odds=-115),
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="under", line=5.5, odds=-105),
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=4.5, odds=-160),
        ]
        current = board.build_board(rows)[0]["books"][0]["current"]

        self.assertEqual(current,
                         {"line": 5.5, "overOdds": -115, "underOdds": -105,
                          "capturedAt": "2026-08-17T16:00:00+00:00",
                          "marketPhase": "pregame"})

    def test_sgo_open_omits_side_that_opened_at_another_line(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=5.5, odds=-120, open_line=5.5, open_odds=-110),
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="under", line=5.5, odds=-105, open_line=4.5, open_odds=-115),
        ]
        book = board.build_board(rows)[0]["books"][0]

        self.assertEqual(book["open"],
                         {"line": 5.5, "overOdds": -110, "underOdds": None})

    def test_one_sided_collection_still_produces_a_pair(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="under", line=4.5, odds=-112),
        ]
        entry = board.build_board(rows)[0]
        current = entry["books"][0]["current"]

        self.assertEqual(current["line"], 4.5)
        self.assertIsNone(current["overOdds"])
        self.assertEqual(current["underOdds"], -112)

    def test_alternate_only_book_is_not_a_column(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=6.5, odds=200, is_alternate=True),
        ]
        self.assertEqual(board.build_board(rows), [])

    def test_doubleheader_starts_are_separate_entries(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=4.5, odds=-108, event_id="EVT1"),
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=5.5, odds=-110, event_id="EVT2"),
        ]
        entries = board.build_board(rows)

        self.assertEqual({entry["eventId"] for entry in entries}, {"EVT1", "EVT2"})

    def test_book_columns_follow_display_order(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=4.5, odds=-108, bookmaker_id=book)
            for book in ("bovada", "unknownbook", "fanduel")
        ]
        entry = board.build_board(rows)[0]

        self.assertEqual([b["bookmakerId"] for b in entry["books"]],
                         ["fanduel", "bovada", "unknownbook"])

    def test_invalid_player_team_does_not_invent_an_opponent(self):
        row = _row(
            captured_at="2026-08-17T16:00:00+00:00",
            collection_id="c1",
            side="over",
            line=4.5,
            odds=-108,
        )
        row["player_team_id"] = "DETROIT_TIGERS_MLB"

        entry = board.build_board([row])[0]

        self.assertEqual(entry["playerTeam"], "")
        self.assertEqual(entry["opponentTeam"], "")


class BuildHistoryTest(unittest.TestCase):
    def test_history_orders_collections_and_skips_alternates(self):
        books = board.build_history(_standard_day(), "ANDRE_PALLANTE_1_MLB", "EVT1")

        self.assertEqual([b["bookmakerId"] for b in books],
                         ["fanduel", "draftkings"])
        fanduel = books[0]
        self.assertEqual([p["line"] for p in fanduel["points"]], [4.5, 5.5])
        self.assertEqual(fanduel["points"][1]["overOdds"], 110)

    def test_history_for_unknown_player_is_empty(self):
        self.assertEqual(board.build_history(_standard_day(), "NOBODY", "EVT1"), [])

    def test_history_never_merges_doubleheader_events(self):
        rows = [
            _row(captured_at="2026-08-17T16:00:00+00:00", collection_id="c1",
                 side="over", line=4.5, odds=-108, event_id="EVT1"),
            _row(captured_at="2026-08-17T22:00:00+00:00", collection_id="c2",
                 side="over", line=6.5, odds=-110, event_id="EVT2"),
        ]

        first_start = board.build_history(rows, "ANDRE_PALLANTE_1_MLB", "EVT1")
        second_start = board.build_history(rows, "ANDRE_PALLANTE_1_MLB", "EVT2")

        self.assertEqual([p["line"] for p in first_start[0]["points"]], [4.5])
        self.assertEqual([p["line"] for p in second_start[0]["points"]], [6.5])


class BuildSettlementHistoryTest(unittest.TestCase):
    def test_summarizes_each_side_without_mixing_hit_rate_and_roi(self):
        rows = [
            {
                "game_date": "2026-08-17",
                "sgo_event_id": "EVT1",
                "sgo_player_id": "P1",
                "pitcher_name": "Test Pitcher",
                "bookmaker_id": "fanduel",
                "side": "over",
                "is_alternate": False,
                "line": 5.5,
                "american_odds": 120,
                "captured_at": "2026-08-17T16:00:00Z",
                "result_fetched_at": "2026-08-18T05:00:00Z",
                "strikeouts": 7,
                "settlement_status": "graded",
                "grade": "win",
                "profit_units": 1.2,
            },
            {
                "game_date": "2026-08-12",
                "sgo_event_id": "EVT0",
                "sgo_player_id": "P1",
                "pitcher_name": "Test Pitcher",
                "bookmaker_id": "fanduel",
                "side": "over",
                "is_alternate": False,
                "line": 6.5,
                "american_odds": -110,
                "captured_at": "2026-08-12T16:00:00Z",
                "result_fetched_at": "2026-08-13T05:00:00Z",
                "strikeouts": 4,
                "settlement_status": "graded",
                "grade": "loss",
                "profit_units": -1,
            },
            {
                "game_date": "2026-08-17",
                "sgo_event_id": "EVT1",
                "sgo_player_id": "P1",
                "pitcher_name": "Test Pitcher",
                "bookmaker_id": "fanduel",
                "side": "under",
                "is_alternate": False,
                "line": 5.5,
                "american_odds": -140,
                "captured_at": "2026-08-17T16:00:00Z",
                "strikeouts": 7,
                "settlement_status": "graded",
                "grade": "loss",
                "profit_units": -1,
            },
        ]

        history = board.build_settlement_history(rows, "P1")

        self.assertEqual(history["playerName"], "Test Pitcher")
        main = history["summary"]["main"]
        self.assertEqual(main["over"]["record"]["hitRate"], 0.5)
        self.assertEqual(main["over"]["record"]["n"], 2)
        self.assertEqual(main["over"]["perBookCloseRoi"]["roi"], 0.1)
        self.assertEqual(main["over"]["bestPriceRoi"]["roi"], 0.1)
        self.assertEqual(main["under"]["record"]["hitRate"], 0.0)
        self.assertEqual(history["settlements"][0]["date"], "2026-08-17")

    def test_alternates_do_not_change_main_line_summary(self):
        rows = [
            {
                "side": "over",
                "is_alternate": False,
                "settlement_status": "graded",
                "grade": "win",
                "profit_units": 1,
            },
            {
                "side": "over",
                "is_alternate": True,
                "settlement_status": "graded",
                "grade": "loss",
                "profit_units": -1,
            },
        ]

        summary = board.build_settlement_history(rows, "P1")["summary"]

        self.assertIsNone(summary["main"]["over"]["record"]["hitRate"])
        self.assertEqual(summary["alternates"]["over"]["hitRate"], 0.0)

    def test_push_is_excluded_from_hit_rate_and_included_in_roi_stake(self):
        rows = [
            {
                "sgo_event_id": "EVT1",
                "side": "over",
                "is_alternate": False,
                "line": 5,
                "american_odds": 100,
                "strikeouts": 6,
                "settlement_status": "graded",
                "grade": "win",
                "profit_units": 1,
            },
            {
                "sgo_event_id": "EVT2",
                "side": "over",
                "is_alternate": False,
                "line": 5,
                "american_odds": 100,
                "strikeouts": 5,
                "settlement_status": "graded",
                "grade": "push",
                "profit_units": 0,
            },
        ]

        over = board.build_settlement_history(rows, "P1")["summary"]["main"]["over"]

        self.assertEqual(over["record"]["hitRate"], 1.0)
        self.assertEqual(over["perBookCloseRoi"]["roi"], 0.5)
        self.assertEqual(over["bestPriceRoi"]["roi"], 0.5)

    def test_six_books_roll_up_to_one_start_and_one_record_decision(self):
        rows = []
        for book in ("fanduel", "draftkings", "betmgm", "caesars", "espnbet", "bovada"):
            for side, grade, profit in (("over", "win", 1), ("under", "loss", -1)):
                rows.append({
                    "game_date": "2026-08-17",
                    "mlb_game_pk": 123,
                    "sgo_event_id": "EVT1",
                    "bookmaker_id": book,
                    "side": side,
                    "is_alternate": False,
                    "line": 5.5,
                    "american_odds": 100,
                    "strikeouts": 7,
                    "settlement_status": "graded",
                    "grade": grade,
                    "profit_units": profit,
                })

        history = board.build_settlement_history(rows, "P1")

        self.assertEqual(len(history["starts"]), 1)
        self.assertEqual(len(history["starts"][0]["books"]), 12)
        self.assertIsNone(history["starts"][0]["lineRange"])
        self.assertEqual(history["starts"][0]["grade"]["over"], "win")
        self.assertEqual(history["summary"]["main"]["over"]["record"], {
            "n": 1,
            "wins": 1,
            "losses": 0,
            "pushes": 0,
            "hitRate": 1.0,
        })
        self.assertEqual(history["summary"]["main"]["over"]["perBookCloseRoi"]["bets"], 6)

    def test_split_lines_grade_at_modal_line_and_report_range(self):
        rows = []
        for book, line in (("fanduel", 5.5), ("draftkings", 5.5), ("betmgm", 6.5)):
            for side in ("over", "under"):
                grade = "win" if (side == "over") == (6 > line) else "loss"
                rows.append({
                    "mlb_game_pk": 123,
                    "sgo_event_id": "EVT1",
                    "bookmaker_id": book,
                    "side": side,
                    "is_alternate": False,
                    "line": line,
                    "american_odds": -110,
                    "strikeouts": 6,
                    "settlement_status": "graded",
                    "grade": grade,
                    "profit_units": 100 / 110 if grade == "win" else -1,
                })

        start = board.build_settlement_history(rows, "P1")["starts"][0]

        self.assertEqual(start["consensusLine"], 5.5)
        self.assertEqual(start["lineRange"], [5.5, 6.5])
        self.assertEqual(start["grade"], {"over": "win", "under": "loss"})

    def test_modal_line_tie_uses_lower_line(self):
        rows = [
            {
                "sgo_event_id": "EVT1",
                "bookmaker_id": book,
                "side": side,
                "is_alternate": False,
                "line": line,
                "american_odds": -110,
                "strikeouts": 6,
                "settlement_status": "graded",
                "grade": "win" if (side == "over") == (6 > line) else "loss",
                "profit_units": 0,
            }
            for book, line in (("fanduel", 5.5), ("draftkings", 6.5))
            for side in ("over", "under")
        ]

        start = board.build_settlement_history(rows, "P1")["starts"][0]

        self.assertEqual(start["consensusLine"], 5.5)
        self.assertEqual(start["grade"]["over"], "win")

    def test_pending_row_blocks_the_whole_start_from_record_and_roi(self):
        rows = [
            {
                "sgo_event_id": "EVT1",
                "bookmaker_id": "fanduel",
                "side": "over",
                "is_alternate": False,
                "line": 5.5,
                "american_odds": 120,
                "strikeouts": 7,
                "settlement_status": "graded",
                "grade": "win",
                "profit_units": 1.2,
            },
            {
                "sgo_event_id": "EVT1",
                "bookmaker_id": "draftkings",
                "side": "over",
                "is_alternate": False,
                "line": 5.5,
                "american_odds": 110,
                "strikeouts": 7,
                "settlement_status": "pending",
                "grade": None,
                "profit_units": None,
            },
        ]

        history = board.build_settlement_history(rows, "P1")
        over = history["summary"]["main"]["over"]

        self.assertEqual(history["starts"][0]["settlementStatus"], "pending")
        self.assertIsNone(history["starts"][0]["grade"]["over"])
        self.assertEqual(over["record"]["n"], 0)
        self.assertEqual(over["perBookCloseRoi"]["bets"], 0)
        self.assertEqual(over["bestPriceRoi"]["bets"], 0)

    def test_best_price_uses_highest_price_at_consensus_line(self):
        rows = [
            {
                "sgo_event_id": "EVT1",
                "bookmaker_id": book,
                "side": "over",
                "is_alternate": False,
                "line": line,
                "american_odds": odds,
                "strikeouts": 7,
                "settlement_status": "graded",
                "grade": "win",
                "profit_units": odds / 100,
            }
            for book, line, odds in (
                ("fanduel", 5.5, 110),
                ("draftkings", 5.5, 125),
                ("betmgm", 6.5, 200),
            )
        ]

        over = board.build_settlement_history(rows, "P1")["summary"]["main"]["over"]

        self.assertEqual(over["bestPriceRoi"], {
            "bets": 1,
            "roi": 1.25,
            "profitUnits": 1.25,
        })

    def test_flat_settlement_payload_remains_available(self):
        row = {
            "game_date": "2026-08-17",
            "mlb_game_pk": 123,
            "sgo_event_id": "EVT1",
            "bookmaker_id": "fanduel",
            "side": "over",
            "is_alternate": False,
            "line": 5.5,
            "american_odds": 120,
            "captured_at": "2026-08-17T16:00:00Z",
            "strikeouts": 7,
            "settlement_status": "graded",
            "grade": "win",
            "profit_units": 1.2,
        }

        settlement = board.build_settlement_history([row], "P1")["settlements"][0]

        self.assertEqual(settlement["eventId"], "EVT1")
        self.assertEqual(settlement["bookmakerId"], "fanduel")
        self.assertEqual(settlement["profitUnits"], 1.2)

    def test_counts_nonterminal_states_separately(self):
        rows = [
            {
                "side": "over",
                "is_alternate": False,
                "settlement_status": "void",
                "grade": None,
            },
            {
                "side": "over",
                "is_alternate": False,
                "settlement_status": "unresolved_identity",
                "grade": None,
            },
            {
                "side": "under",
                "is_alternate": False,
                "settlement_status": "policy_pending",
                "grade": None,
            },
            {
                "side": "under",
                "is_alternate": False,
                "settlement_status": "data_pending",
                "grade": None,
            },
        ]

        history = board.build_settlement_history(rows, "P1")

        statuses = history["summary"]["statuses"]
        self.assertEqual(statuses["void"], 1)
        self.assertEqual(statuses["unresolvedIdentity"], 1)
        self.assertEqual(statuses["policyPending"], 1)
        self.assertEqual(statuses["dataPending"], 1)


class _SelectQuery:
    def __init__(self, rows, calls, fail=False):
        self._rows = rows
        self._calls = calls
        self._fail = fail
        self._range = (0, 0)

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("select failed")
        self._calls.append(self._range)
        start, end = self._range
        return SimpleNamespace(data=self._rows[start:end + 1])


class _Supabase:
    def __init__(self, rows, fail=False):
        self.rows = rows
        self.fail = fail
        self.calls = []

    def table(self, _name):
        return _SelectQuery(self.rows, self.calls, self.fail)


class FetchSettlementRowsTest(unittest.TestCase):
    def test_paginates_instead_of_silently_truncating_summary_rows(self):
        rows = [{"id": index} for index in range(5)]
        fake = _Supabase(rows)

        with (
            patch.object(board.router_utils, "supabase", fake),
            patch.object(board, "SETTLEMENT_PAGE_SIZE", 2),
        ):
            fetched = board.fetch_settlement_rows("P1", include_alternates=True)

        self.assertEqual(fetched, rows)
        self.assertEqual(fake.calls, [(0, 1), (2, 3), (4, 5)])

    def test_explicitly_fails_if_the_safety_ceiling_is_reached(self):
        fake = _Supabase([{"id": index} for index in range(4)])

        with (
            patch.object(board.router_utils, "supabase", fake),
            patch.object(board, "SETTLEMENT_PAGE_SIZE", 2),
            patch.object(board, "SETTLEMENT_MAX_PAGES", 2),
            self.assertRaises(HTTPException) as context,
        ):
            board.fetch_settlement_rows("P1", include_alternates=True)

        self.assertEqual(context.exception.status_code, 503)


class FetchDayRowsTest(unittest.TestCase):
    def setUp(self):
        board._day_rows_cache.clear()

    def tearDown(self):
        board._day_rows_cache.clear()

    def test_paginates_until_short_page(self):
        rows = [{"id": i} for i in range(board.DAY_ROWS_PAGE_SIZE + 5)]
        fake = _Supabase(rows)

        with patch.object(board.router_utils, "supabase", fake):
            fetched, stale = board.fetch_day_rows(date(2026, 8, 17))

        self.assertEqual(len(fetched), len(rows))
        self.assertFalse(stale)
        self.assertEqual(len(fake.calls), 2)

    def test_second_call_within_ttl_hits_cache(self):
        fake = _Supabase([{"id": 1}])

        with patch.object(board.router_utils, "supabase", fake):
            board.fetch_day_rows(date(2026, 8, 17))
            _, stale = board.fetch_day_rows(date(2026, 8, 17))

        self.assertFalse(stale)
        self.assertEqual(len(fake.calls), 1)

    def test_failed_refresh_serves_expired_cache_flagged_stale(self):
        cached_rows = [{"id": 1}]
        expired = time.monotonic() - board.DAY_ROWS_CACHE_TTL - 1
        board._day_rows_cache["2026-08-17"] = (expired, cached_rows)

        with patch.object(board.router_utils, "supabase", _Supabase(None, fail=True)):
            fetched, stale = board.fetch_day_rows(date(2026, 8, 17))

        self.assertEqual(fetched, cached_rows)
        self.assertTrue(stale)

    def test_unconfigured_supabase_returns_503(self):
        with patch.object(board.router_utils, "supabase", None):
            with self.assertRaises(HTTPException) as ctx:
                board.fetch_day_rows(date(2026, 8, 17))

        self.assertEqual(ctx.exception.status_code, 503)


class ParseGameDateTest(unittest.TestCase):
    def test_default_uses_shared_sports_slate_date(self):
        with patch.object(
            board.router_utils,
            "slate_date_et",
            return_value=date(2026, 8, 17),
        ) as slate_date:
            self.assertEqual(board._parse_game_date(None), date(2026, 8, 17))

        slate_date.assert_called_once_with()

    def test_rejects_malformed_date(self):
        with self.assertRaises(HTTPException) as ctx:
            board._parse_game_date("08/17/2026")

        self.assertEqual(ctx.exception.status_code, 400)

    def test_accepts_iso_date(self):
        self.assertEqual(board._parse_game_date("2026-08-17"), date(2026, 8, 17))


if __name__ == "__main__":
    unittest.main()
