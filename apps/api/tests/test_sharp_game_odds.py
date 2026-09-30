from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

import httpx

from routers import sharp_game_odds
from routers.nhl import get_tricode_from_team_name as nhl_tricode
from routers.utils import ET, fetch_sgo_odds


def row(
    market: str,
    side: str,
    value: int | float,
    *,
    league: str = "nba",
    book: str = "draftkings",
    home: str = "Boston Celtics",
    away: str = "New York Knicks",
    start: str = "2099-01-15T00:30:00Z",
) -> dict:
    return {
        "league": league,
        "market_type": market,
        "sportsbook": book,
        "event_id": f"nba-{book}",
        "home_team": home,
        "away_team": away,
        "selection_type": side,
        "odds_american": value if market == "moneyline" else -110,
        "line": value if market != "moneyline" else None,
        "event_start_time": start,
        "is_live": False,
        "is_active": True,
        "is_alternate_line": False,
        "is_main_line": True,
    }


class SharpGameOddsTest(unittest.IsolatedAsyncioTestCase):
    def test_nhl_sharp_team_aliases(self):
        self.assertEqual(nhl_tricode("MTL Canadiens"), "MTL")
        self.assertEqual(nhl_tricode("Montreal Canadiens"), "MTL")

    async def test_normalizes_one_book_without_mixing_lines(self):
        rows = {
            "moneyline": [
                row("moneyline", "away", 120),
                row("moneyline", "home", -140),
                row("moneyline", "away", 105, book="fanduel"),
                row("moneyline", "home", -125, book="fanduel"),
            ],
            "point_spread": [
                row("point_spread", "away", 2.5),
                row("point_spread", "home", -2.5),
                row("point_spread", "away", 1.5, book="fanduel"),
                row("point_spread", "home", -1.5, book="fanduel"),
            ],
            "total_points": [
                row("total_points", "over", 218.5),
                row("total_points", "under", 218.5),
                row("total_points", "over", 219.5, book="fanduel"),
                row("total_points", "under", 219.5, book="fanduel"),
            ],
        }

        async def fetch(league, market, *, sweep):
            sweep.requests_made += 1
            return rows[market]

        snapshot_call = {}

        def save_snapshots(snapshots, *, stats):
            snapshot_call["rows"] = snapshots
            stats.update(inserted=1, deduplicated=0, persistence_ok=True)
            return 1

        with (
            patch.object(sharp_game_odds, "fetch_sharp_odds", side_effect=fetch),
            patch.object(sharp_game_odds, "save_persisted_odds") as save_latest,
            patch.object(
                sharp_game_odds, "save_odds_snapshots", side_effect=save_snapshots
            ),
        ):
            stats = {}
            odds, fresh = await sharp_game_odds.fetch_sharp_game_odds(
                sport="nba",
                league="nba",
                table_name="nba_odds",
                tricode_for={
                    "Boston Celtics": "BOS",
                    "New York Knicks": "NYK",
                }.get,
                target_date=date(2099, 1, 14),
                persisted_odds={},
                collection_stats=stats,
            )

        self.assertEqual(fresh, 1)
        game = odds[("2099-01-14", "NYK@BOS")]
        self.assertEqual(game["awayMoneyline"], 120)
        self.assertEqual(game["homeMoneyline"], -140)
        self.assertEqual(game["awaySpread"], 2.5)
        self.assertEqual(game["overUnder"], 218.5)
        self.assertAlmostEqual(
            game["awayWinProbability"] + game["homeWinProbability"], 1
        )
        self.assertEqual(snapshot_call["rows"][0]["provider"], "sharpapi")
        self.assertEqual(snapshot_call["rows"][0]["bookmaker"], "draftkings")
        self.assertEqual(stats["provider"], "sharpapi")
        save_latest.assert_called_once()

    async def test_nhl_uses_hockey_market_names(self):
        nhl_row = {"league": "nhl", "home": "Boston Bruins", "away": "New York Rangers"}
        rows = {
            "moneyline": [
                row("moneyline", "away", 145, **nhl_row),
                row("moneyline", "home", -170, **nhl_row),
            ],
            "puck_line": [
                row("puck_line", "away", 1.5, **nhl_row),
                row("puck_line", "home", -1.5, **nhl_row),
            ],
            "total_goals": [
                row("total_goals", "over", 6.5, **nhl_row),
                row("total_goals", "under", 6.5, **nhl_row),
            ],
        }
        requested_markets = []

        async def fetch(league, market, *, sweep):
            requested_markets.append(market)
            return rows[market]

        with (
            patch.object(sharp_game_odds, "fetch_sharp_odds", side_effect=fetch),
            patch.object(sharp_game_odds, "save_persisted_odds"),
            patch.object(sharp_game_odds, "save_odds_snapshots"),
        ):
            odds, fresh = await sharp_game_odds.fetch_sharp_game_odds(
                sport="nhl",
                league="nhl",
                table_name="nhl_odds",
                tricode_for={
                    "Boston Bruins": "BOS",
                    "New York Rangers": "NYR",
                }.get,
                target_date=date(2099, 1, 14),
                persisted_odds={},
            )

        self.assertEqual(
            requested_markets,
            ["moneyline", "puck_line", "total_goals"],
        )
        self.assertEqual(fresh, 1)
        game = odds[("2099-01-14", "NYR@BOS")]
        self.assertEqual(game["homeSpread"], -1.5)
        self.assertEqual(game["overUnder"], 6.5)
        self.assertEqual(game["overOdds"], -110)
        self.assertEqual(game["underOdds"], -110)

    async def test_mlb_uses_baseball_market_names(self):
        mlb_row = {
            "league": "mlb",
            "home": "Atlanta Braves",
            "away": "Philadelphia Phillies",
        }
        rows = {
            "moneyline": [
                row("moneyline", "away", -117, **mlb_row),
                row("moneyline", "home", -102, **mlb_row),
            ],
            "run_line": [
                row("run_line", "away", -1.5, **mlb_row),
                row("run_line", "home", 1.5, **mlb_row),
            ],
            "total_runs": [
                row("total_runs", "over", 7.5, **mlb_row),
                row("total_runs", "under", 7.5, **mlb_row),
            ],
        }
        requested_markets = []

        async def fetch(league, market, *, sweep):
            requested_markets.append(market)
            return rows[market]

        with (
            patch.object(sharp_game_odds, "fetch_sharp_odds", side_effect=fetch),
            patch.object(sharp_game_odds, "save_persisted_odds"),
            patch.object(sharp_game_odds, "save_odds_snapshots"),
        ):
            odds, fresh = await sharp_game_odds.fetch_sharp_game_odds(
                sport="mlb",
                league="mlb",
                table_name="mlb_odds",
                tricode_for={
                    "Atlanta Braves": "ATL",
                    "Philadelphia Phillies": "PHI",
                }.get,
                target_date=date(2099, 1, 14),
                persisted_odds={},
            )

        self.assertEqual(
            requested_markets,
            ["moneyline", "run_line", "total_runs"],
        )
        self.assertEqual(fresh, 1)
        game = odds[("2099-01-14", "PHI@ATL")]
        self.assertEqual(game["awaySpread"], -1.5)
        self.assertEqual(game["overUnder"], 7.5)
        self.assertEqual(game["overOdds"], -110)
        self.assertEqual(game["underOdds"], -110)

    async def test_sgo_http_failure_uses_sharp_fallback(self):
        response = httpx.Response(
            429,
            json={"error": "monthly quota exhausted"},
            request=httpx.Request(
                "GET", "https://api.sportsgameodds.com/v2/events"
            ),
        )
        client = AsyncMock()
        client.get.return_value = response
        fallback = AsyncMock(return_value=({("2099-01-14", "NYK@BOS"): {}}, 1))

        with (
            patch("routers.utils.load_persisted_odds", return_value={}),
            patch("routers.utils.get_http_client", return_value=client),
            patch(
                "routers.sharp_game_odds.fetch_sharp_game_odds", fallback
            ),
            patch.dict(
                "routers.utils.os.environ",
                {"SPORTS_GAME_ODDS_KEY": "sgo", "SHARP_API_KEY": "sharp"},
                clear=True,
            ),
        ):
            odds, fresh = await fetch_sgo_odds(
                sport="nba",
                league_id="NBA",
                table_name="nba_odds",
                tricode_for=lambda value: value,
                fallback_tricode_for=lambda value: value,
                target_date=date(2099, 1, 14),
            )

        self.assertEqual(fresh, 1)
        self.assertIn(("2099-01-14", "NYK@BOS"), odds)
        fallback.assert_awaited_once()

    async def test_sgo_failure_awaits_optional_market_fallback(self):
        response = httpx.Response(
            429,
            json={"error": "monthly quota exhausted"},
            request=httpx.Request(
                "GET", "https://api.sportsgameodds.com/v2/events"
            ),
        )
        client = AsyncMock()
        client.get.return_value = response
        optional_fallback = AsyncMock()

        with (
            patch("routers.utils.load_persisted_odds", return_value={}),
            patch("routers.utils.get_http_client", return_value=client),
            patch(
                "routers.sharp_game_odds.fetch_sharp_game_odds",
                AsyncMock(return_value=({}, 0)),
            ),
            patch.dict(
                "routers.utils.os.environ",
                {"SPORTS_GAME_ODDS_KEY": "sgo", "SHARP_API_KEY": "sharp"},
                clear=True,
            ),
        ):
            await fetch_sgo_odds(
                sport="mlb",
                league_id="MLB",
                table_name="mlb_odds",
                tricode_for=lambda value: value,
                target_date=date(2099, 1, 14),
                optional_market_fallback_consumer=optional_fallback,
            )

        optional_fallback.assert_awaited_once()

    async def test_valid_empty_sgo_slate_does_not_spend_sharp_requests(self):
        response = httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request(
                "GET", "https://api.sportsgameodds.com/v2/events"
            ),
        )
        client = AsyncMock()
        client.get.return_value = response
        fallback = AsyncMock()

        with (
            patch("routers.utils.load_persisted_odds", return_value={}),
            patch("routers.utils.get_http_client", return_value=client),
            patch("routers.utils.save_odds_snapshots"),
            patch(
                "routers.sharp_game_odds.fetch_sharp_game_odds", fallback
            ),
            patch.dict(
                "routers.utils.os.environ",
                {"SPORTS_GAME_ODDS_KEY": "sgo", "SHARP_API_KEY": "sharp"},
                clear=True,
            ),
        ):
            odds, fresh = await fetch_sgo_odds(
                sport="nba",
                league_id="NBA",
                table_name="nba_odds",
                tricode_for=lambda value: value,
                target_date=date(2099, 1, 14),
            )

        self.assertEqual((odds, fresh), ({}, 0))
        fallback.assert_not_awaited()

    async def test_sgo_error_envelope_uses_sharp_fallback(self):
        response = httpx.Response(
            200,
            json={"success": False, "error": "entity quota exhausted"},
            request=httpx.Request(
                "GET", "https://api.sportsgameodds.com/v2/events"
            ),
        )
        client = AsyncMock()
        client.get.return_value = response
        fallback = AsyncMock(return_value=({}, 0))

        with (
            patch("routers.utils.load_persisted_odds", return_value={}),
            patch("routers.utils.get_http_client", return_value=client),
            patch(
                "routers.sharp_game_odds.fetch_sharp_game_odds", fallback
            ),
            patch.dict(
                "routers.utils.os.environ",
                {"SPORTS_GAME_ODDS_KEY": "sgo", "SHARP_API_KEY": "sharp"},
                clear=True,
            ),
        ):
            await fetch_sgo_odds(
                sport="nba",
                league_id="NBA",
                table_name="nba_odds",
                tricode_for=lambda value: value,
                target_date=date(2099, 1, 14),
            )

        fallback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
