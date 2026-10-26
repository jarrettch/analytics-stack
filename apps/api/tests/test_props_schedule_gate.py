from __future__ import annotations

import asyncio
import importlib.util
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.error import URLError

import pytest
from routers import nba_player_props, nfl, nfl_player_props

spec = importlib.util.spec_from_file_location(
    "props_schedule_gate",
    Path(__file__).resolve().parents[3] / "scripts/props_schedule_gate.py",
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NOW = datetime(2026, 10, 25, 21, 37, tzinfo=timezone.utc)  # 17:37 ET


def event(start, away="MIN", home="CHI", *, state="pre", name="STATUS_SCHEDULED"):
    return {"date": start.isoformat(), "competitions": [{
        "status": {"type": {"state": state, "name": name}},
        "competitors": [{"homeAway": "away", "team": {"abbreviation": away}},
                        {"homeAway": "home", "team": {"abbreviation": home}}],
    }]}


def schedule(*games):
    """fetch(league, date) over (league, event) pairs, filtered by ET date."""
    def fetch(league, target):
        return {"events": [e for lg, e in games if lg == league
                           and datetime.fromisoformat(e["date"]).astimezone(gate.ET).date() == target]}
    return fetch


def outputs(path: Path) -> dict:
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 20, 16, tzinfo=timezone.utc),
    datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc),  # ET midnight
    datetime(2026, 11, 27, 19, tzinfo=timezone.utc),   # Black Friday
    datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc),  # London
])
@pytest.mark.parametrize("minutes", [9, 10, 90, 91])
def test_nfl_close_matches_api_window(monkeypatch, now, minutes):
    fetch = schedule(("nfl", event(now + timedelta(minutes=minutes))))
    starts = gate.selected_starts("nfl", now, "close", fetch)
    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status",
                        AsyncMock(side_effect=lambda target: (fetch("nfl", target), True)))
    selected = asyncio.run(nfl_player_props.NFLSchedule().selected(now))
    assert len(starts) == len(selected) == int(10 <= minutes <= 90)


@pytest.mark.parametrize("phase", ["close", "intraday"])
@pytest.mark.parametrize("minutes", [9, 10, 90, 91, 400, -30])
@pytest.mark.parametrize("now", [
    NOW,
    datetime(2026, 10, 25, 15, 37, tzinfo=timezone.utc),  # 11:37 ET
    datetime(2026, 10, 26, 3, 37, tzinfo=timezone.utc),  # 23:37 ET, window crosses midnight
])
def test_nba_matches_api_selection(monkeypatch, now, minutes, phase):
    fetch = schedule(("nba", event(now + timedelta(minutes=minutes), "GS", "PHX")))
    starts = gate.selected_starts("nba", now, phase, fetch)
    monkeypatch.setattr(nba_player_props, "_scoreboard_for_date_with_status",
                        AsyncMock(side_effect=lambda target: (fetch("nba", target), True)))
    selected = asyncio.run(nba_player_props.NBASchedule().selected(now, phase))
    assert len(starts) == len(selected)
    assert [g.starts_at for g in selected] == starts


@pytest.mark.parametrize("state,name", [
    ("in", "STATUS_IN_PROGRESS"), ("post", "STATUS_FINAL"),
    ("pre", "STATUS_POSTPONED"), ("pre", "STATUS_CANCELED"), ("pre", "STATUS_SUSPENDED"),
])
def test_started_or_dead_games_are_never_selected(state, name):
    fetch = schedule(("nba", event(NOW + timedelta(hours=1), state=state, name=name)))
    assert gate.selected_starts("nba", NOW, "close", fetch) == []
    assert gate.selected_starts("nba", NOW, "intraday", fetch) == []


@pytest.mark.parametrize("dispatched,tick", [
    ("2026-10-25T15:37:01Z", (11, 37)),
    ("2026-10-25T15:50:00Z", (11, 37)),
    ("2026-10-25T16:06:59Z", (11, 37)),
    ("2026-10-25T16:07:00Z", (12, 7)),
    ("2026-10-26T04:03:00Z", (23, 37)),  # 00:03 ET rolls back to the previous hour
])
def test_tick_is_the_latest_slot_at_or_before_dispatch(dispatched, tick):
    found = gate.tick_for(datetime.fromisoformat(dispatched.replace("Z", "+00:00")))
    assert (found.hour, found.minute) == tick


@pytest.mark.parametrize("tick,phase", [((11, 37), "intraday"), ((15, 37), "intraday"),
                                        ((11, 7), "close"), ((12, 7), "close"), ((17, 37), "close")])
def test_intraday_only_on_its_two_ticks(tick, phase):
    assert gate.nba_phase(datetime(2026, 10, 25, *tick, tzinfo=gate.ET)) == phase


def test_late_start_keeps_the_dispatch_ticks_phase(tmp_path):
    dispatched = datetime(2026, 10, 25, 15, 37, 1, tzinfo=timezone.utc)  # 11:37 ET
    started = dispatched + timedelta(minutes=18)  # queued behind another run
    path = tmp_path / "out"
    gate.run_gate(path, started, dispatched_at=dispatched, fetch=schedule())
    assert outputs(path)["nba_phase"] == "intraday"
    assert outputs(path)["window_at"] == "2026-10-25T15:55:01Z"


def test_missing_dispatch_time_falls_back_to_start(tmp_path):
    path = tmp_path / "out"
    gate.run_gate(path, datetime(2026, 10, 25, 15, 40, tzinfo=timezone.utc), dispatched_at=None,
                  fetch=schedule())
    assert outputs(path)["nba_phase"] == "intraday"
    assert gate.dispatch_time({}) is None


def test_both_leagues_write_eligibility_and_deadlines(tmp_path):
    path = tmp_path / "out"
    nfl_start = NOW + timedelta(minutes=48)
    fetch = schedule(("nfl", event(nfl_start)), ("nba", event(NOW + timedelta(minutes=23), "NY", "BOS")),
                     ("nba", event(NOW + timedelta(minutes=53), "GS", "PHX")))
    gate.run_gate(path, NOW, dispatched_at=NOW, fetch=fetch)
    assert outputs(path) == {
        "window_at": "2026-10-25T21:37:00Z", "nba_phase": "close",
        "nfl_gate_ok": "true", "nfl_eligible": "true", "nfl_deadline": "2026-10-25T22:15:00Z",
        "nba_gate_ok": "true", "nba_eligible": "true", "nba_deadline": "2026-10-25T21:50:00Z",
    }


def test_empty_window_is_ok_and_not_eligible(tmp_path):
    path = tmp_path / "out"
    gate.run_gate(path, NOW, dispatched_at=NOW, fetch=schedule())
    values = outputs(path)
    assert values["nfl_gate_ok"] == values["nba_gate_ok"] == "true"
    assert values["nfl_eligible"] == values["nba_eligible"] == "false"
    assert "nfl_deadline" not in values and "nba_deadline" not in values


@pytest.mark.parametrize("failure", [
    URLError("ESPN unavailable"), {"events": None}, {"events": [{}]}, {},
])
def test_one_leagues_failure_still_writes_the_other(tmp_path, failure):
    healthy = schedule(("nfl", event(NOW + timedelta(minutes=48))))

    def fetch(league, target):
        if league == "nba":
            if isinstance(failure, Exception):
                raise failure
            return failure
        return healthy(league, target)

    path = tmp_path / "out"
    gate.run_gate(path, NOW, dispatched_at=NOW, fetch=fetch)
    values = outputs(path)
    assert values["nfl_gate_ok"] == "true" and values["nfl_eligible"] == "true"
    assert values["nba_gate_ok"] == "false"
    assert "nba_eligible" not in values and "nba_deadline" not in values


def test_second_date_failure_fails_only_that_league(tmp_path):
    late = datetime(2026, 10, 26, 3, 37, tzinfo=timezone.utc)  # 23:37 ET

    def fetch(league, target):
        if league == "nba" and target != late.astimezone(gate.ET).date():
            raise URLError("next day unavailable")
        return {"events": []}

    path = tmp_path / "out"
    gate.run_gate(path, late, dispatched_at=late, fetch=fetch)
    assert outputs(path)["nba_gate_ok"] == "false"
    assert outputs(path)["nfl_gate_ok"] == "true"


def test_expired_lookup_never_writes_or_delays_exit(tmp_path):
    release = threading.Event()
    finished = threading.Event()

    def fetch(league, target):
        if league == "nba":
            release.wait(5)
            finished.set()
        return {"events": [event(NOW + timedelta(minutes=30), "NY", "BOS")]} if league == "nba" else {"events": []}

    path = tmp_path / "out"
    started = time.monotonic()
    summary = gate.run_gate(path, NOW, dispatched_at=NOW, fetch=fetch, deadline=0.2)
    assert time.monotonic() - started < 2
    assert "deadline" in summary["nba"]
    before = path.read_text()
    assert outputs(path)["nba_gate_ok"] == "false" and outputs(path)["nfl_gate_ok"] == "true"

    release.set()  # the stuck lookup now succeeds, after the gate has finished
    assert finished.wait(2)
    time.sleep(0.05)
    assert path.read_text() == before


def test_each_league_written_once(tmp_path):
    path = tmp_path / "out"
    gate.run_gate(path, NOW, dispatched_at=NOW, fetch=schedule())
    keys = [line.split("=", 1)[0] for line in path.read_text().splitlines()]
    assert len(keys) == len(set(keys))


def test_schedule_request_retries_are_bounded(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(args)
        raise URLError("offline")

    monkeypatch.setattr(gate, "urlopen", fail)
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    with pytest.raises(URLError):
        gate.fetch_scoreboard("nba", NOW.date())
    assert len(attempts) == 3
