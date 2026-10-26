"""Check ESPN on the Actions runner for NFL and NBA props before waking Render.

Standard library only. Each league's lookup runs in its own daemon thread with
its own deadline, so one league's ESPN outage can't stop the other's outputs.
Only the main thread writes outputs, once per league, as each lookup finishes;
a lookup that finishes after its deadline is ignored. The API repeats every
check before collecting.

Outputs: window_at, nba_phase, and per league <league>_gate_ok, plus
<league>_eligible and <league>_deadline (earliest selected start minus the
guard) when the lookup succeeded. A failed league writes only gate_ok=false.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ESPN_URLS = {
    "nfl": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
    "nba": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
}
ET = ZoneInfo("America/New_York")
GUARD = timedelta(minutes=10)
WINDOW = timedelta(minutes=90)
DEAD_STATUSES = {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED", "STATUS_SUSPENDED"}
# One scoreboard lookup can take ~75 s (three 20 s attempts plus backoff).
LEAGUE_DEADLINE_SECONDS = 100
# pg_cron dispatches at these minutes; 11:37 and 15:37 ET are NBA intraday.
TICK_MINUTES = (7, 37)
INTRADAY_TICKS = {(11, 37), (15, 37)}


def fetch_scoreboard(league: str, target: date) -> dict:
    url = f"{ESPN_URLS[league]}?dates={target:%Y%m%d}&limit=400"
    for attempt in range(3):
        try:
            with urlopen(url, timeout=20) as response:
                return json.load(response)
        except (URLError, TimeoutError, ValueError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def _games(payload, target: date) -> list[datetime]:
    """Pregame start times on one date, failing on anything malformed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise TypeError(f"Invalid ESPN schedule for {target}")
    starts = []
    for event in payload["events"]:
        competition = event["competitions"][0]
        status = (competition.get("status") or event.get("status") or {}).get("type") or {}
        if status.get("state") not in {"pre", "in", "post"}:
            raise ValueError(f"Invalid ESPN game status for {target}")
        start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        if start.tzinfo is None:
            raise ValueError("ESPN start time has no timezone")
        teams = {c["homeAway"]: c["team"]["abbreviation"] for c in competition["competitors"]}
        if not teams.get("away") or not teams.get("home") or teams["away"] == teams["home"]:
            raise ValueError("ESPN game has invalid teams")
        if status["state"] == "pre" and status.get("name") not in DEAD_STATUSES:
            starts.append((start, teams["away"], teams["home"]))
    return starts


def selected_starts(league: str, now: datetime, phase: str, fetch=fetch_scoreboard) -> list[datetime]:
    """The start times the API would select, deduplicated per game."""
    today = now.astimezone(ET).date()
    if phase == "intraday":
        dates = [today]
    else:
        dates = sorted({today, (now + WINDOW).astimezone(ET).date()})
    games = set()
    for target in dates:
        for start, away, home in _games(fetch(league, target), target):
            if phase == "intraday" and now + GUARD <= start:
                games.add((start, away, home))
            elif phase == "close" and now + GUARD <= start <= now + WINDOW:
                games.add((start, away, home))
    return sorted(start for start, _, _ in games)


def tick_for(dispatched_at: datetime) -> datetime:
    """The pg_cron tick a dispatch belongs to: the latest :07/:37 at or before it, in ET."""
    local = dispatched_at.astimezone(ET).replace(second=0, microsecond=0)
    slots = [m for m in TICK_MINUTES if m <= local.minute]
    if slots:
        return local.replace(minute=max(slots))
    return (local - timedelta(hours=1)).replace(minute=max(TICK_MINUTES))


def nba_phase(tick: datetime) -> str:
    return "intraday" if (tick.hour, tick.minute) in INTRADAY_TICKS else "close"


def dispatch_time(env=os.environ) -> datetime | None:
    """When pg_cron dispatched this run. Unchanged by queueing and reruns."""
    try:
        request = Request(
            f"https://api.github.com/repos/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}",
            headers={"Authorization": f"Bearer {env['GITHUB_TOKEN']}",
                     "Accept": "application/vnd.github+json"},
        )
        with urlopen(request, timeout=10) as response:
            created = json.load(response)["created_at"]
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except Exception as exc:  # The fallback can lose an intraday sweep, never a close.
        print(f"Could not read the run's created_at ({exc}); using the gate's start time for the phase.")
        return None


def write_outputs(output: Path, values: dict) -> None:
    with output.open("a") as stream:
        for key, value in values.items():
            stream.write(f"{key}={value}\n")


def run_gate(output: Path, now: datetime, *, dispatched_at: datetime | None,
             fetch=fetch_scoreboard, deadline: float = LEAGUE_DEADLINE_SECONDS) -> dict:
    phase = nba_phase(tick_for(dispatched_at or now))
    phases = {"nfl": "close", "nba": phase}
    write_outputs(output, {"window_at": f"{now.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}",
                           "nba_phase": phase})

    results: queue.Queue = queue.Queue()

    def lookup(league: str) -> None:
        try:
            results.put((league, selected_starts(league, now, phases[league], fetch), None))
        except Exception as exc:
            results.put((league, None, exc))

    # Both deadlines run in parallel from here. Daemon threads never hold up exit.
    for league in phases:
        threading.Thread(target=lookup, args=(league,), daemon=True).start()
    ends_at = time.monotonic() + deadline
    pending = set(phases)
    summary = {}
    while pending:
        remaining = ends_at - time.monotonic()
        if remaining <= 0:
            break
        try:
            league, starts, error = results.get(timeout=remaining)
        except queue.Empty:
            break
        pending.discard(league)
        if error is not None:
            write_outputs(output, {f"{league}_gate_ok": "false"})
            summary[league] = f"lookup failed: {error!r}"
            continue
        values = {f"{league}_gate_ok": "true", f"{league}_eligible": "true" if starts else "false"}
        if starts:
            values[f"{league}_deadline"] = f"{(starts[0] - GUARD).astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}"
        write_outputs(output, values)
        summary[league] = f"{len(starts)} {phases[league]} games"
    for league in sorted(pending):
        write_outputs(output, {f"{league}_gate_ok": "false"})
        summary[league] = f"lookup missed its {deadline:.0f} s deadline"
    return summary


if __name__ == "__main__":
    started = datetime.now(timezone.utc).replace(microsecond=0)
    summary = run_gate(Path(os.environ["GITHUB_OUTPUT"]), started, dispatched_at=dispatch_time())
    for league, line in summary.items():
        print(f"ESPN gate {league.upper()}: {line}; checked {started.astimezone(ET).isoformat()}")
