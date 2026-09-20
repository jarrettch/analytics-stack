from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.error import URLError

import pytest
from routers import nfl, nfl_player_props

spec = importlib.util.spec_from_file_location(
    "nfl_props_schedule_gate",
    Path(__file__).resolve().parents[3] / "scripts/nfl_props_schedule_gate.py",
)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def event(start, *, state="pre", name="STATUS_SCHEDULED"):
    return {"date": start.isoformat(), "competitions": [{
        "status": {"type": {"state": state, "name": name}},
        "competitors": [{"homeAway": "away", "team": {"abbreviation": "MIN"}},
                        {"homeAway": "home", "team": {"abbreviation": "CHI"}}],
    }]}


@pytest.mark.parametrize("now", [
    datetime(2026, 9, 20, 16, tzinfo=timezone.utc),
    datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc),  # ET midnight
    datetime(2026, 11, 27, 19, tzinfo=timezone.utc),   # Black Friday
    datetime(2027, 1, 16, 20, tzinfo=timezone.utc),   # Saturday playoffs
    datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc),  # London
])
@pytest.mark.parametrize("minutes", [9, 10, 90, 91])
def test_runner_gate_matches_api_window(monkeypatch, now, minutes):
    game = event(now + timedelta(minutes=minutes))
    calls = []

    def fetch(target):
        calls.append(target)
        day = datetime.fromisoformat(game["date"]).astimezone(gate.ET).date()
        return {"events": [game] if target == day else []}

    count = gate.qualifying_games(now, fetch)
    runner_dates = calls.copy()
    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status",
                        AsyncMock(side_effect=lambda target: (fetch(target), True)))
    selected = asyncio.run(nfl_player_props.NFLSchedule().selected(now))
    assert count == len(selected) == int(10 <= minutes <= 90)
    assert runner_dates == sorted({now.astimezone(gate.ET).date(),
                                   (now + timedelta(minutes=90)).astimezone(gate.ET).date()})


@pytest.mark.parametrize("state,name", [
    ("in", "STATUS_IN_PROGRESS"), ("post", "STATUS_FINAL"),
    ("pre", "STATUS_POSTPONED"), ("pre", "STATUS_CANCELED"),
])
def test_runner_excludes_started_or_postponed_games(state, name):
    now = datetime(2026, 9, 20, 16, tzinfo=timezone.utc)
    assert gate.qualifying_games(now, lambda _: {
        "events": [event(now + timedelta(hours=1), state=state, name=name)],
    }) == 0


@pytest.mark.parametrize("count", [0, 1])
def test_gate_outputs_control_wake_step(tmp_path, count):
    now = datetime(2026, 9, 20, 16, tzinfo=timezone.utc)
    output = tmp_path / "outputs"
    gate.write_gate(output, now, lambda _: {"events": [event(now + timedelta(hours=1))] * count})
    assert output.read_text() == (
        f"eligible={'true' if count else 'false'}\nwindow_at=2026-09-20T16:00:00Z\n"
    )


@pytest.mark.parametrize("payload", [{}, {"events": None}, {"events": [{}]}])
def test_bad_schedule_fails_without_publishing_gate_outputs(tmp_path, payload):
    output = tmp_path / "outputs"
    with pytest.raises((TypeError, ValueError, KeyError)):
        gate.write_gate(output, datetime(2026, 9, 20, 16, tzinfo=timezone.utc), lambda _: payload)
    assert not output.exists()


def test_second_day_failure_does_not_publish_positive_gate(tmp_path):
    now = datetime(2026, 9, 21, 3, 30, tzinfo=timezone.utc)

    def fetch(target):
        if target == now.astimezone(gate.ET).date():
            return {"events": [event(now + timedelta(minutes=10))]}
        raise URLError("ESPN unavailable")

    output = tmp_path / "outputs"
    with pytest.raises(URLError):
        gate.write_gate(output, now, fetch)
    assert not output.exists()


def test_schedule_request_retries_are_bounded(monkeypatch):
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(args)
        raise URLError("offline")

    monkeypatch.setattr(gate, "urlopen", fail)
    monkeypatch.setattr(gate.time, "sleep", lambda _: None)
    with pytest.raises(URLError):
        gate.fetch_scoreboard(datetime(2026, 9, 20, tzinfo=timezone.utc).date())
    assert len(attempts) == 3
