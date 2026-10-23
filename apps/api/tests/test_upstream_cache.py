"""Coverage for UpstreamCache, the per-game cache behind detail pages and card
sparklines. Every viewer polls those every 15s, so without it upstream traffic
from our one server IP grows with viewers rather than with live games.
"""
from __future__ import annotations

import asyncio
import gc
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

    def test_concurrent_misses_share_one_refresh(self):
        """Viewers polling on the same cadence miss together; one request should go out."""
        async def slow_load():
            self.loads += 1
            await asyncio.sleep(0)  # yields without the patched clock; a timed sleep would never wake
            return {"load": self.loads}

        async def burst():
            return await asyncio.gather(*(self.cache.fetch("game-1", slow_load) for _ in range(20)))

        results = asyncio.run(burst())

        self.assertEqual(self.loads, 1)
        self.assertEqual(results, [{"load": 1}] * 20)

    def test_concurrent_misses_share_a_failure_too(self):
        async def failing_load():
            self.loads += 1
            await asyncio.sleep(0)  # yields without the patched clock; a timed sleep would never wake
            raise RuntimeError("ESPN 503")

        async def burst():
            return await asyncio.gather(
                *(self.cache.fetch("game-1", failing_load) for _ in range(5)), return_exceptions=True
            )

        results = asyncio.run(burst())

        self.assertEqual(self.loads, 1)
        self.assertTrue(all(isinstance(r, RuntimeError) for r in results))

    def test_freshness_counts_from_when_the_load_finished(self):
        """A 10s upstream response must not leave only 5s of a 15s TTL."""
        async def slow_load():
            self.loads += 1
            self.clock.now += 10
            return {"load": self.loads}

        asyncio.run(self.cache.fetch("game-1", slow_load))
        self.clock.now += 14

        self.assertEqual(asyncio.run(self.cache.fetch("game-1", slow_load)), {"load": 1})

    def test_does_not_serve_a_copy_older_than_retention(self):
        """During a long outage, a live game shouldn't show hours-old data as current."""
        self.fetch()
        self.clock.now += 3000
        self.assertEqual(self.fetch(error=RuntimeError("ESPN 503")), {"load": 1})

        self.clock.now += 601
        with self.assertRaises(RuntimeError):
            self.fetch(error=RuntimeError("ESPN 503"))
        self.assertNotIn("game-1", self.cache._entries)

    def test_a_cancelled_waiter_does_not_cancel_the_refresh_for_others(self):
        """A viewer closing the page mid-request mustn't fail everyone else's poll."""
        release = asyncio.Event()

        async def gated_load():
            self.loads += 1
            await release.wait()
            return {"load": self.loads}

        async def scenario():
            leaving = asyncio.ensure_future(self.cache.fetch("game-1", gated_load))
            staying = asyncio.ensure_future(self.cache.fetch("game-1", gated_load))
            await asyncio.sleep(0)
            leaving.cancel()
            release.set()
            return await staying, leaving.cancelled()

        result, cancelled = asyncio.run(scenario())

        self.assertEqual((result, cancelled, self.loads), ({"load": 1}, True, 1))

    def test_a_failure_nobody_awaits_is_not_reported_as_unretrieved(self):
        """If the only waiter leaves and the refresh then fails, asyncio would log
        "Task exception was never retrieved" when the task is collected.

        Only reproduces on Python 3.14+: through 3.13, shield() retrieves the
        exception itself, so on production's 3.11 this passes with or without the
        fix. It guards the upgrade."""
        unhandled = []
        release = asyncio.Event()

        async def gated_failing_load():
            await release.wait()
            raise RuntimeError("ESPN 503")

        async def scenario():
            asyncio.get_running_loop().set_exception_handler(lambda _loop, context: unhandled.append(context))
            waiter = asyncio.ensure_future(self.cache.fetch("game-1", gated_failing_load))
            await asyncio.sleep(0)
            waiter.cancel()
            release.set()
            for _ in range(5):
                await asyncio.sleep(0)
            # The cancelled waiter's traceback still references the refresh task;
            # asyncio only reports an unretrieved exception once the task is collected.
            del waiter
            gc.collect()

        asyncio.run(scenario())

        self.assertEqual(unhandled, [])

    def test_drops_entries_not_refreshed_within_retention(self):
        self.fetch("finished-game")
        self.clock.now += 3601
        self.fetch("live-game")

        self.assertNotIn("finished-game", self.cache._entries)
        self.assertIn("live-game", self.cache._entries)


if __name__ == "__main__":
    unittest.main()
