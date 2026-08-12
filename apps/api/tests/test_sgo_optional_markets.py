from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import httpx

from routers.utils import SGO_ODD_IDS, fetch_sgo_odds


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.params = []

    async def get(self, url, *, params, headers):
        self.params.append(params)
        return self.responses.pop(0)


def _response(status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "https://api.sportsgameodds.com/v2/events"),
    )


class OptionalMarketsFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_request_shape_error_retries_game_markets_and_skips_consumer(self):
        client = _Client([
            _response(400, {"error": "bad oddID"}),
            _response(200, {"data": []}),
        ])
        consumer_called = False
        fallback_called = False

        async def consumer(events, captured_at):
            nonlocal consumer_called
            consumer_called = True

        def on_fallback():
            nonlocal fallback_called
            fallback_called = True

        with (
            patch("routers.utils.load_persisted_odds", return_value={}),
            patch("routers.utils.get_http_client", return_value=client),
            patch.dict("routers.utils.os.environ", {"SPORTS_GAME_ODDS_KEY": "test"}),
        ):
            odds, fresh = await fetch_sgo_odds(
                league_id="MLB",
                table_name="mlb_odds",
                tricode_for=lambda team_id: team_id,
                target_date=date(2026, 8, 11),
                extra_odd_ids=["pitching_strikeouts-PLAYER_ID-game-ou-over"],
                include_alt_lines=True,
                include_open_close_odds=True,
                response_consumer=consumer,
                optional_market_fallback_consumer=on_fallback,
            )

        self.assertEqual((odds, fresh), ({}, 0))
        self.assertEqual(len(client.params), 2)
        self.assertIn("pitching_strikeouts", client.params[0]["oddID"])
        self.assertEqual(client.params[1]["oddID"], ",".join(SGO_ODD_IDS))
        self.assertNotIn("includeAltLines", client.params[1])
        self.assertNotIn("includeOpenCloseOdds", client.params[1])
        self.assertFalse(consumer_called)
        self.assertTrue(fallback_called)


if __name__ == "__main__":
    unittest.main()
