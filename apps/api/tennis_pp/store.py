"""Where snapshots, refreshes and runs live. Supabase in production, memory in tests."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol


class Store(Protocol):
    def latest_snapshot(self) -> dict | None: ...  # {id, pulled_at}, without rows
    def snapshot(self, snapshot_id: int) -> dict | None: ...  # with rows
    def insert_snapshot(self, rows: list[dict], pulled_at: datetime, event_count: int) -> int: ...
    def running_refresh(self) -> dict | None: ...
    def start_refresh(self, started_at: datetime) -> dict | None: ...  # None if one is already running
    def finish_refresh(self, refresh_id: int, status: str, finished_at: datetime, *, snapshot_id: int | None = None, error: str | None = None) -> None: ...
    def refresh(self, refresh_id: int) -> dict | None: ...
    def insert_run(self, run: dict) -> dict: ...
    def run(self, run_id: str) -> dict | None: ...
    def runs_waiting_on(self, refresh_id: int) -> list[dict]: ...
    def newer_run_id(self, run_id: str) -> str | None: ...
    def set_run_refresh(self, run_id: str, refresh_id: int) -> None: ...


def _iso(value: datetime) -> str:
    return value.isoformat()


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: list[dict] = []
        self.refreshes: list[dict] = []
        self.runs: dict[str, dict] = {}

    def latest_snapshot(self):
        if not self.snapshots:
            return None
        newest = max(self.snapshots, key=lambda s: s["pulled_at"])
        return {"id": newest["id"], "pulled_at": newest["pulled_at"]}

    def snapshot(self, snapshot_id):
        return next((s for s in self.snapshots if s["id"] == snapshot_id), None)

    def insert_snapshot(self, rows, pulled_at, event_count):
        self.snapshots.append({"id": len(self.snapshots) + 1, "pulled_at": _iso(pulled_at), "rows": rows,
                               "row_count": len(rows), "event_count": event_count})
        return self.snapshots[-1]["id"]

    def running_refresh(self):
        return next((r for r in self.refreshes if r["status"] == "running"), None)

    def start_refresh(self, started_at):
        if self.running_refresh():
            return None
        self.refreshes.append({"id": len(self.refreshes) + 1, "status": "running", "started_at": _iso(started_at),
                               "finished_at": None, "snapshot_id": None, "error": None})
        return self.refreshes[-1]

    def finish_refresh(self, refresh_id, status, finished_at, *, snapshot_id=None, error=None):
        self.refresh(refresh_id).update(status=status, finished_at=_iso(finished_at), snapshot_id=snapshot_id, error=error)

    def refresh(self, refresh_id):
        return next((r for r in self.refreshes if r["id"] == refresh_id), None)

    def insert_run(self, run):
        stored = {"id": str(uuid.uuid4()), **run}
        self.runs[stored["id"]] = stored
        return stored

    def run(self, run_id):
        return self.runs.get(run_id)

    def runs_waiting_on(self, refresh_id):
        return [r for r in self.runs.values() if r["refresh_id"] == refresh_id]

    def newer_run_id(self, run_id):
        newer = [r for r in self.runs.values() if r["revises"] == run_id]
        return max(newer, key=lambda r: r["created_at"])["id"] if newer else None

    def set_run_refresh(self, run_id, refresh_id):
        self.runs[run_id]["refresh_id"] = refresh_id


class SupabaseStore:
    """Service-role access only; the tables have no anon or authenticated policies."""

    def __init__(self, client) -> None:
        self._db = client

    def _one(self, query):
        rows = query.limit(1).execute().data
        return rows[0] if rows else None

    def latest_snapshot(self):
        return self._one(self._db.table("tennis_odds_snapshots").select("id,pulled_at").order("pulled_at", desc=True))

    def snapshot(self, snapshot_id):
        return self._one(self._db.table("tennis_odds_snapshots").select("id,pulled_at,rows").eq("id", snapshot_id))

    def insert_snapshot(self, rows, pulled_at, event_count):
        inserted = self._db.table("tennis_odds_snapshots").insert({
            "pulled_at": _iso(pulled_at), "rows": rows, "row_count": len(rows), "event_count": event_count,
        }).execute().data
        return inserted[0]["id"]

    def running_refresh(self):
        return self._one(self._db.table("tennis_odds_refreshes").select("*").eq("status", "running"))

    def start_refresh(self, started_at):
        try:
            inserted = self._db.table("tennis_odds_refreshes").insert({"status": "running", "started_at": _iso(started_at)}).execute().data
        except Exception:
            # The partial unique index allows one running refresh; another request won the race.
            if self.running_refresh():
                return None
            raise
        return inserted[0]

    def finish_refresh(self, refresh_id, status, finished_at, *, snapshot_id=None, error=None):
        self._db.table("tennis_odds_refreshes").update({
            "status": status, "finished_at": _iso(finished_at), "snapshot_id": snapshot_id, "error": error,
        }).eq("id", refresh_id).execute()

    def refresh(self, refresh_id):
        return self._one(self._db.table("tennis_odds_refreshes").select("*").eq("id", refresh_id))

    def insert_run(self, run):
        return self._db.table("tennis_runs").insert(run).execute().data[0]

    def run(self, run_id):
        return self._one(self._db.table("tennis_runs").select("*").eq("id", run_id))

    def runs_waiting_on(self, refresh_id):
        return self._db.table("tennis_runs").select("*").eq("refresh_id", refresh_id).execute().data

    def newer_run_id(self, run_id):
        newer = self._one(self._db.table("tennis_runs").select("id").eq("revises", run_id).order("created_at", desc=True))
        return newer["id"] if newer else None

    def set_run_refresh(self, run_id, refresh_id):
        self._db.table("tennis_runs").update({"refresh_id": refresh_id}).eq("id", run_id).execute()
