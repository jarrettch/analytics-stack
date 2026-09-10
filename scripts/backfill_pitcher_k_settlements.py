#!/usr/bin/env python3
"""Settle one ET slate at a time across a date range.

The nightly cron only revisits the last three days, so odds collected before
that window stay `unresolved_identity` forever and the stale-settlement warning
never clears. This walks `?date=` backfill calls over an explicit range instead.

    scripts/backfill_pitcher_k_settlements.py --since 2026-08-12
    scripts/backfill_pitcher_k_settlements.py --since 2026-08-12 --dry-run

Costs no SportsGameOdds quota: the settlement endpoint reads MLB StatsAPI and
Supabase only. It does fetch one schedule plus one boxscore per final game per
date, so it is paced to stay polite to MLB.

Needs CRON_SECRET (from apps/api/.env, or the environment) and the deployed API
base URL via --api-url or API_URL.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
ET = timezone(timedelta(hours=-4))
SETTLE_PATH = "/api/mlb/cron/settle-pitcher-ks"
# One date fans out to a schedule call plus a boxscore per final game.
REQUEST_TIMEOUT = 300


def daterange(since: date, until: date):
    day = since
    while day <= until:
        yield day
        day += timedelta(days=1)


def settle(client: httpx.Client, api_url: str, secret: str, day: date) -> dict:
    response = client.post(
        f"{api_url.rstrip('/')}{SETTLE_PATH}",
        params={"date": day.isoformat()},
        headers={"Authorization": f"Bearer {secret}"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def describe(day: date, payload: dict) -> tuple[bool, str]:
    """Endpoint returns HTTP 200 with success=false, so read the body."""
    if not payload.get("success"):
        error = payload.get("error") or "reported failure"
        dates = payload.get("dates") or []
        detail = dates[0] if dates else {}
        feed_failures = detail.get("feed_failures") or []
        if feed_failures:
            error = f"{error}; boxscore fetch failed for {feed_failures}"
        return False, f"{day}  FAILED  {error}"

    detail = (payload.get("dates") or [{}])[0]
    identities = detail.get("identity_pitchers") or {}
    return True, (
        f"{day}  {detail.get('final_games', 0):2d} final games, "
        f"{detail.get('result_rows', 0):3d} pitcher rows, "
        f"{identities.get('resolved', 0):2d}/{identities.get('found', 0):2d} identities"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="first ET slate date (YYYY-MM-DD)")
    parser.add_argument("--until", help="last ET slate date; defaults to yesterday")
    parser.add_argument("--api-url", default=os.getenv("API_URL"),
                        help="deployed API base URL; defaults to $API_URL")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="seconds to pause between dates (default 2)")
    parser.add_argument("--dry-run", action="store_true",
                        help="list the dates that would be settled, then exit")
    args = parser.parse_args()

    since = date.fromisoformat(args.since)
    until = (
        date.fromisoformat(args.until)
        if args.until
        else datetime.now(ET).date() - timedelta(days=1)
    )
    if since > until:
        print(f"--since {since} is after --until {until}", file=sys.stderr)
        return 2

    days = list(daterange(since, until))
    if args.dry_run:
        print(f"would settle {len(days)} date(s): {days[0]} through {days[-1]}")
        return 0

    load_dotenv(REPO_ROOT / "apps" / "api" / ".env")
    secret = os.getenv("CRON_SECRET")
    if not (args.api_url and secret):
        print("need --api-url (or $API_URL) and CRON_SECRET", file=sys.stderr)
        return 2

    failures = []
    with httpx.Client() as client:
        for index, day in enumerate(days):
            try:
                payload = settle(client, args.api_url, secret, day)
                ok, line = describe(day, payload)
            except Exception as exc:
                ok, line = False, f"{day}  ERROR   {exc}"
            print(line, flush=True)
            if not ok:
                failures.append(day)
            # Pace the MLB fan-out; no pause needed after the final date.
            if args.delay and index < len(days) - 1:
                time.sleep(args.delay)

    print(f"\n{len(days) - len(failures)}/{len(days)} dates settled")
    if failures:
        print("failed: " + ", ".join(day.isoformat() for day in failures))
        print("re-run those individually with --since/--until once the cause is fixed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
