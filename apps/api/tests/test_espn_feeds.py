"""Coverage for the ESPN standings/efficiency adapters.

These replaced two nba_api calls that stats.nba.com tarpits from datacenter IPs.
The failure mode being guarded here is the quiet one: an adapter that returns a
map nobody can look up, or ratings computed on the wrong scale.
"""
from __future__ import annotations

import unittest
from datetime import date

from routers import espn_feeds
from routers.utils import ET
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
        espn_feeds._get = lambda *_a, **_k: self.payload  # type: ignore[assignment]
        self.payload = _efficiency_payload()

    def tearDown(self):
        import importlib

        importlib.reload(espn_feeds)

    def test_derives_ratings_and_pace_from_season_totals(self):
        # possessions = 3000 - 300 + 500 + 0.44*1000 = 3640
        # offRating   = 100 * 3800 / 3640 = 104.4
        # defRating   = 100 * (3800-100) / 3640 = 101.6
        # pace        = 3640 / 40 = 91.0
        result = espn_feeds.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )

        self.assertEqual(
            result["GSW"],
            {"offRating": 104.4, "defRating": 101.6, "netRating": 2.7, "pace": 91.0},
        )

    def test_pace_is_per_game_not_per_48_minutes(self):
        """The WNBA path used to rescale pace by 40/48; this value must not be."""
        result = espn_feeds.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertAlmostEqual(result["GSW"]["pace"], 3640 / 40, places=1)

    def test_keys_by_tricode_and_display_name(self):
        result = espn_feeds.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertIn("GSW", result)
        self.assertIn("Golden State Warriors", result)
        self.assertNotIn("GS", result)  # the raw ESPN abbreviation is not a key

    def test_team_id_alias_is_added_when_resolvable(self):
        result = espn_feeds.fetch_efficiency(
            sport="nba",
            canonical_tricode=nba_tricode,
            team_id_for=lambda tri: "1610612744" if tri == "GSW" else None,
        )
        self.assertEqual(result["1610612744"], result["GSW"])

    def test_skips_team_with_incomplete_stats_rather_than_inventing(self):
        self.payload = _efficiency_payload(offensive=[3800, None, 300, 500, 1000])

        result = espn_feeds.fetch_efficiency(
            sport="nba", canonical_tricode=nba_tricode
        )

        self.assertEqual(result, {})

    def test_skips_team_with_zero_games_played(self):
        self.payload = _efficiency_payload(games_played=0)

        result = espn_feeds.fetch_efficiency(
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
        espn_feeds._get = lambda *_a, **_k: self.payload  # type: ignore[assignment]

    def tearDown(self):
        import importlib

        importlib.reload(espn_feeds)

    def test_maps_conference_names_to_short_form(self):
        result = espn_feeds.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertEqual(result["NYK"]["conference"], "East")
        self.assertEqual(result["GSW"]["conference"], "West")

    def test_carries_record_streak_and_last_ten(self):
        result = espn_feeds.fetch_standings(
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
        result = espn_feeds.fetch_standings(
            sport="nba", canonical_tricode=nba_tricode
        )
        self.assertEqual(result["NYK"]["seed"], 3)
        self.assertEqual(result["GSW"]["seed"], 10)

    def test_league_wide_seed_reranks_across_conferences(self):
        """The WNBA seeds its playoffs from the whole league, not per conference."""
        result = espn_feeds.fetch_standings(
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


def _event(event_id: str, iso_date: str, away: str, home: str, status: str = "STATUS_SCHEDULED"):
    def competitor(abbr: str, side: str):
        return {
            "homeAway": side,
            "team": {
                "id": f"id-{abbr}",
                "abbreviation": abbr,
                "location": abbr.title(),
                "name": "Team",
            },
            "records": [{"name": "overall", "summary": "10-5"}],
            "score": None,
        }

    return {
        "id": event_id,
        "date": iso_date,
        "competitions": [
            {
                "status": {"period": 0, "type": {"name": status, "state": "pre"}},
                "competitors": [competitor(home, "home"), competitor(away, "away")],
                "broadcasts": [{"names": ["ESPN"]}],
            }
        ],
    }


class NextSlateTest(unittest.TestCase):
    def tearDown(self):
        import importlib

        importlib.reload(espn_feeds)

    def _slate(self, events, start=date(2026, 4, 1), lookahead_days=14):
        espn_feeds._get = lambda *_a, **_k: {"events": events}  # type: ignore[assignment]
        return espn_feeds.fetch_next_slate(
            sport="nba",
            start_date=start,
            lookahead_days=lookahead_days,
            canonical_tricode=nba_tricode,
            timezone=ET,
        )

    def test_returns_earliest_date_with_games_not_first_event(self):
        games, slate_date = self._slate(
            [
                _event("late", "2026-04-05T23:00Z", "BOS", "NY"),
                _event("early", "2026-04-03T23:00Z", "GS", "LAL"),
            ]
        )

        self.assertEqual(slate_date, date(2026, 4, 3))
        self.assertEqual([g["gameId"] for g in games], ["early"])

    def test_buckets_by_eastern_day_not_utc(self):
        """A 00:30Z tip-off is the previous evening in ET and belongs on that slate."""
        games, slate_date = self._slate([_event("late-tip", "2026-04-02T00:30Z", "BOS", "NY")])

        self.assertEqual(slate_date, date(2026, 4, 1))
        self.assertTrue(games[0]["gameEt"].startswith("2026-04-01T20:30"))

    def test_skips_postponed_games(self):
        games, slate_date = self._slate(
            [
                _event("off", "2026-04-03T23:00Z", "BOS", "NY", status="STATUS_POSTPONED"),
                _event("on", "2026-04-04T23:00Z", "GS", "LAL"),
            ]
        )

        self.assertEqual(slate_date, date(2026, 4, 4))
        self.assertEqual([g["gameId"] for g in games], ["on"])

    def test_empty_window_returns_no_date(self):
        self.assertEqual(self._slate([]), ([], None))

    def test_ignores_games_beyond_the_lookahead_window(self):
        games, slate_date = self._slate(
            [_event("far", "2026-05-30T23:00Z", "BOS", "NY")], lookahead_days=14
        )

        self.assertEqual((games, slate_date), ([], None))

    def test_emits_the_fields_the_slate_builder_reads(self):
        games, _ = self._slate([_event("g1", "2026-04-01T23:00Z", "GS", "WSH")])
        game = games[0]

        for key in (
            "gameId", "gameEt", "gameStatus", "gameStatusText",
            "period", "gameClock", "awayTeam", "homeTeam",
            "seriesText", "seriesGameNumber", "broadcasters",
        ):
            self.assertIn(key, game)
        for key in ("teamId", "teamCity", "teamName", "teamTricode", "wins", "losses", "score"):
            self.assertIn(key, game["awayTeam"])

    def test_canonicalizes_espn_abbreviations(self):
        games, _ = self._slate([_event("g1", "2026-04-01T23:00Z", "GS", "WSH")])

        self.assertEqual(games[0]["awayTeam"]["teamTricode"], "GSW")
        self.assertEqual(games[0]["homeTeam"]["teamTricode"], "WAS")

    def test_parses_records_into_wins_and_losses(self):
        games, _ = self._slate([_event("g1", "2026-04-01T23:00Z", "GS", "WSH")])

        self.assertEqual((games[0]["awayTeam"]["wins"], games[0]["awayTeam"]["losses"]), (10, 5))

    def test_ignores_games_before_the_start_date(self):
        """A late tip the evening before converts to an earlier ET day, which is
        inside ESPN's UTC range but must not be selected as the slate."""
        games, slate_date = self._slate(
            [
                _event("yesterday", "2026-04-01T00:30Z", "BOS", "NY"),  # 03-31 ET
                _event("today", "2026-04-01T23:00Z", "GS", "LAL"),
            ],
            start=date(2026, 4, 1),
        )

        self.assertEqual(slate_date, date(2026, 4, 1))
        self.assertEqual([g["gameId"] for g in games], ["today"])

    def test_converts_using_real_dst_offsets(self):
        """America/New_York, not a fixed offset: 23:00Z is 19:00 in April (EDT)
        and 18:00 in January (EST)."""
        _, april = self._slate(
            [_event("g", "2026-04-01T23:00Z", "BOS", "NY")], start=date(2026, 4, 1)
        )
        april_games, _ = self._slate(
            [_event("g", "2026-04-01T23:00Z", "BOS", "NY")], start=date(2026, 4, 1)
        )
        january_games, _ = self._slate(
            [_event("g", "2026-01-15T23:00Z", "BOS", "NY")], start=date(2026, 1, 15)
        )

        self.assertTrue(april_games[0]["gameEt"].startswith("2026-04-01T19:00"))
        self.assertTrue(january_games[0]["gameEt"].startswith("2026-01-15T18:00"))

    def test_midnight_eastern_stays_on_the_night_it_started(self):
        """A slate day runs 6am-6am ET, so a 9pm PT tip is still that night's game.

        This previously asserted the opposite — that midnight ET opened the next
        slate. Bucketing on the calendar date put such a game on Jan 16 while the
        board still showed Jan 15 until 6am, making it invisible at its own
        tip-off. Assignment and display now share slate_date_et.
        """
        games, slate_date = self._slate(
            [_event("g", "2026-01-16T05:00Z", "BOS", "NY")],  # 00:00 ET Jan 16
            start=date(2026, 1, 15),
        )

        self.assertEqual(slate_date, date(2026, 1, 15))
        self.assertTrue(games[0]["gameEt"].startswith("2026-01-16T00:00"))

    def test_six_am_eastern_opens_the_next_slate(self):
        """The other side of the boundary: 6am ET is a new slate day."""
        games, slate_date = self._slate(
            [_event("g", "2026-01-16T11:00Z", "BOS", "NY")],  # 06:00 ET Jan 16
            start=date(2026, 1, 15),
        )

        self.assertEqual(slate_date, date(2026, 1, 16))

    def test_late_west_coast_finish_stays_on_its_own_slate(self):
        """2am ET (11pm PT) — the CBB/CFB late-window case."""
        _games, slate_date = self._slate(
            [_event("g", "2026-01-16T07:00Z", "BOS", "NY")],  # 02:00 ET Jan 16
            start=date(2026, 1, 15),
        )

        self.assertEqual(slate_date, date(2026, 1, 15))
