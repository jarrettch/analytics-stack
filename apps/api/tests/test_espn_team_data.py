"""Coverage for the ESPN standings/efficiency adapters.

These replaced two nba_api calls that stats.nba.com tarpits from datacenter IPs.
The failure mode being guarded here is the quiet one: an adapter that returns a
map nobody can look up, or ratings computed on the wrong scale.
"""
from __future__ import annotations

import unittest

from routers import espn_team_data
from routers.nba import _canonical_tricode as nba_tricode
from routers.nba import NBA_TRICODES, _ESPN_TO_NBA_TRICODE


def _efficiency_payload(**overrides):
    """One team with round numbers, so the expected ratings are checkable by hand."""
    offensive = overrides.pop("offensive", [3800, 3000, 300, 500, 1000])
    return {
        "requestedSeason": {"year": 2026},
        "categories": [
            {"name": "general", "names": ["gamesPlayed"]},
            {
                "name": "offensive",
                "names": [
                    "points",
                    "fieldGoalsAttempted",
                    "offensiveRebounds",
                    "turnovers",
                    "freeThrowsAttempted",
                ],
            },
            {"name": "differential", "names": ["pointsDifferential"]},
        ],
        "teams": [
            {
                "team": {"abbreviation": "GS", "displayName": "Golden State Warriors"},
                "categories": [
                    {"values": [overrides.pop("games_played", 40)]},
                    {"values": offensive},
                    {"values": [overrides.pop("differential", 100)]},
                ],
            }
        ],
    }


class EfficiencyMathTest(unittest.TestCase):
    def setUp(self):
        espn_team_data._get = lambda *_a, **_k: self.payload  # type: ignore[assignment]
        self.payload = _efficiency_payload()

    def tearDown(self):
        import importlib

        importlib.reload(espn_team_data)

    def test_derives_ratings_and_pace_from_season_totals(self):
        # possessions = 3000 - 300 + 500 + 0.44*1000 = 3640
        # offRating   = 100 * 3800 / 3640 = 104.4
        # defRating   = 100 * (3800-100) / 3640 = 101.6
        # pace        = 3640 / 40 = 91.0
        result = espn_team_data.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )

        self.assertEqual(
            result["GSW"],
            {"offRating": 104.4, "defRating": 101.6, "netRating": 2.7, "pace": 91.0},
        )

    def test_pace_is_per_game_not_per_48_minutes(self):
        """The WNBA path used to rescale pace by 40/48; this value must not be."""
        result = espn_team_data.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertAlmostEqual(result["GSW"]["pace"], 3640 / 40, places=1)

    def test_keys_by_tricode_and_display_name(self):
        result = espn_team_data.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertIn("GSW", result)
        self.assertIn("Golden State Warriors", result)
        self.assertNotIn("GS", result)  # the raw ESPN abbreviation is not a key

    def test_team_id_alias_is_added_when_resolvable(self):
        result = espn_team_data.fetch_efficiency(
            sport="nba",
            canonical_tricode=nba_tricode,
            team_id_for=lambda tri: "1610612744" if tri == "GSW" else None,
        )
        self.assertEqual(result["1610612744"], result["GSW"])

    def test_skips_team_with_incomplete_stats_rather_than_inventing(self):
        self.payload = _efficiency_payload(offensive=[3800, None, 300, 500, 1000])

        result = espn_team_data.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )

        self.assertEqual(result, {})

    def test_skips_team_with_zero_games_played(self):
        self.payload = _efficiency_payload(games_played=0)

        result = espn_team_data.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )

        self.assertEqual(result, {})


class StandingsAdapterTest(unittest.TestCase):
    payload = {
        "season": {"year": 2026},
        "children": [
            {
                "name": "Eastern Conference",
                "standings": {
                    "entries": [
                        {
                            "team": {"abbreviation": "NY", "displayName": "New York Knicks"},
                            "stats": [
                                {"name": "wins", "value": 53},
                                {"name": "losses", "value": 29},
                                {"name": "playoffSeed", "value": 3},
                                {"name": "streak", "displayValue": "W2"},
                                {"name": "Last Ten Games", "displayValue": "6-4"},
                            ],
                        }
                    ]
                },
            },
            {
                "name": "Western Conference",
                "standings": {
                    "entries": [
                        {
                            "team": {"abbreviation": "GS", "displayName": "Golden State Warriors"},
                            "stats": [
                                {"name": "wins", "value": 37},
                                {"name": "losses", "value": 45},
                                {"name": "playoffSeed", "value": 10},
                                {"name": "streak", "displayValue": "L3"},
                                {"name": "Last Ten Games", "displayValue": "3-7"},
                            ],
                        }
                    ]
                },
            },
        ],
    }

    def setUp(self):
        espn_team_data._get = lambda *_a, **_k: self.payload  # type: ignore[assignment]

    def tearDown(self):
        import importlib

        importlib.reload(espn_team_data)

    def test_maps_conference_names_to_short_form(self):
        result = espn_team_data.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertEqual(result["NYK"]["conference"], "East")
        self.assertEqual(result["GSW"]["conference"], "West")

    def test_carries_record_streak_and_last_ten(self):
        result = espn_team_data.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertEqual(
            result["NYK"],
            {
                "record": "53-29",
                "winPct": 53 / 82,
                "seed": 3,
                "conference": "East",
                "streak": "W2",
                "last10": "6-4",
            },
        )

    def test_conference_seed_is_used_by_default(self):
        result = espn_team_data.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertEqual(result["NYK"]["seed"], 3)
        self.assertEqual(result["GSW"]["seed"], 10)

    def test_league_wide_seed_reranks_across_conferences(self):
        """The WNBA seeds its playoffs from the whole league, not per conference."""
        result = espn_team_data.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode, league_wide_seed=True
        )
        self.assertEqual(result["NYK"]["seed"], 1)  # .646 win pct
        self.assertEqual(result["GSW"]["seed"], 2)  # .451 win pct


class NBACanonicalTricodeTest(unittest.TestCase):
    def test_every_espn_abbreviation_resolves_to_a_tricode(self):
        # ESPN's current NBA vocabulary; the six non-identity codes are the ones
        # that would otherwise fail the join.
        espn_abbreviations = [
            "ATL", "BKN", "BOS", "CHA", "CHI", "CLE", "DAL", "DEN", "DET", "GS",
            "HOU", "IND", "LAC", "LAL", "MEM", "MIA", "MIL", "MIN", "NO", "NY",
            "OKC", "ORL", "PHI", "PHX", "POR", "SA", "SAC", "TOR", "UTAH", "WSH",
        ]

        unresolved = [a for a in espn_abbreviations if nba_tricode(a) not in NBA_TRICODES]

        self.assertEqual(unresolved, [])

    def test_mapping_targets_are_all_real_tricodes(self):
        for abbreviation, tricode in _ESPN_TO_NBA_TRICODE.items():
            self.assertIn(tricode, NBA_TRICODES, f"{abbreviation} -> {tricode}")

    def test_unknown_abbreviation_passes_through_with_a_warning(self):
        from routers import nba

        nba._warned_espn_abbreviations.discard("ZZZ")

        with self.assertLogs("routers.nba", level="WARNING") as captured:
            self.assertEqual(nba_tricode("ZZZ"), "ZZZ")

        self.assertIn("ZZZ", captured.output[0])


if __name__ == "__main__":
    unittest.main()
