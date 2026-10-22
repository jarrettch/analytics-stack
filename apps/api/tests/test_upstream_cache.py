"""Coverage for UpstreamCache, the per-game cache behind detail pages and card
sparklines. Every viewer polls those every 15s, so without it upstream traffic
from our one server IP grows with viewers rather than with live games.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from routers.utils import UpstreamCache


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class UpstreamCacheTest(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch("routers.utils.time.monotonic", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cache = UpstreamCache(ttl=15, retention=3600)
        self.loads = 0

    def fetch(self, key="game-1", result=None, error=None):
        async def load():
            self.loads += 1
            if error:
                raise error
            return result if result is not None else {"load": self.loads}

        return asyncio.run(self.cache.fetch(key, load))

    def test_viewers_polling_within_the_ttl_share_one_request(self):
        first = self.fetch()
        self.clock.now += 14
        second = self.fetch()

        self.assertEqual((first, second, self.loads), ({"load": 1}, {"load": 1}, 1))

    def test_refetches_once_the_ttl_passes(self):
        self.fetch()
        self.clock.now += 15

        self.assertEqual(self.fetch(), {"load": 2})

    def test_failed_refresh_serves_the_last_copy(self):
        self.fetch()
        self.clock.now += 60

        self.assertEqual(self.fetch(error=RuntimeError("ESPN 503")), {"load": 1})

    def test_failure_with_nothing_cached_raises(self):
        with self.assertRaises(RuntimeError):
            self.fetch(error=RuntimeError("ESPN 503"))

    def test_games_are_cached_separately(self):
        self.fetch("game-1")

        self.assertEqual(self.fetch("game-2"), {"load": 2})

    def test_drops_entries_not_refreshed_within_retention(self):
        self.fetch("finished-game")
        self.clock.now += 3601
        self.fetch("live-game")

        self.assertNotIn("finished-game", self.cache._entries)
        self.assertIn("live-game", self.cache._entries)


if __name__ == "__main__":
    unittest.main()
