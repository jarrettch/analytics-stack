"""Collect NFL and NBA props for one workflow run, earliest guard deadline first.

Standard library only. Reads the gate's outputs from the environment, calls
each eligible league's cron endpoint once, and checks the response the way the
NFL workflow's jq block did. Collection requests are never retried: a timeout
or 5xx leaves the database outcome unknown. Each request gets a total
wall-clock limit, enforced by abandoning its worker thread, because socket
timeouts only bound each read and a trickling response could otherwise use up
the next league's time. Every eligible league is attempted; the exit status is
non-zero if any league's gate or collection failed.

Environment: API_URL, CRON_SECRET, GITHUB_RUN_ID, and the gate outputs
WINDOW_AT, NBA_PHASE, <LEAGUE>_GATE_OK, <LEAGUE>_ELIGIBLE, <LEAGUE>_DEADLINE.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode, urlsplit

LEAGUES = ("nfl", "nba")
# Above the API's 480 s sweep timeout, so the server reports its own timeout first.
LEAGUE_LIMIT_SECONDS = 540
CONNECT_TIMEOUT_SECONDS = 20


class CollectionError(Exception):
    pass


@dataclass(frozen=True)
class Planned:
    league: str
    deadline: datetime
    path: str


def plan(env) -> tuple[list[Planned], list[str]]:
    """Eligible leagues in earliest-deadline order, plus gate failures."""
    run_id, window_at = env["GITHUB_RUN_ID"], env["WINDOW_AT"]
    planned, failures = [], []
    for league in LEAGUES:
        prefix = league.upper()
        if env.get(f"{prefix}_GATE_OK") != "true":
            failures.append(f"{prefix}: schedule gate failed; nothing was collected")
            continue
        if env.get(f"{prefix}_ELIGIBLE") != "true":
            continue
        params = {"window_at": window_at}
        if league == "nba":
            phase = env["NBA_PHASE"]
            params.update(collection_key=f"{run_id}-nba-props-{phase}", phase=phase)
        else:
            # Unchanged from the NFL-only workflow, so reruns keep deduplicating.
            params.update(collection_key=f"{run_id}-nfl-props")
        deadline = datetime.fromisoformat(env[f"{prefix}_DEADLINE"].replace("Z", "+00:00"))
        planned.append(Planned(league, deadline, f"/api/{league}/cron/fetch-player-props?{urlencode(params)}"))
    return sorted(planned, key=lambda p: p.deadline), failures


def post(api_url: str, path: str, secret: str, limit: float) -> tuple[int, bytes]:
    """One POST with a connect timeout and a total wall-clock limit. Never retried."""
    target = urlsplit(api_url)
    outcome: dict = {}

    def request() -> None:
        try:
            connection_class = (http.client.HTTPSConnection if target.scheme == "https"
                                else http.client.HTTPConnection)
            connection = connection_class(target.netloc, timeout=CONNECT_TIMEOUT_SECONDS)
            connection.connect()
            # The server holds the response for the whole sweep, so reads get the full limit.
            connection.sock.settimeout(limit)
            connection.request("POST", target.path.rstrip("/") + path,
                               headers={"Authorization": f"Bearer {secret}", "Content-Length": "0"})
            response = connection.getresponse()
            outcome["result"] = (response.status, response.read())
            connection.close()
        except Exception as exc:
            outcome["error"] = exc

    worker = threading.Thread(target=request, daemon=True)
    worker.start()
    worker.join(limit)
    if worker.is_alive():
        raise CollectionError(f"no complete response within {limit:.0f} s; the database outcome is unknown")
    if "error" in outcome:
        raise CollectionError(f"request failed ({outcome['error']!r}); the database outcome may be unknown")
    return outcome["result"]


def problems(response) -> list[str]:
    """The NFL workflow's jq conditions. Missing fields fail closed."""
    if not isinstance(response, dict):
        return ["response is not a JSON object"]
    found = [f"{field} is not true" for field in ("success", "schedule_ok", "sweep_complete", "persistence_ok")
             if response.get(field) is not True]
    if "markets_requested" not in response or response.get("markets_completed") != response["markets_requested"]:
        found.append("not every market completed")
    if response.get("unmapped_teams") != []:
        found.append(f"unmapped teams: {response.get('unmapped_teams')}")
    if response.get("rows_rejected") != 0:
        found.append(f"rows_rejected: {response.get('rows_rejected')}")
    return found


def summarize(league: str, response: dict) -> str:
    if response.get("games_selected") == 0:
        return f"{league.upper()}: no eligible games after the API schedule recheck; nothing to collect."
    line = (f"{league.upper()} {response.get('phase', 'close')}: {response.get('rows_inserted')} rows inserted, "
            f"{response.get('rows_deduplicated')} duplicates, {response.get('rows_skipped')} incidental rows "
            f"skipped; {response.get('markets_completed')} markets, {response.get('games_selected')} games "
            f"selected, {response.get('games_persisted')} archived.")
    checks = response.get("combo_checks")
    if checks:
        line += " Combo checks: " + "; ".join(
            f"{book} {c['passed']} passed, {c['failed']} failed, {c['unchecked']} unchecked"
            for book, c in checks.items())
    return line


def collect(planned: Planned, env, limit: float, send=post) -> tuple[bool, str]:
    try:
        status, body = send(env["API_URL"], planned.path, env["CRON_SECRET"], limit)
    except CollectionError as exc:
        return False, f"{planned.league.upper()}: {exc}"
    print(f"{planned.league.upper()} response ({status}): {body.decode(errors='replace')}")
    if not 200 <= status < 300:
        return False, f"{planned.league.upper()}: HTTP {status}"
    try:
        response = json.loads(body)
    except ValueError:
        return False, f"{planned.league.upper()}: response is not JSON"
    found = problems(response)
    if found:
        return False, f"{planned.league.upper()} collection reported a failure: {', '.join(found)}"
    return True, summarize(planned.league, response)


def main(env=os.environ, limit: float = LEAGUE_LIMIT_SECONDS, send=post) -> int:
    planned, failures = plan(env)
    lines = list(failures)
    if not planned and not failures:
        lines.append("No eligible NFL or NBA games in this window; nothing was collected.")
    for item in planned:
        print(f"Collecting {item.league.upper()} (guard deadline {item.deadline:%H:%M:%S}Z).")
        ok, line = collect(item, env, limit, send)
        lines.append(line)
        if not ok:
            failures.append(line)
    for line in lines:
        print(f"::error::{line}" if line in failures else line)
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("\n".join(lines) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
