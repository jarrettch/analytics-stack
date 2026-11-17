"""Runs: price an uploaded board on the newest odds snapshot, refreshing it on demand.

A stored run never changes. When a refresh lands, every run waiting on it is repriced as a new
run that `revises` the old one. See specs/tennis_fs_upload.md, "Refresh lifecycle".
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from .config import DEFAULT_CONFIG, ModelConfig
from .pricing import build_events, price_board
from .store import Store

logger = logging.getLogger(__name__)

LEAGUES = ("atp", "wta")
# (market, main lines only). The total and ace ladders need their alternates.
MARKETS = (
    ("moneyline", True),
    ("total_games", False),
    ("game_spread", True),
    ("player_aces", False),
    ("player_games_won", True),
)


@dataclass(frozen=True)
class RunConfig:
    stale_after_s: int = 20 * 60
    # Past this the matches on the board have mostly changed; stale prices would mislead.
    max_snapshot_age_s: int = 6 * 3600
    # About twice the slowest measured pull. A running refresh older than this lost its task
    # to a deploy or restart.
    refresh_lease_s: int = 6 * 60


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class TennisRuns:
    def __init__(
        self,
        store: Store,
        fetch: Callable[..., Awaitable[list[dict]]],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        run_cfg: RunConfig = RunConfig(),
        model_cfg: ModelConfig = DEFAULT_CONFIG,
    ) -> None:
        self.store, self._fetch, self._now = store, fetch, now
        self.run_cfg, self.model_cfg = run_cfg, model_cfg
        self._tasks: set[asyncio.Task] = set()
        # Fits per snapshot, so repeat uploads against one snapshot don't refit.
        self._fits: OrderedDict[int, dict] = OrderedDict()

    # --- public -------------------------------------------------------------

    async def create(self, board: list[dict], board_saved_at: datetime) -> dict:
        now = self._now()
        snapshot = self.store.latest_snapshot()
        age = (now - _parse(snapshot["pulled_at"])).total_seconds() if snapshot else None
        fresh = age is not None and age <= self.run_cfg.stale_after_s
        usable = age is not None and age <= self.run_cfg.max_snapshot_age_s
        refresh = None if fresh else self._start_or_join_refresh()
        results = await self._price(board, snapshot["id"], now, stale=not fresh) if usable else []
        run = self.store.insert_run(self._run_row(
            now, board, board_saved_at, results,
            snapshot_id=snapshot["id"] if usable else None,
            refresh_id=refresh["id"] if refresh else None,
        ))
        # Pricing runs off the event loop, so the refresh can land before this run is stored
        # and miss it. Reprice it here in that case.
        landed = self.store.refresh(refresh["id"]) if refresh else None
        if landed and landed["status"] == "done" and self.store.newer_run_id(run["id"]) is None:
            await self._revise(run, landed["snapshot_id"])
        return self._view(run)

    def view(self, run_id: str) -> dict | None:
        run = self.store.run(run_id)
        return self._view(run) if run else None

    async def set_manual_lines(self, run_id: str, lines: list[dict]) -> dict | None:
        """Reprice a run with hand-entered lines as a new run on the same snapshot. Raises
        ValueError where the run can't take them."""
        run = self.store.run(run_id)
        if not run:
            return None
        if self.store.newer_run_id(run_id) is not None:
            raise ValueError("This run has been repriced; reload it and enter the lines there.")
        if run["snapshot_id"] is None:
            raise ValueError("This run has no odds yet; enter lines once it is priced.")
        now = self._now()
        age = (now - _parse(self.store.snapshot(run["snapshot_id"])["pulled_at"])).total_seconds()
        results = await self._price(
            run["board"], run["snapshot_id"], now, stale=age > self.run_cfg.stale_after_s, manual=lines,
        )
        # Keeps the refresh the run was waiting on, so the new run is repriced when it lands.
        revised = self.store.insert_run(self._run_row(
            now, run["board"], _parse(run["board_saved_at"]), results,
            snapshot_id=run["snapshot_id"], refresh_id=run["refresh_id"], revises=run_id, manual=lines,
        ))
        landed = self.store.refresh(run["refresh_id"]) if run["refresh_id"] is not None else None
        if landed and landed["status"] == "done" and landed["snapshot_id"] != run["snapshot_id"] \
                and self.store.newer_run_id(revised["id"]) is None:
            await self._revise(revised, landed["snapshot_id"])
        return self._view(revised)

    def retry(self, run_id: str) -> dict | None:
        """After a failed refresh: start or join one and point the run at it."""
        run = self.store.run(run_id)
        if not run:
            return None
        refresh = self._start_or_join_refresh()
        self.store.set_run_refresh(run_id, refresh["id"])
        return self.view(run_id)

    async def wait_for_refreshes(self) -> None:
        """Tests only: let in-flight refresh tasks finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    # --- refresh ------------------------------------------------------------

    def _start_or_join_refresh(self) -> dict:
        now = self._now()
        running = self.store.running_refresh()
        if running and (now - _parse(running["started_at"])).total_seconds() > self.run_cfg.refresh_lease_s:
            self.store.finish_refresh(running["id"], "failed", now, error="abandoned")
            running = None
        if running:
            return running
        started = self.store.start_refresh(now)
        if started is None:  # another request started one between the two calls
            return self.store.running_refresh()
        task = asyncio.create_task(self._refresh(started["id"]))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return started

    async def _refresh(self, refresh_id: int) -> None:
        # All ten league x market sweeps or nothing: a snapshot is only ever used whole.
        try:
            rows: list[dict] = []
            for league in LEAGUES:
                for market, main_only in MARKETS:
                    rows.extend(await self._fetch(league, market, main_only=main_only))
            now = self._now()
            snapshot_id = self.store.insert_snapshot(rows, now, len(build_events(rows, self.model_cfg)))
            self.store.finish_refresh(refresh_id, "done", now, snapshot_id=snapshot_id)
        except Exception as exc:  # noqa: BLE001 - any failure fails the refresh, never the process
            logger.warning("Tennis odds refresh %s failed: %s", refresh_id, exc)
            self.store.finish_refresh(refresh_id, "failed", self._now(), error=str(exc)[:300] or type(exc).__name__)
            return
        for waiting in self.store.runs_waiting_on(refresh_id):
            try:
                await self._revise(waiting, snapshot_id)
            except Exception:  # noqa: BLE001
                logger.exception("Repricing tennis run %s failed", waiting["id"])

    async def _revise(self, run: dict, snapshot_id: int) -> None:
        """Reprice a run on a new snapshot as a new run. The stored run is left as it was."""
        if self.store.newer_run_id(run["id"]) is not None:
            return
        now = self._now()
        manual = run.get("manual_lines") or []
        results = await self._price(run["board"], snapshot_id, now, stale=False, manual=manual)
        if self.store.newer_run_id(run["id"]) is None:
            self.store.insert_run(self._run_row(
                now, run["board"], _parse(run["board_saved_at"]), results,
                snapshot_id=snapshot_id, refresh_id=None, revises=run["id"], manual=manual,
            ))

    # --- pricing ------------------------------------------------------------

    async def _price(self, board: list[dict], snapshot_id: int, now: datetime, *, stale: bool, manual: list[dict] | None = None) -> list[dict]:
        snapshot = self.store.snapshot(snapshot_id)
        fits = self._fits.setdefault(snapshot_id, {})
        self._fits.move_to_end(snapshot_id)
        while len(self._fits) > 3:
            self._fits.popitem(last=False)
        # CPU-bound pure Python: keep the event loop serving the dashboard.
        results = await asyncio.to_thread(
            price_board, board, snapshot["rows"], now=now, cfg=self.model_cfg, fit_cache=fits, manual=manual,
        )
        if stale:
            for result in results:
                # Typed-in lines are as fresh as the user made them, whatever the snapshot's age.
                if "manual_odds" not in result["flags"]:
                    result["flags"].append("stale_odds")
        return results

    def _run_row(self, now, board, board_saved_at, results, *, snapshot_id, refresh_id, revises=None, manual=None) -> dict:
        return {
            "created_at": now.isoformat(),
            "status": "ready" if snapshot_id is not None else "pending",
            "board_saved_at": board_saved_at.isoformat(),
            "snapshot_id": snapshot_id, "refresh_id": refresh_id, "revises": revises,
            "board": board, "results": results, "manual_lines": manual or [],
            "model_version": os.getenv("RENDER_GIT_COMMIT", "dev"),
            "model_config": {**self.model_cfg.as_dict(), **asdict(self.run_cfg)},
        }

    def _view(self, run: dict) -> dict:
        snapshot = self.store.snapshot(run["snapshot_id"]) if run["snapshot_id"] is not None else None
        refresh = self.store.refresh(run["refresh_id"]) if run["refresh_id"] is not None else None
        pulled_at = snapshot["pulled_at"] if snapshot else None
        return {
            "run_id": run["id"], "status": run["status"], "created_at": run["created_at"],
            "board_saved_at": run["board_saved_at"], "revises": run["revises"],
            "odds_snapshot_at": pulled_at,
            # Age when the run was priced, so an old run reads the same next week.
            "odds_age_s": round((_parse(run["created_at"]) - _parse(pulled_at)).total_seconds()) if pulled_at else None,
            "refresh": {k: refresh[k] for k in ("id", "status", "started_at", "error")} if refresh else None,
            "newer_run_id": self.store.newer_run_id(run["id"]),
            "manual_lines": run.get("manual_lines") or [],
            "results": run["results"],
        }
