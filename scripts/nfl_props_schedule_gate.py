"""Check ESPN on the Actions runner before spending any Render active time.

Standard library only. A failed or malformed schedule fails the Action instead
of reporting an empty window. The API repeats the check before collecting.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from zoneinfo import ZoneInfo

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ET = ZoneInfo("America/New_York")


def fetch_scoreboard(target):
    url = f"{ESPN_URL}?dates={target:%Y%m%d}&limit=100"
    for attempt in range(3):
        try:
            with urlopen(url, timeout=20) as response:
                return json.load(response)
        except (URLError, TimeoutError, ValueError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))


def qualifying_games(now: datetime, fetch=fetch_scoreboard) -> int:
    earliest = now + timedelta(minutes=10)
    latest = now + timedelta(minutes=90)
    dates = sorted({now.astimezone(ET).date(), latest.astimezone(ET).date()})
    games = set()
    for target in dates:
        payload = fetch(target)
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise TypeError(f"Invalid ESPN schedule for {target}")
        for event in payload["events"]:
            competition = event["competitions"][0]
            status = (competition.get("status") or event.get("status") or {}).get("type") or {}
            if status.get("state") not in {"pre", "in", "post"}:
                raise ValueError(f"Invalid ESPN game status for {target}")
            start = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            if start.tzinfo is None:
                raise ValueError("ESPN kickoff has no timezone")
            if (status["state"] == "pre"
                    and status.get("name") not in {"STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED"}
                    and earliest <= start <= latest):
                teams = {c["homeAway"]: c["team"]["abbreviation"] for c in competition["competitors"]}
                if not teams.get("away") or not teams.get("home") or teams["away"] == teams["home"]:
                    raise ValueError("ESPN game has invalid teams")
                games.add((start, teams["away"], teams["home"]))
    return len(games)


def write_gate(output: Path, now: datetime, fetch=fetch_scoreboard) -> int:
    count = qualifying_games(now, fetch)
    # Write only after all needed date lookups succeed, including ET midnight.
    with output.open("a") as stream:
        stream.write(f"eligible={'true' if count else 'false'}\n")
        stream.write(f"window_at={now.astimezone(timezone.utc):%Y-%m-%dT%H:%M:%SZ}\n")
    earliest = (now + timedelta(minutes=10)).astimezone(ET)
    latest = (now + timedelta(minutes=90)).astimezone(ET)
    print(f"ESPN gate: {count} qualifying NFL games; checked {now.astimezone(ET).isoformat()}; "
          f"kickoff window {earliest.isoformat()} through {latest.isoformat()}")
    return count


if __name__ == "__main__":
    write_gate(Path(os.environ["GITHUB_OUTPUT"]), datetime.now(timezone.utc).replace(microsecond=0))
