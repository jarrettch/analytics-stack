"""Write yesterday's NBA props close coverage to the watchdog's job summary.

Standard library only. Coverage is a standing metric, not an alarm: gaps add
one warning annotation and never fail the step. Only a report that can't be
produced fails it. The endpoint only reads, so failed requests are retried.

Environment: API_URL, CRON_SECRET, and GITHUB_STEP_SUMMARY when present.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ATTEMPTS = 3
TIMEOUT_SECONDS = 60


def fetch(api_url: str, secret: str) -> dict:
    request = Request(f"{api_url.rstrip('/')}/api/nba/cron/player-props-coverage", method="POST",
                      headers={"Authorization": f"Bearer {secret}", "Content-Length": "0"})
    for attempt in range(ATTEMPTS):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return json.load(response)
        except Exception as exc:
            if attempt == ATTEMPTS - 1:
                raise
            print(f"Coverage request failed ({exc!r}); retrying in 10 seconds.")
            time.sleep(10)


def render(report: dict) -> tuple[list[str], str | None]:
    """Markdown summary lines, plus one warning when any game has a gap."""
    day = report["game_date"]
    if report["games"] == 0:
        return [f"NBA props close coverage, {day}: no games."], None
    books = list(report["matrix"])
    markets = list(report["matrix"][books[0]])
    lines = [
        f"## NBA props close coverage, {day}",
        f"{report['cells_covered']} of {report['cells_expected']} book × market cells had a usable close "
        f"across {report['games']} games.",
        "",
        "| market | " + " | ".join(books) + " |",
        "|---" * (len(books) + 1) + "|",
    ]
    for market in markets:
        lines.append(f"| {market} | " + " | ".join(
            f"{report['matrix'][book][market]}/{report['games']}" for book in books) + " |")
    gaps = report["game_gaps"]
    if gaps:
        lines += ["", "Games with gaps:"]
    for gap in gaps:
        tip = datetime.fromisoformat(gap["starts_at"]).astimezone(ET)
        parts = []
        if gap["missing"]:
            parts.append("no usable close: " + ", ".join(gap["missing"]))
        if gap["unchecked_only"]:
            parts.append("unchecked combos only: " + ", ".join(gap["unchecked_only"]))
        if gap["failed_rows"]:
            parts.append(f"{gap['failed_rows']} failed combo rows")
        lines.append(f"- {gap['matchup_key']} ({tip:%-I:%M %p} ET): " + "; ".join(parts))
    if report["unexpected_matchups"]:
        lines += ["", "Close rows for matchups not on ESPN's played slate: "
                  + ", ".join(report["unexpected_matchups"])]
    warning = None
    if gaps or report["unexpected_matchups"]:
        warning = (f"NBA props close coverage {day}: {report['cells_covered']}/{report['cells_expected']} cells; "
                   f"{len(gaps)} of {report['games']} games have gaps. See the job summary.")
    return lines, warning


def main(env=os.environ, get=fetch) -> int:
    try:
        report = get(env["API_URL"], env["CRON_SECRET"])
    except Exception as exc:
        print(f"::error::NBA props coverage request failed: {exc!r}")
        return 1
    print(json.dumps(report))
    if not isinstance(report, dict) or report.get("success") is not True:
        error = report.get("error") if isinstance(report, dict) else "response is not a JSON object"
        print(f"::error::NBA props coverage report failed: {error}")
        return 1
    try:
        lines, warning = render(report)
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        print(f"::error::NBA props coverage report is malformed: {exc!r}")
        return 1
    print("\n".join(lines))
    if warning:
        print(f"::warning::{warning}")
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
