#!/usr/bin/env python3
"""Report Oh-K Corral starter coverage: MLB probables vs. pitchers we priced.

SportsGameOdds omits pitcher-strikeout props for pitchers whose player entity
still carries a stale `teamID` after a trade, so a slate silently arrives with
five or six starters missing. This diffs MLB's probable pitchers for a slate
date against the distinct pitchers stored in `mlb_pitcher_k_odds`, and annotates
each miss with the pitcher's 2026 team changes so a provider-side gap is
distinguishable from a market books genuinely never posted.

    scripts/pitcher_k_coverage.py [--date YYYY-MM-DD] [--min-coverage 0.90]
    scripts/pitcher_k_coverage.py --since 2026-08-12    # every slate to date

Exits 1 when coverage falls below --min-coverage, so it can run as a cron check.
Reads MLB StatsAPI and Supabase only; it never calls SportsGameOdds, so a
historical sweep costs no provider quota.
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv
from supabase import create_client

REPO_ROOT = Path(__file__).resolve().parent.parent
ET = timezone(timedelta(hours=-4))  # matches the API's ET slate handling
MLB_SCHEDULE = "https://statsapi.mlb.com/api/v1/schedule"
MLB_STATS = "https://statsapi.mlb.com/api/v1/people/{pid}/stats"
PAGE = 1000


def normalize(name: str) -> str:
    """Fold accents, punctuation and suffixes so SGO and MLB spellings compare."""
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.lower().replace(".", "").replace("'", "").replace("-", " ")
    parts = [p for p in folded.split() if p not in {"jr", "sr", "ii", "iii", "iv"}]
    return " ".join(parts)


def slate_date() -> date:
    return datetime.now(ET).date()


def probables(game_date: date) -> list[dict]:
    """Every listed probable starter for the date, with its MLB identifiers."""
    response = httpx.get(
        MLB_SCHEDULE,
        params={
            "sportId": 1,
            "date": game_date.isoformat(),
            "hydrate": "probablePitcher,team",
        },
        timeout=30,
    )
    response.raise_for_status()
    out = []
    for day in response.json().get("dates", []):
        for game in day.get("games", []):
            teams = game.get("teams") or {}
            away = ((teams.get("away") or {}).get("team") or {}).get("abbreviation", "?")
            home = ((teams.get("home") or {}).get("team") or {}).get("abbreviation", "?")
            for side in ("away", "home"):
                pitcher = (teams.get(side) or {}).get("probablePitcher") or {}
                if not pitcher.get("id"):
                    continue
                out.append({
                    "name": pitcher.get("fullName", ""),
                    "mlb_id": pitcher["id"],
                    "team": away if side == "away" else home,
                    "matchup": f"{away}@{home}",
                    "game_pk": game.get("gamePk"),
                })
    return out


def priced_pitchers(supabase, game_date: date) -> dict[str, str]:
    """Normalized name -> stored SGO player_id, for everyone we have a price on."""
    found: dict[str, str] = {}
    start = 0
    while True:
        response = (
            supabase.table("mlb_pitcher_k_odds")
            .select("player_id,player_name")
            .eq("game_date", game_date.isoformat())
            .order("id")
            .range(start, start + PAGE - 1)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            found[normalize(row.get("player_name") or row["player_id"])] = row["player_id"]
        if len(rows) < PAGE:
            return found
        start += PAGE


_team_change_cache: dict[int, str] = {}


def team_changes(mlb_id: int) -> str:
    """2026 team sequence from the pitcher's game log; '' when it can't be read.

    A trade is the signature of the known provider bug, so it is worth naming
    inline rather than making every investigation start from scratch. Cached
    because a sweep re-encounters the same missing pitcher every fifth day.
    """
    if mlb_id in _team_change_cache:
        return _team_change_cache[mlb_id]
    try:
        response = httpx.get(
            MLB_STATS.format(pid=mlb_id),
            params={"stats": "gameLog", "group": "pitching", "season": 2026, "sportId": 1},
            timeout=30,
        )
        response.raise_for_status()
        sequence: list[str] = []
        for block in response.json().get("stats", []):
            for split in block.get("splits", []):
                # Game-log splits carry the club as id/name only, no tricode.
                team_row = split.get("team") or {}
                team = team_row.get("abbreviation") or team_row.get("name")
                if team and (not sequence or sequence[-1] != team):
                    sequence.append(team)
        result = " -> ".join(sequence) if len(sequence) > 1 else ""
    except Exception:
        result = ""
    _team_change_cache[mlb_id] = result
    return result


def measure(supabase, game_date: date) -> tuple[list[dict], int]:
    """(probables with no stored price, total probables listed) for one slate."""
    expected = probables(game_date)
    if not expected:
        return [], 0
    found = priced_pitchers(supabase, game_date)
    return [p for p in expected if normalize(p["name"]) not in found], len(expected)


def report_day(supabase, game_date: date, min_coverage: float) -> int:
    missing, total = measure(supabase, game_date)
    if not total:
        print(f"{game_date}: no probables listed yet — nothing to check")
        return 0
    covered = total - len(missing)
    ratio = covered / total
    print(f"{game_date}  starters priced: {covered}/{total}  ({ratio:.0%})")
    if missing:
        print(f"\n{len(missing)} probable(s) with no stored price:")
        for pitcher in sorted(missing, key=lambda p: p["matchup"]):
            moves = team_changes(pitcher["mlb_id"])
            reason = f"traded: {moves}" if moves else "no team change"
            print(f"  {pitcher['matchup']:9} {pitcher['name']:22} "
                  f"(mlb {pitcher['mlb_id']}, gamePk {pitcher['game_pk']})  {reason}")
    if ratio < min_coverage:
        print(f"\nFAIL: coverage {ratio:.0%} below threshold {min_coverage:.0%}")
        return 1
    return 0


def sweep(supabase, since: date, through: date) -> int:
    """Per-slate coverage across a date range, then the pitchers behind the gap."""
    totals = missed = 0
    offenders: dict[str, list] = {}
    day = since
    while day <= through:
        missing, total = measure(supabase, day)
        if total:
            totals += total
            missed += len(missing)
            covered = total - len(missing)
            bar = "#" * covered + "." * len(missing)
            print(f"  {day}  {covered:2d}/{total:2d}  {bar}")
            for pitcher in missing:
                offenders.setdefault(pitcher["name"], []).append((day, pitcher["mlb_id"]))
        day += timedelta(days=1)

    if not totals:
        print("no slates with data in that range")
        return 0

    print(f"\ntotal: {totals - missed}/{totals} starters priced "
          f"({(totals - missed) / totals:.1%})  |  {missed} missing")
    print(f"\ndistinct pitchers never or sometimes priced ({len(offenders)}):")
    traded = 0
    for name, hits in sorted(offenders.items(), key=lambda kv: -len(kv[1])):
        moves = team_changes(hits[0][1])
        traded += 1 if moves else 0
        reason = f"traded: {moves}" if moves else "no team change"
        print(f"  {name:24} missed {len(hits):2d} start(s)   {reason}")
    print(f"\n{traded} of {len(offenders)} changed teams in 2026")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="ET slate date (YYYY-MM-DD); defaults to today")
    parser.add_argument("--since", help="sweep every slate from this date through --date")
    parser.add_argument("--min-coverage", type=float, default=0.90,
                        help="exit 1 below this ratio (default 0.90)")
    args = parser.parse_args()

    game_date = date.fromisoformat(args.date) if args.date else slate_date()

    load_dotenv(REPO_ROOT / "apps" / "api" / ".env")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        print("SUPABASE_URL / SUPABASE_KEY not set", file=sys.stderr)
        return 2
    supabase = create_client(url, key)

    if args.since:
        return sweep(supabase, date.fromisoformat(args.since), game_date)
    return report_day(supabase, game_date, args.min_coverage)


if __name__ == "__main__":
    sys.exit(main())
