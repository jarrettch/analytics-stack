"""Behaviour the port adds on top of the scripts (specs/tennis_fs_upload.md)."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from tennis_pp import model
from tennis_pp.config import DEFAULT_CONFIG
from tennis_pp.pricing import Event, _accept, build_events, match_event, price_board

START = "2026-11-20T18:00Z"
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def odds(event_id, home, away, market, selection, price, *, book="draftkings", line=None, side=None,
         main=True, player=None, league="atp", start=START, competition=None):
    return {
        "event_id": event_id, "league": league, "home_team": home, "away_team": away, "market_type": market,
        "sportsbook": book, "selection": selection, "selection_type": side, "player_name": player,
        "odds_american": price, "line": line, "is_main_line": main, "event_start_time": start,
        "competition_ref": {"id": competition} if competition else None,
    }


def match(event_id="e1", home="Alex Favorite", away="Ben Underdog", *, total=22.5, **kw):
    rows = [
        odds(event_id, home, away, "moneyline", home, -200, **kw),
        odds(event_id, home, away, "moneyline", away, 165, **kw),
    ]
    if total is not None:
        rows += [
            odds(event_id, home, away, "total_games", "Over", -110, line=total, side="over", **kw),
            odds(event_id, home, away, "total_games", "Under", -110, line=total, side="under", **kw),
            odds(event_id, home, away, "game_spread", home, -110, line=-3.5, **kw),
            odds(event_id, home, away, "game_spread", away, -110, line=3.5, **kw),
        ]
    return rows


def line(player="Alex Favorite", opponent="Ben Underdog", stat="Fantasy Score", value=20.0, odds_type="standard",
         game_id="100", start="2026-11-20T13:00:00-05:00", projection_id="1"):
    return {"player": player, "opponent": opponent, "stat": stat, "odds_type": odds_type, "line": value,
            "start_time": start, "status": "pre_game", "projection_id": projection_id, "game_id": game_id}


def price(rows, snapshot, now=NOW):
    return price_board(rows, snapshot, now=now)


class MatchingTest(unittest.TestCase):
    def test_two_events_for_one_name_pair_are_ambiguous(self):
        snapshot = match("e1") + match("e2")
        [result] = price([line()], snapshot)
        self.assertEqual(result["unpriced_reason"], "ambiguous_match")
        self.assertNotIn("p_over", result)

    def test_a_later_meeting_of_the_same_pair_is_outside_the_window(self):
        snapshot = match("e1") + match("e2", start="2026-11-27T18:00Z")
        [result] = price([line()], snapshot)
        self.assertEqual(result["event_id"], "e1")

    def test_exact_names_beat_a_looser_match(self):
        # "Anderson" is a suffix of "Kevinanderson" but so would be another Anderson's event.
        snapshot = match("e1", home="Kevin Anderson", away="Ben Underdog") + match("e2", home="Anderson", away="Ben Underdog")
        [result] = price([line(player="Kevin Anderson")], snapshot)
        self.assertEqual(result["event_id"], "e1")

    def test_shared_endings_do_not_cross_inside_an_event(self):
        event = Event("e1", "atp", ("Kevin Anderson", "Tim Henderson"))
        self.assertEqual(event.side_of("kevinanderson"), 0)
        self.assertEqual(event.side_of("timhenderson"), 1)
        # "derson" fits both participants, so it identifies neither.
        self.assertIsNone(event.side_of("derson"))

    def test_games_won_lines_stay_with_their_own_event(self):
        snapshot = (
            match("e1", home="Kevin Anderson", away="Ben Underdog")
            + match("e2", home="Tim Henderson", away="Carl Other")
            + [odds("e2", "Tim Henderson", "Carl Other", "player_games_won", side_name, -110, line=13.5, side=side_name.lower(), player="Tim Henderson")
               for side_name in ("Over", "Under")]
        )
        [result] = price([line(player="Kevin Anderson", stat="Total Games Won", value=12.5)], snapshot)
        self.assertNotIn("dk_gw_line", result)

    def test_no_book_event(self):
        [result] = price([line(player="Nobody Known", opponent="Someone Else")], match())
        self.assertEqual(result["unpriced_reason"], "no_book_line")
        events = build_events(match())
        self.assertEqual(match_event(events, "Nobody Known", "Someone Else", None, DEFAULT_CONFIG), (None, "no_book_line"))


class FormatAndFitTest(unittest.TestCase):
    def test_best_of_five_is_unsupported(self):
        slam = match(competition="atp_-_australian_open_2027")
        long_total = match(total=38.5)
        for snapshot in (slam, long_total):
            [result] = price([line()], snapshot)
            self.assertEqual(result["unpriced_reason"], "unsupported_format")

    def test_a_wta_slam_match_is_still_priced(self):
        [result] = price([line(stat="Total Games", value=21.5)], match(league="wta", competition="wta_-_australian_open_2027"))
        self.assertIsNone(result["unpriced_reason"])

    def test_no_total_prices_games_on_the_moneyline_alone_but_not_fantasy_score(self):
        fs, games = price([line(), line(stat="Total Games", value=21.5, projection_id="2")], match(total=None))
        self.assertEqual(fs["unpriced_reason"], "no_book_line")
        self.assertIn("ml_only", games["flags"])
        self.assertIsNotNone(games["p_over"])
        self.assertIsNone(games["lean"])

    def test_a_fit_off_its_targets_is_rejected(self):
        good = model.fit_dist(0.65, 0.5, 22.5, DEFAULT_CONFIG.mix_c["atp"])
        total = (22.5, -110, -110)
        self.assertTrue(_accept(good, 0.65, total, DEFAULT_CONFIG))
        self.assertFalse(_accept(good, 0.75, total, DEFAULT_CONFIG))
        self.assertFalse(_accept(good, 0.65, (22.5, -300, 240), DEFAULT_CONFIG))
        out_of_range = model.Fit(good.dist, good.label, (1.24, 0.4))
        self.assertFalse(_accept(out_of_range, 0.65, total, DEFAULT_CONFIG))

    def test_the_stray_set_total_is_ignored_and_the_most_even_rung_stands_in(self):
        snapshot = match(total=None) + [
            odds("e1", "Alex Favorite", "Ben Underdog", "total_games", name, price_, line=line_, side=name.lower(), main=main)
            for line_, main, prices in ((11.5, True, (-110, -110)), (21.5, False, (-180, 150)), (22.5, False, (-105, -115)))
            for name, price_ in zip(("Over", "Under"), prices)
        ]
        self.assertEqual(build_events(snapshot)["e1"].main_total, 22.5)


class RowTest(unittest.TestCase):
    def test_total_games_under_both_players_prices_once(self):
        rows = [
            line(stat="Total Games", value=21.5, projection_id="1"),
            line(player="Ben Underdog", opponent="Alex Favorite", stat="Total Games", value=21.5, projection_id="2"),
        ]
        self.assertEqual(len(price(rows, match())), 1)

    def test_an_under_lean_on_a_goblin_is_over_only(self):
        [goblin] = price([line(value=40.0, odds_type="goblin")], match())
        self.assertEqual(goblin["lean"], "under")
        self.assertIn("over_only", goblin["flags"])
        [standard] = price([line(value=40.0)], match())
        self.assertNotIn("over_only", standard["flags"])

    def test_started_rows_are_flagged(self):
        [result] = price([line()], match(), now=NOW + timedelta(hours=8))
        self.assertIn("started", result["flags"])

    def test_rule_of_thumb_uses_seven_minus_spread_for_an_underdog(self):
        favorite, underdog = price([line(), line(player="Ben Underdog", opponent="Alex Favorite", projection_id="2")], match())
        # No ace line: both use the tour default, 0.45 aces per service game.
        self.assertEqual(favorite["spread"], -3.5)
        self.assertGreater(favorite["rule_fs"], 13 + 3.5)
        self.assertLess(underdog["rule_fs"], 7 - 3.5 + 6)
        self.assertGreater(underdog["rule_fs"], 7 - 3.5)

    def test_ace_source_order(self):
        fd = [odds("e1", "Alex Favorite", "Ben Underdog", "player_aces", "Over", price_, book="fanduel", line=line_, side="over", main=main, player="Alex Favorite")
              # FanDuel flags the tail rung as main; the near-even rung is the one to fit.
              for line_, price_, main in ((14.5, 850, True), (8.5, -135, False))]
        [with_fd] = price([line()], match() + fd)
        self.assertEqual(with_fd["aces_src"], "FD 8.5 -135")

        ladder = [line(stat="Aces", value=4.5, odds_type="goblin", projection_id="3"),
                  line(stat="Aces", value=7.5, odds_type="demon", projection_id="4")]
        [with_ladder] = price([line()] + ladder, match())
        self.assertEqual(with_ladder["aces_src"], "PP ladder ~6.5")

        [assumed] = price([line()], match())
        self.assertEqual(assumed["aces_src"], "assumed 0.45")
        self.assertIn("assumed_aces", assumed["flags"])
        self.assertNotIn("assumed_aces", with_fd["flags"])


def typed(**overrides):
    return {"game_id": "100", "player": "Alex Favorite", "opponent": "Ben Underdog", "tour": "atp",
            "ml_player": -200, "ml_opponent": 165, "total_line": 22.5, "total_over": -110, "total_under": -110,
            "aces_player": None, "aces_opponent": None, **overrides}


class ManualLinesTest(unittest.TestCase):
    BOARD = [line(), line(stat="Total Games", value=21.5, projection_id="2"),
             line(stat="Total Games Won", value=12.5, projection_id="3"),
             line("Ben Underdog", "Alex Favorite", stat="Total Games Won", value=9.5, projection_id="4")]

    def test_typed_lines_price_a_match_the_feed_does_not_carry(self):
        unpriced = price(self.BOARD, [])
        self.assertTrue(all(r["unpriced_reason"] == "no_book_line" for r in unpriced))

        results = price_board(self.BOARD, [], now=NOW, manual=[typed()])
        book = price(self.BOARD, match())
        for ours, theirs in zip(results, book):
            self.assertIsNone(ours["unpriced_reason"])
            self.assertIn("manual_odds", ours["flags"])
            # The same numbers typed in give the same answer as the feed.
            self.assertEqual((ours["p_over"], ours["p_win"], ours["lean"]), (theirs["p_over"], theirs["p_win"], theirs["lean"]))
        self.assertEqual(results[0]["event_id"], "manual:100")
        self.assertNotIn("dk_p_over", results[1])

    def test_the_moneylines_belong_to_the_named_player(self):
        flipped = typed(player="Ben Underdog", opponent="Alex Favorite", ml_player=165, ml_opponent=-200)
        for entry in (typed(), flipped):
            fs = price_board(self.BOARD, [], now=NOW, manual=[entry])[0]
            self.assertGreater(fs["p_win"], 0.6)

    def test_typed_lines_replace_the_feed_for_that_match_only(self):
        other = line("Cara Other", "Dee Other", stat="Total Games", value=21.5, game_id="200", projection_id="9")
        snapshot = match() + match("e2", "Cara Other", "Dee Other")
        ours, theirs = price_board([self.BOARD[1], other], snapshot, now=NOW, manual=[typed(ml_player=-600, ml_opponent=420)])
        self.assertEqual((ours["fav_ml"], theirs["fav_ml"]), (-600, -200))
        self.assertNotIn("manual_odds", theirs["flags"])

    def test_without_a_total_only_the_games_markets_price(self):
        fs, games, *_ = price_board(self.BOARD, [], now=NOW, manual=[typed(total_line=None, total_over=None, total_under=None)])
        self.assertEqual(fs["unpriced_reason"], "no_book_line")
        self.assertIn("ml_only", games["flags"])

    def test_a_total_without_prices_is_taken_as_even(self):
        priced = price_board(self.BOARD, [], now=NOW, manual=[typed()])[1]
        bare = price_board(self.BOARD, [], now=NOW, manual=[typed(total_over=None, total_under=None)])[1]
        self.assertEqual(bare["p_over"], priced["p_over"])

    def test_typed_aces_set_the_mean_and_clear_the_assumed_flag(self):
        few = price_board(self.BOARD, [], now=NOW, manual=[typed(aces_player=2)])[0]
        many = price_board(self.BOARD, [], now=NOW, manual=[typed(aces_player=12)])[0]
        assumed = price_board(self.BOARD, [], now=NOW, manual=[typed()])[0]
        self.assertEqual(many["aces_src"], "manual 12")
        self.assertNotIn("assumed_aces", many["flags"])
        self.assertIn("assumed_aces", assumed["flags"])
        # Ten more aces at half a point each.
        self.assertAlmostEqual(many["model_mean"] - few["model_mean"], 5.0, delta=0.11)


if __name__ == "__main__":
    unittest.main()
