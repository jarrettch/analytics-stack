"""Coverage for the ESPN summary adapters behind the NBA game detail page.

These replaced nba_api's live BoxScore and PlayByPlay, which read cdn.nba.com
and now get 403 on every request. The adapters feed the existing nba_api-shaped
helpers, so the risk is a field that silently maps to zero.
"""
from __future__ import annotations

import unittest

from routers.nba import (
    _espn_actions,
    _espn_box_team,
    _espn_home_prob_by_play,
    _espn_linescore,
    _nba_pbp,
    _nba_team_box,
    _parse_clock_seconds,
)

BOX_KEYS = [
    "minutes", "points", "fieldGoalsMade-fieldGoalsAttempted",
    "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
    "freeThrowsMade-freeThrowsAttempted", "rebounds", "assists", "turnovers",
    "steals", "blocks", "offensiveRebounds", "defensiveRebounds", "fouls", "plusMinus",
]


def _athlete(name, stats, *, did_not_play=False):
    return {
        "athlete": {"displayName": name, "position": {"abbreviation": "F"}},
        "didNotPlay": did_not_play,
        "stats": stats,
    }


def _player_box(*athletes):
    return {"team": {"id": "2"}, "statistics": [{"keys": BOX_KEYS, "athletes": list(athletes)}]}


class BoxScoreTest(unittest.TestCase):
    def test_splits_made_attempted_and_reads_signed_plus_minus(self):
        team = _espn_box_team(_player_box(
            _athlete("Paul George", ["31", "38", "12-21", "7-12", "7-8", "5", "3", "3", "5", "0", "0", "5", "5", "+9"]),
            _athlete("Jayson Tatum", ["38", "13", "6-23", "0-7", "1-1", "4", "11", "2", "0", "0", "1", "3", "3", "-5"]),
        ))
        box = _nba_team_box(team)
        george, tatum = box["players"]

        self.assertEqual(
            (george["minutes"], george["points"], george["fgm"], george["fga"], george["threePm"],
             george["threePa"], george["ftm"], george["fta"], george["rebounds"], george["fouls"]),
            ("31", 38, 12, 21, 7, 12, 7, 8, 5, 5),
        )
        self.assertEqual((george["plusMinus"], tatum["plusMinus"]), (9, -5))
        self.assertEqual(george["position"], "F")
        self.assertEqual(box["totals"]["points"], 51)
        self.assertEqual(box["totals"]["fgPct"], round(18 / 44 * 100, 1))

    def test_drops_players_who_did_not_play(self):
        team = _espn_box_team(_player_box(
            _athlete("Starter", ["30", "10", "4-8", "1-3", "1-2", "6", "2", "1", "1", "0", "1", "5", "2", "+3"]),
            _athlete("Inactive", [], did_not_play=True),
        ))

        self.assertEqual([p["name"] for p in team["players"]], ["Starter"])


class LinescoreTest(unittest.TestCase):
    def test_overtime_periods_go_to_ot(self):
        score = _espn_linescore({
            "score": "125",
            "linescores": [{"displayValue": v} for v in ("34", "24", "26", "31", "10")],
        })

        self.assertEqual(score, {"q1": 34, "q2": 24, "q3": 26, "q4": 31, "ot": [10], "total": 125})

    def test_pregame_has_no_linescores(self):
        self.assertEqual(
            _espn_linescore({"linescores": []}),
            {"q1": 0, "q2": 0, "q3": 0, "q4": 0, "total": 0},
        )


class ClockTest(unittest.TestCase):
    def test_reads_espn_and_nba_clock_formats(self):
        self.assertEqual(_parse_clock_seconds("11:38"), 698)
        self.assertEqual(_parse_clock_seconds("PT05M30.00S"), 330)

    def test_reads_sub_minute_espn_clock(self):
        """Under a minute ESPN shows "45.3"; reading it as 0 would call close games final."""
        self.assertAlmostEqual(_parse_clock_seconds("45.3"), 45.3)
        self.assertEqual(_parse_clock_seconds("0.0"), 0)


class PlayByPlayTest(unittest.TestCase):
    def test_maps_team_ids_to_matchup_tricodes(self):
        plays = [
            {
                "id": "4019090887",
                "type": {"text": "Offensive Foul"},
                "text": "Mitchell Robinson offensive foul",
                "awayScore": 0,
                "homeScore": 0,
                "period": {"number": 1},
                "clock": {"displayValue": "11:48"},
                "team": {"id": "18"},
                "wallclock": "2026-10-20T19:07:50Z",
            },
            {
                "id": "4019090890",
                "type": {"text": "Jump Shot"},
                "text": "Jalen Duren makes 2-foot dunk",
                "awayScore": 0,
                "homeScore": 2,
                "period": {"number": 1},
                "clock": {"displayValue": "11:20"},
                "team": {"id": "8"},
            },
        ]
        matchup = {"away": {"abbreviation": "NYK", "pace": 100}, "home": {"abbreviation": "DET", "pace": 100}}

        events, win_probability = _nba_pbp(_espn_actions(plays, {"18": "NYK", "8": "DET"}), matchup)

        self.assertEqual([e["team"] for e in events], ["NYK", "DET"])
        self.assertEqual(events[1]["event"], "Jalen Duren makes 2-foot dunk")
        self.assertEqual((events[1]["quarter"], events[1]["clock"], events[1]["homeScore"]), (1, "11:20", 2))
        self.assertEqual(events[0]["id"], "4019090887")
        self.assertGreater(events[1]["winProbHome"], 50)
        self.assertEqual(len(win_probability), 2)


def _play(play_id, clock, home_score=0):
    return {
        "id": play_id,
        "awayScore": 0,
        "homeScore": home_score,
        "period": {"number": 1},
        "clock": {"displayValue": clock},
    }


MATCHUP = {"away": {"abbreviation": "OKC", "pace": 100}, "home": {"abbreviation": "SAS", "pace": 100}}


class WinProbabilityTest(unittest.TestCase):
    def test_uses_espn_probability_for_each_play(self):
        actions = _espn_actions([_play("a", "11:42", 3), _play("b", "11:20", 3)], {})
        probs = _espn_home_prob_by_play([
            {"playId": "a", "homeWinPercentage": 0.525},
            {"playId": "b", "homeWinPercentage": 0.454},
        ])

        events, series = _nba_pbp(actions, MATCHUP, probs)

        self.assertEqual([(e["winProbHome"], e["winProbAway"]) for e in events], [(52.5, 47.5), (45.4, 54.6)])
        self.assertEqual([p["homeProb"] for p in series], [52.5, 45.4])

    def test_carries_last_espn_value_over_a_play_without_a_point(self):
        """Live summaries can trail the play feed; holding the last published value
        avoids a jump to the local model's number for one play."""
        actions = _espn_actions([_play("a", "11:42"), _play("b", "11:20", 2)], {})

        events, _ = _nba_pbp(actions, MATCHUP, {"a": 0.61})

        self.assertEqual([e["winProbHome"] for e in events], [61.0, 61.0])

    def test_falls_back_to_the_model_without_espn_probabilities(self):
        actions = _espn_actions([_play("a", "11:42", 3)], {})

        with_model, _ = _nba_pbp(actions, MATCHUP)
        with_empty, _ = _nba_pbp(actions, MATCHUP, {})

        self.assertEqual(with_model, with_empty)
        self.assertGreater(with_model[0]["winProbHome"], 50)

    def test_drops_malformed_points(self):
        probs = _espn_home_prob_by_play([
            {"playId": "ok", "homeWinPercentage": 0.5},
            {"playId": "out-of-range", "homeWinPercentage": 1.7},
            {"playId": "missing"},
            {"homeWinPercentage": 0.4},
        ])

        self.assertEqual(probs, {"ok": 0.5})


if __name__ == "__main__":
    unittest.main()
