#!/usr/bin/env python3
"""Fetch a day's tennis matches with single-book odds from The Odds API.

Outputs CSV ready to paste into the betting tool:
    tour,start,player_1,ml_1,player_2,ml_2,total_games,over,under,book

Lines come from one book per match — BetOnline first, Pinnacle as fallback
(override with --books). Defaults to tomorrow ET, ATP + WTA US Open.

Usage:
    python scripts/fetch_tennis_odds.py                 # tomorrow's US Open
    python scripts/fetch_tennis_odds.py --date 2026-08-31
    python scripts/fetch_tennis_odds.py --sports tennis_atp_cincinnati_open tennis_wta_cincinnati_open
    python scripts/fetch_tennis_odds.py -o slate.csv    # write to file instead of stdout

Run `python scripts/fetch_tennis_odds.py --list` to see currently active tennis keys.

Requires ODDS_API_KEY in apps/api/.env (or the environment).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from dotenv import load_dotenv

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
API_BASE = "https://api.the-odds-api.com/v4"

# The Odds API sport keys. ATP/WTA share the same tournament suffix.
# Rotate this each Slam — a tournament's keys go inactive once it ends. Inactive
# keys are now detected up front rather than silently returning 0 matches, and
# --list shows what is currently live.
DEFAULT_SPORTS = ["tennis_atp_us_open", "tennis_wta_us_open"]
# Bookmaker keys in preference order; first available per match wins.
DEFAULT_BOOKS = ["betonlineag", "pinnacle"]
# Only the eu region is requested: quota cost is markets x regions, and eu alone
# carries both preferred books (betonlineag is listed in us and eu, pinnacle only
# in eu). Adding us would double the cost for no extra coverage.
REGIONS = "eu"
MARKETS = "h2h,totals"
# Tour label keyed off the sport key, for the output's `tour` column.
TOUR_LABELS = {"tennis_atp_": "ATP", "tennis_wta_": "WTA"}

# Field order in the output CSV.
COLUMNS = [
    "tour", "start", "player_1", "ml_1", "player_2", "ml_2",
    "total_games", "over", "under", "book",
]


def load_api_key() -> str:
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(repo_root, "apps", "api", ".env"))
    key = os.getenv("ODDS_API_KEY")
    if not key:
        sys.exit("ODDS_API_KEY not set (looked in apps/api/.env and environment).")
    return key


def american(price) -> str:
    """The Odds API returns integer American odds; format with explicit sign."""
    if price is None:
        return ""
    return f"+{int(price)}" if price > 0 else f"{int(price)}"


def tour_label(sport_key: str) -> str:
    for prefix, label in TOUR_LABELS.items():
        if sport_key.startswith(prefix):
            return label
    return sport_key


def day_window(target: date) -> tuple[str, str]:
    """ISO8601 UTC bounds for a single ET calendar day (commenceTime is UTC)."""
    start = datetime.combine(target, datetime.min.time(), ET).astimezone(UTC)
    end = datetime.combine(target + timedelta(days=1), datetime.min.time(), ET).astimezone(UTC)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return start.strftime(fmt), end.strftime(fmt)


def pick_book(event: dict, books: list[str]) -> tuple[str | None, dict | None]:
    """Return the first preferred book present on this event, else (None, None)."""
    available = {b["key"]: b for b in event.get("bookmakers", [])}
    for key in books:
        if key in available:
            return key, available[key]
    return None, None


def extract_row(event: dict, sport_key: str, books: list[str]) -> dict:
    start = datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00")).astimezone(ET)
    home, away = event.get("home_team"), event.get("away_team")
    book_key, book = pick_book(event, books)

    moneyline: dict[str, int] = {}
    total_line = over = under = None
    if book:
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for oc in market["outcomes"]:
                    moneyline[oc["name"]] = oc["price"]
            elif market["key"] == "totals":
                for oc in market["outcomes"]:
                    if oc["name"] == "Over":
                        total_line, over = oc.get("point"), oc["price"]
                    elif oc["name"] == "Under":
                        under = oc["price"]

    return {
        "tour": tour_label(sport_key),
        "start": start.strftime("%Y-%m-%d %H:%M ET"),
        "_sort": start,
        "player_1": home,
        "ml_1": american(moneyline.get(home)),
        "player_2": away,
        "ml_2": american(moneyline.get(away)),
        "draw": american(moneyline.get("Draw")),  # soccer 1X2 only; empty otherwise
        "total_games": total_line if total_line is not None else "",
        "over": american(over),
        "under": american(under),
        "book": book_key or "none",
    }


def fetch_sport(client: httpx.Client, key: str, sport_key: str, window: tuple[str, str]) -> list[dict]:
    params = {
        "apiKey": key,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": "american",
        "commenceTimeFrom": window[0],
        "commenceTimeTo": window[1],
    }
    resp = client.get(f"{API_BASE}/sports/{sport_key}/odds", params=params, timeout=25)
    if resp.status_code != 200:
        sys.exit(f"{sport_key}: HTTP {resp.status_code} — {resp.text[:200]}")
    remaining = resp.headers.get("x-requests-remaining")
    rows = [extract_row(e, sport_key, FETCH_BOOKS) for e in resp.json()]
    print(f"  {sport_key}: {len(rows)} match(es)  [credits left: {remaining}]", file=sys.stderr)
    return rows


def active_tennis_keys(client: httpx.Client, key: str) -> dict[str, str]:
    """{sport_key: title} for tennis tournaments currently live.

    /v4/sports costs no quota, so checking here is free and saves paying
    markets x regions for a tournament that has already ended.
    """
    resp = client.get(f"{API_BASE}/sports", params={"apiKey": key}, timeout=25)
    resp.raise_for_status()
    return {
        s["key"]: s["title"]
        for s in resp.json()
        if s.get("group") == "Tennis" and s.get("active")
    }


def drop_inactive(requested: list[str], active: dict[str, str]) -> list[str]:
    """Keep only live keys, explaining any that were dropped.

    Deliberately does not substitute a different tournament — silently pulling a
    slate you didn't ask for is worse than saying nothing is live.
    """
    live = [k for k in requested if k in active]
    for sport_key in requested:
        if sport_key not in active:
            print(f"  {sport_key}: not active, skipping", file=sys.stderr)
    if not live:
        print(
            "\nNo requested tournament is active. Currently live:", file=sys.stderr
        )
        for sport_key, title in sorted(active.items()):
            print(f"  {sport_key:34} {title}", file=sys.stderr)
        if not active:
            print("  (no tennis tournaments are live right now)", file=sys.stderr)
        print(
            "\nPass --sports with one of the above, or update DEFAULT_SPORTS.",
            file=sys.stderr,
        )
    return live


def list_active_tennis(client: httpx.Client, key: str) -> None:
    resp = client.get(f"{API_BASE}/sports", params={"apiKey": key}, timeout=25)
    resp.raise_for_status()
    print("Active tennis sport keys:", file=sys.stderr)
    for s in resp.json():
        if s.get("group") == "Tennis" and s.get("active"):
            print(f"  {s['key']:32} {s['title']}")


# Module-level so extract_row's caller doesn't need it threaded through; set in main().
FETCH_BOOKS = DEFAULT_BOOKS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="ET date YYYY-MM-DD (default: tomorrow ET)")
    parser.add_argument("--sports", nargs="+", default=DEFAULT_SPORTS, help="Odds API sport keys")
    parser.add_argument("--books", nargs="+", default=DEFAULT_BOOKS, help="Book keys, preference order")
    parser.add_argument("-o", "--output", help="Write CSV to this file (default: stdout)")
    parser.add_argument("--list", action="store_true", help="List active tennis keys and exit")
    args = parser.parse_args()

    global FETCH_BOOKS
    FETCH_BOOKS = args.books

    key = load_api_key()
    with httpx.Client() as client:
        if args.list:
            list_active_tennis(client, key)
            return

        target = date.fromisoformat(args.date) if args.date else datetime.now(ET).date() + timedelta(days=1)
        window = day_window(target)
        print(f"Fetching {target} ET — books: {', '.join(args.books)}", file=sys.stderr)

        sports = drop_inactive(args.sports, active_tennis_keys(client, key))
        if not sports:
            sys.exit(1)

        rows: list[dict] = []
        for sport_key in sports:
            rows.extend(fetch_sport(client, key, sport_key, window))

    rows.sort(key=lambda r: (r["tour"], r["_sort"]))
    for r in rows:
        r.pop("_sort", None)

    # Only surface the draw column when a book actually priced one (soccer 1X2).
    columns = list(COLUMNS)
    if any(r.get("draw") for r in rows):
        columns.insert(columns.index("ml_2") + 1, "draw")

    out = open(args.output, "w", newline="") if args.output else sys.stdout
    try:
        out.write(", ".join(columns) + "\n")
        for r in rows:
            out.write(", ".join(str(r.get(c, "")) for c in columns) + "\n")
    finally:
        if args.output:
            out.close()
            print(f"Wrote {len(rows)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
