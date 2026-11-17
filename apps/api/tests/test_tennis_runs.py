"""Run lifecycle: staleness, on-demand refresh, immutable runs and revisions."""
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import tennis as tennis_router
from tennis_pp.board import RunRequest
from tennis_pp.runs import LEAGUES, MARKETS, TennisRuns
from tennis_pp.store import MemoryStore

FIXTURES = Path(__file__).parent / "fixtures" / "tennis_pp"
SNAPSHOT = json.loads((FIXTURES / "snapshot_11-15.json").read_text())
BOARD = [row for row in json.loads((FIXTURES / "board_11-15.json").read_text())
         if "Alcaraz" in row["player"] + row["opponent"]]
T0 = datetime(2026, 11, 16, 3, tzinfo=timezone.utc)


class Harness:
    def __init__(self):
        self.clock = T0
        self.store = MemoryStore()
        self.calls: list[tuple] = []
        self.fail_on: str | None = None
        self.gate: asyncio.Event | None = None
        self.runs = TennisRuns(self.store, self.fetch, now=lambda: self.clock)

    async def fetch(self, league, market, *, main_only):
        self.calls.append((league, market, main_only))
        if self.gate:
            await self.gate.wait()
        if market == self.fail_on:
            raise RuntimeError("SharpAPI page cap reached")
        return [row for row in SNAPSHOT if row["league"] == league and row["market_type"] == market]

    def seed_snapshot(self, age: timedelta):
        return self.store.insert_snapshot(SNAPSHOT, self.clock - age, 19)

    async def upload(self):
        return await self.runs.create(BOARD, self.clock)


class RunLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_snapshot_prices_at_once_without_a_refresh(self):
        h = Harness()
        h.seed_snapshot(timedelta(minutes=5))
        run = await h.upload()
        self.assertEqual((run["status"], run["refresh"], run["odds_age_s"]), ("ready", None, 300))
        self.assertTrue(run["results"])
        self.assertFalse(any("stale_odds" in r["flags"] for r in run["results"]))
        self.assertEqual(h.calls, [])

    async def test_stale_snapshot_prices_flagged_and_revises_when_the_refresh_lands(self):
        h = Harness()
        h.seed_snapshot(timedelta(hours=2))
        run = await h.upload()
        self.assertEqual(run["status"], "ready")
        self.assertTrue(all("stale_odds" in r["flags"] for r in run["results"]))
        # Pricing runs off the event loop, so the refresh may already have landed.
        self.assertIsNotNone(run["refresh"])

        await h.runs.wait_for_refreshes()
        self.assertEqual(h.calls, [(league, market, main) for league in LEAGUES for market, main in MARKETS])
        after = h.runs.view(run["run_id"])
        # The stored run is untouched; the reprice is a new run.
        self.assertEqual(after["results"], run["results"])
        self.assertEqual(after["refresh"]["status"], "done")
        revision = h.runs.view(after["newer_run_id"])
        self.assertEqual((revision["revises"], revision["status"], revision["refresh"]), (run["run_id"], "ready", None))
        self.assertFalse(any("stale_odds" in r["flags"] for r in revision["results"]))

    async def test_cold_start_is_pending_until_the_refresh_lands(self):
        for age in (None, timedelta(hours=7)):
            h = Harness()
            if age:
                h.seed_snapshot(age)
            run = await h.upload()
            self.assertEqual((run["status"], run["results"], run["odds_snapshot_at"]), ("pending", [], None))
            await h.runs.wait_for_refreshes()
            revision = h.runs.view(h.runs.view(run["run_id"])["newer_run_id"])
            self.assertEqual(revision["status"], "ready")
            self.assertTrue(revision["results"])

    async def test_a_failed_sweep_writes_no_snapshot_and_retry_starts_another(self):
        h = Harness()
        h.seed_snapshot(timedelta(hours=2))
        h.fail_on = "player_aces"
        run = await h.upload()
        await h.runs.wait_for_refreshes()
        failed = h.runs.view(run["run_id"])
        self.assertEqual(failed["refresh"]["status"], "failed")
        self.assertIn("page cap", failed["refresh"]["error"])
        self.assertEqual(len(h.store.snapshots), 1)
        self.assertIsNone(failed["newer_run_id"])
        self.assertEqual(failed["results"], run["results"])

        h.fail_on = None
        retried = h.runs.retry(run["run_id"])
        self.assertEqual(retried["refresh"]["status"], "running")
        self.assertNotEqual(retried["refresh"]["id"], failed["refresh"]["id"])
        await h.runs.wait_for_refreshes()
        self.assertIsNotNone(h.runs.view(run["run_id"])["newer_run_id"])

    async def test_two_uploads_during_one_refresh_share_it(self):
        h = Harness()
        h.gate = asyncio.Event()
        first, second = await h.upload(), await h.upload()
        self.assertEqual(first["refresh"]["id"], second["refresh"]["id"])
        self.assertEqual(len(h.store.refreshes), 1)
        h.gate.set()
        await h.runs.wait_for_refreshes()
        for run in (first, second):
            self.assertIsNotNone(h.runs.view(run["run_id"])["newer_run_id"])
        self.assertEqual(len(h.calls), len(LEAGUES) * len(MARKETS))

    async def test_an_abandoned_refresh_is_failed_and_replaced(self):
        h = Harness()
        # A refresh whose task died with a deploy: running, but nothing is working on it.
        lost = h.store.start_refresh(h.clock - timedelta(minutes=7))
        run = await h.upload()
        self.assertEqual(h.store.refresh(lost["id"])["status"], "failed")
        self.assertEqual(h.store.refresh(lost["id"])["error"], "abandoned")
        self.assertNotEqual(run["refresh"]["id"], lost["id"])
        await h.runs.wait_for_refreshes()

    async def test_an_old_run_reads_the_same_after_a_newer_snapshot(self):
        h = Harness()
        h.seed_snapshot(timedelta(minutes=5))
        run = await h.upload()
        h.clock += timedelta(days=7)
        h.seed_snapshot(timedelta(0))
        self.assertEqual(h.runs.view(run["run_id"]), run)

    async def test_started_rows_are_flagged_when_priced(self):
        h = Harness()
        h.clock = datetime(2026, 11, 16, 20, tzinfo=timezone.utc)  # after the 13:00Z start
        h.seed_snapshot(timedelta(minutes=5))
        run = await h.upload()
        self.assertTrue(all("started" in r["flags"] for r in run["results"]))


class ValidationTest(unittest.TestCase):
    def payload(self, **overrides):
        row = {**BOARD[0], **overrides}
        return json.dumps({"board_saved_at": T0.isoformat(), "rows": [row]})

    def test_a_real_row_validates(self):
        board = RunRequest.model_validate_json(self.payload()).board(T0)
        self.assertEqual(board[0]["player"], BOARD[0]["player"])

    def test_rejects_what_the_page_should_never_send(self):
        for bad in (
            {"status": "in_progress"},
            {"player": "Bolelli / Vavassori"},
            {"player": "<script>"},
            {"stat": "Break Points Won"},
            {"odds_type": "flash"},
            {"line": 61},
            {"line": "NaN"},
            {"projection_id": "abc"},
            {"game_id": "1" * 21},
            {"start_time": "2026-11-16T08:00:00"},
            {"extra": 1},
        ):
            with self.assertRaises(ValueError, msg=str(bad)):
                RunRequest.model_validate_json(self.payload(**bad))

    def test_rejects_a_start_time_more_than_four_days_out(self):
        request = RunRequest.model_validate_json(self.payload())
        with self.assertRaises(ValueError):
            request.board(T0 + timedelta(days=6))

    def test_accented_and_hyphenated_names_are_allowed(self):
        RunRequest.model_validate_json(self.payload(player="Félix Auger-Aliassime", opponent="Christopher O'Connell"))


class EndpointTest(unittest.TestCase):
    def setUp(self):
        self.h = Harness()
        self.h.clock = datetime.now(timezone.utc)
        self.h.seed_snapshot(timedelta(minutes=5))
        app = FastAPI()
        app.include_router(tennis_router.router)
        app.dependency_overrides[tennis_router.get_service] = lambda: self.h.runs
        self.client = TestClient(app)
        self.auth = {"Authorization": "Bearer hunter2"}
        start = (self.h.clock + timedelta(hours=6)).isoformat()
        self.body = {"board_saved_at": self.h.clock.isoformat(), "rows": [{**row, "start_time": start} for row in BOARD]}

    def test_requires_the_upload_secret_and_fails_closed(self):
        with patch.dict("os.environ", {"PREP_UPLOAD_SECRET": ""}):
            self.assertEqual(self.client.post("/api/tennis/runs", json=self.body, headers=self.auth).status_code, 503)
        with patch.dict("os.environ", {"PREP_UPLOAD_SECRET": "hunter2"}):
            self.assertEqual(self.client.post("/api/tennis/runs", json=self.body).status_code, 401)
            wrong = {"Authorization": "Bearer nope"}
            self.assertEqual(self.client.post("/api/tennis/runs", json=self.body, headers=wrong).status_code, 401)
            # The cron secret is a different secret.
            with patch.dict("os.environ", {"CRON_SECRET": "cron"}):
                cron = {"Authorization": "Bearer cron"}
                self.assertEqual(self.client.post("/api/tennis/runs", json=self.body, headers=cron).status_code, 401)

    def test_upload_then_read_back(self):
        with patch.dict("os.environ", {"PREP_UPLOAD_SECRET": "hunter2"}):
            created = self.client.post("/api/tennis/runs", json=self.body, headers=self.auth)
            self.assertEqual(created.status_code, 200, created.text)
            run = created.json()
            self.assertEqual(run["status"], "ready")
            read = self.client.get(f"/api/tennis/runs/{run['run_id']}", headers=self.auth)
            self.assertEqual(read.json(), run)
            missing = self.client.get("/api/tennis/runs/00000000-0000-0000-0000-000000000000", headers=self.auth)
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(self.client.get("/api/tennis/runs/not-a-uuid", headers=self.auth).status_code, 422)

    def test_rejects_oversized_and_invalid_boards(self):
        with patch.dict("os.environ", {"PREP_UPLOAD_SECRET": "hunter2"}):
            huge = {**self.body, "rows": self.body["rows"] * 400}
            self.assertEqual(self.client.post("/api/tennis/runs", json=huge, headers=self.auth).status_code, 413)
            bad = {**self.body, "rows": [{**self.body["rows"][0], "status": "final"}]}
            self.assertEqual(self.client.post("/api/tennis/runs", json=bad, headers=self.auth).status_code, 422)
            self.assertEqual(self.client.post("/api/tennis/runs", content=b"not json", headers=self.auth).status_code, 422)


if __name__ == "__main__":
    unittest.main()
