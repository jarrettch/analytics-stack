from __future__ import annotations

import unittest

from routers.wnba import (
    _ESPN_TO_WNBA_TRICODE,
    WNBA_TEAMS,
    _canonical_tricode,
    _warned_espn_abbreviations,
    adapt_espn_scoreboard,
    adapt_espn_win_probability,
)


class ESPNScoreboardAdapterTest(unittest.TestCase):
    def test_adapts_live_game_to_existing_wnba_shape(self):
        payload = {
            "events": [{
                "id": "401857149",
                "date": "2026-08-16T23:00Z",
                "competitions": [{
                    "competitors": [
                        {
                            "homeAway": "home",
                            "team": {
                                "id": "11",
                                "location": "Phoenix",
                                "name": "Mercury",
                                "abbreviation": "PHX",
                            },
                            "score": "47",
                            "records": [{"name": "overall", "summary": "13-22"}],
                        },
                        {
                            "homeAway": "away",
                            "team": {
                                "id": "132052",
                                "location": "Portland",
                                "name": "Fire",
                                "abbreviation": "POR",
                            },
                            "score": "51",
                            "records": [{"name": "overall", "summary": "14-20"}],
                        },
                    ],
                    "status": {
                        "displayClock": "7:18",
                        "period": 2,
                        "type": {
                            "state": "in",
                            "completed": False,
                            "description": "In Progress",
                        },
                    },
                    "broadcasts": [{"names": ["ESPN", "Disney+"]}],
                }],
            }],
        }

        games = adapt_espn_scoreboard(payload)["scoreboard"]["games"]

        self.assertEqual(len(games), 1)
        game = games[0]
        self.assertEqual(game["gameId"], "401857149")
        self.assertEqual(game["gameEt"], "2026-08-16T19:00:00-04:00")
        self.assertEqual(game["gameStatus"], 2)
        self.assertEqual(game["period"], 2)
        self.assertEqual(game["gameClock"], "PT07M18.00S")
        self.assertEqual(game["broadcast"], "ESPN, Disney+")
        self.assertEqual(game["homeTeam"]["teamTricode"], "PHX")
        self.assertEqual(game["homeTeam"]["wins"], 13)
        self.assertEqual(game["awayTeam"]["teamTricode"], "PDX")
        self.assertEqual(game["awayTeam"]["teamId"], "1611661327")

    def test_skips_event_without_both_teams(self):
        payload = {"events": [{"competitions": [{"competitors": []}]}]}

        self.assertEqual(adapt_espn_scoreboard(payload), {"scoreboard": {"games": []}})

    def test_joins_espn_win_probability_to_play_context(self):
        payload = {
            "plays": [
                {
                    "id": "play-1",
                    "homeScore": 17,
                    "awayScore": 28,
                    "period": {"number": 2},
                    "clock": {"displayValue": "7:18"},
                    "wallclock": "2026-08-16T21:30:00Z",
                },
            ],
            "winprobability": [
                {"playId": "play-1", "homeWinPercentage": 0.192},
                {"playId": "missing-play", "homeWinPercentage": 0.5},
            ],
        }

        self.assertEqual(
            adapt_espn_win_probability(payload),
            [{
                "playId": "play-1",
                "homeWinPercentage": 0.192,
                "period": 2,
                "clock": "7:18",
                "homeScore": 17,
                "awayScore": 28,
                "timestamp": "2026-08-16T21:30:00Z",
            }],
        )

    def test_does_not_fabricate_missing_win_probability(self):
        payload = {
            "plays": [{"id": "play-1"}],
            "winprobability": [{"playId": "play-1"}],
        }

        self.assertEqual(adapt_espn_win_probability(payload), [])


class CanonicalTricodeTest(unittest.TestCase):
    """The odds join is keyed on WNBA_TEAMS tricodes, so an ESPN abbreviation
    that doesn't translate silently drops that team's odds from /today."""

    def test_every_espn_abbreviation_resolves_to_a_known_team(self):
        # ESPN's current WNBA vocabulary. GS/LA/LV/NY/WSH were unmapped when the
        # scoreboard moved to ESPN, which dropped odds for five teams.
        espn_abbreviations = [
            "ATL", "CHI", "CON", "DAL", "GS", "IND", "LA", "LV",
            "MIN", "NY", "PHX", "POR", "SEA", "TOR", "WSH",
        ]

        unresolved = [
            abbreviation
            for abbreviation in espn_abbreviations
            if _canonical_tricode(abbreviation) not in WNBA_TEAMS
        ]

        self.assertEqual(unresolved, [])

    def test_mapping_targets_are_all_real_tricodes(self):
        """Guards a typo in the map itself, which would fail the same way."""
        for abbreviation, tricode in _ESPN_TO_WNBA_TRICODE.items():
            self.assertIn(tricode, WNBA_TEAMS, f"{abbreviation} -> {tricode}")

    def test_unknown_abbreviation_passes_through_with_a_warning(self):
        _warned_espn_abbreviations.discard("ZZZ")

        with self.assertLogs("routers.wnba", level="WARNING") as captured:
            self.assertEqual(_canonical_tricode("ZZZ"), "ZZZ")

        self.assertIn("ZZZ", captured.output[0])
        self.assertIn("not in WNBA_TEAMS", captured.output[0])


if __name__ == "__main__":
    unittest.main()
