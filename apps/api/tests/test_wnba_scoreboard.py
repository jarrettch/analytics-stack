from __future__ import annotations

import unittest

from routers.wnba import adapt_espn_scoreboard


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


if __name__ == "__main__":
    unittest.main()
