"""UFC next-card page: ESPN card structure, SharpAPI DraftKings/FanDuel odds.

See specs/ufc_page.md. ESPN is the single source of identity: the card, fight
order, weight class, and every persisted key come from it. SharpAPI supplies
prices and nothing else, joined to ESPN fights by fighter name.

A false-positive join would write one fight's price under another fight's
ESPN id into append-only history, so the join rejects every ambiguity rather
than breaking ties.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException

from . import utils
from .auth import require_cron_auth
from .sharp_odds import SharpOddsError, SharpSweep, fetch_sharp_odds
from .utils import (
    CACHE_TTL_DEFAULT,
    CACHE_TTL_LIVE,
    ET,
    UTC,
    Cache,
    de_vig_ml,
    format_et_time,
    get_http_client,
    load_persisted_odds,
    save_odds_snapshots,
    save_persisted_odds,
    slate_date_et,
    to_num,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ufc", tags=["UFC"])

ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/mma/ufc/scoreboard"
TABLE_NAME = "ufc_odds"
LEAGUE = "ufc"
BOOKS = ("draftkings", "fanduel")  # display preference, in order
MARKETS = ("moneyline", "total_rounds", "go_the_distance")
# Books disagree on start times by hours; this only bounds the candidate pool
# to one card; identity always comes from ESPN.
CANDIDATE_WINDOW = timedelta(hours=24)

MARKET_FIELDS = {
    "moneyline": ("fighterAMoneyline", "fighterBMoneyline"),
    "total_rounds": ("totalRounds", "overOdds", "underOdds"),
    "go_the_distance": ("goesDistanceYes", "goesDistanceNo"),
}

cache = Cache({
    "calendar": 3600,
    "assembled": CACHE_TTL_DEFAULT,
})


# --- card selection ---------------------------------------------------------


# Excluded before any positive rule: development showcases and grappling.
EXCLUDED_EVENTS = ("Contender Series", "UFC BJJ")
# "UFC 332", and also branded numbered cards like "UFC Freedom 250". A
# number is required, so unnumbered UFC-branded events stay out.
NUMBERED_CARD = re.compile(r"^UFC\s+(?:\w+\s+)?\d")
# Named explicitly rather than by widening the regex: two words before the
# number would also admit "UFC BJJ Invitational 2".
BRANDED_CARDS = ("UFC Fight Night", "Noche UFC", "UFC on ESPN", "UFC on ABC")


def is_ufc_card(label: str) -> bool | None:
    """True for an MMA card, False for an excluded event, None if unrecognized.

    Numbered events, Fight Nights, and known branded specials only: other
    UFC-branded events are not fight cards and must not displace the next one.
    Grappling is excluded outright: "UFC BJJ 2" is a numbered series that the
    numbered-card pattern would otherwise admit.
    """
    if any(excluded in label for excluded in EXCLUDED_EVENTS):
        return False
    if NUMBERED_CARD.match(label) or any(brand in label for brand in BRANDED_CARDS):
        return True
    return None


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def next_card_entry(calendar: list[dict], today: date) -> dict | None:
    """First UFC calendar entry on or after `today` (ET), in calendar order."""
    candidates = []
    for entry in calendar:
        label = str(entry.get("label") or "")
        starts = _parse_time(entry.get("startDate"))
        if starts is None:
            continue
        if starts.astimezone(ET).date() < today:
            continue  # past entries stay quiet, so the warning below stays rare
        verdict = is_ufc_card(label)
        if verdict is None:
            # UFC branding changes; a new format should surface, not vanish.
            logger.warning("Unrecognized UFC calendar entry skipped: %r", label)
            continue
        if verdict:
            candidates.append((starts, entry))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _event_id_from_ref(entry: dict) -> str | None:
    match = re.search(r"/events/(\d+)", str((entry.get("event") or {}).get("$ref") or ""))
    return match.group(1) if match else None


async def _espn_get(params: dict | None = None) -> dict:
    response = await get_http_client().get(ESPN_SCOREBOARD_URL, params=params or {})
    response.raise_for_status()
    return response.json()


async def _calendar() -> list[dict]:
    cached = cache.get("calendar")
    if cached:
        return cached
    try:
        payload = await _espn_get()
        calendar = ((payload.get("leagues") or [{}])[0]).get("calendar") or []
    except Exception:
        stale = cache.get_stale("calendar")
        if stale:
            logger.warning("UFC calendar fetch failed; using stale calendar", exc_info=True)
            return stale
        raise
    cache.set("calendar", calendar)
    return calendar


async def next_card(today: date) -> tuple[dict, date] | None:
    """The next card's ESPN event, fetched explicitly by its ET date."""
    entry = next_card_entry(await _calendar(), today)
    if entry is None:
        return None
    card_date = _parse_time(entry["startDate"]).astimezone(ET).date()
    payload = await _espn_get({"dates": card_date.strftime("%Y%m%d")})
    events = payload.get("events") or []
    event_id = _event_id_from_ref(entry)
    event = next((e for e in events if str(e.get("id")) == event_id), None) or next(
        (e for e in events if e.get("name") == entry.get("label")), None
    )
    if event is None:
        raise RuntimeError(f"ESPN card {entry.get('label')!r} missing from its {card_date} scoreboard")
    return event, card_date


@dataclass
class Fight:
    fight_id: str
    order: int  # 1 = main event
    weight_class: str
    starts_at: datetime | None
    state: str
    status_detail: str
    broadcast: str
    fighters: list[dict]  # [A, B]: {"name", "id", "winner"}

    def started(self, now: datetime) -> bool:
        return self.state != "pre" or (self.starts_at is not None and self.starts_at <= now)


class CardValidationError(ValueError):
    """The ESPN card cannot supply a safe persistence identity."""


def card_fights(event: dict) -> list[Fight]:
    """ESPN fights, main event first.

    ESPN lists bouts earliest prelim first; a competitor's own `order` is its
    corner within the bout, not the bout's place on the card.

    Every competition id becomes a permanent `matchup_key`, so a missing or
    duplicated one aborts the whole card rather than persisting under "None"
    or colliding with another fight. So does any bout without exactly two
    named, identified fighters: the page promises the full card, and skipping
    a bout would let a collection report success on part of it.

    A card with no bouts yet is valid and empty; ESPN lists announced cards
    before their fights (the Qatar card had no `competitions` at all).
    """
    competitions = event.get("competitions") or []
    ids = [str(c.get("id") or "").strip() for c in competitions]
    if any(not fight_id or fight_id == "None" for fight_id in ids) or len(set(ids)) != len(ids):
        raise CardValidationError(f"ESPN card {event.get('id')} has missing or duplicate competition ids: {ids}")
    for fight_id, competition in zip(ids, competitions):
        competitors = competition.get("competitors") or []
        usable = [
            item for item in competitors
            if str((item.get("athlete") or {}).get("displayName") or "").strip()
            and str(item.get("id") or (item.get("athlete") or {}).get("id") or "").strip()
        ]
        if len(competitors) != 2 or len(usable) != 2:
            raise CardValidationError(f"ESPN fight {fight_id} lacks two named, identified fighters")
        athlete_ids = {
            str(item.get("id") or (item.get("athlete") or {}).get("id")).strip() for item in usable
        }
        if len(athlete_ids) != 2:
            raise CardValidationError(f"ESPN fight {fight_id} lists the same fighter id twice")
    fights = []
    for index, competition in enumerate(competitions):
        competitors = sorted(
            competition.get("competitors") or [],
            key=lambda item: to_num(item.get("order")) or 0,
        )
        status_type = (competition.get("status") or {}).get("type") or {}
        fights.append(Fight(
            fight_id=ids[index],
            order=len(competitions) - index,
            weight_class=str((competition.get("type") or {}).get("abbreviation") or ""),
            starts_at=_parse_time(competition.get("date")),
            state=str(status_type.get("state") or "pre"),
            status_detail=str(status_type.get("shortDetail") or ""),
            broadcast=str(competition.get("broadcast") or ""),
            fighters=[
                {
                    "name": str((item.get("athlete") or {}).get("displayName") or ""),
                    "id": str(item.get("id") or (item.get("athlete") or {}).get("id") or ""),
                    "winner": item.get("winner"),
                }
                for item in competitors
            ],
        ))
    return sorted(fights, key=lambda fight: fight.order)


# --- name matching ----------------------------------------------------------


def normalize_name(name: str) -> str:
    """Accent-stripped, case-folded, hyphens to spaces, punctuation dropped."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    text = text.replace("-", " ")
    text = re.sub(r"[^\w\s]", "", text)
    return " ".join(text.split())


def names_match(espn_name: str, sharp_name: str) -> bool:
    a, b = normalize_name(espn_name), normalize_name(sharp_name)
    if not a or not b:
        return False
    if a == b or a.replace(" ", "") == b.replace(" ", ""):
        return True  # "Alatengheili" / "Alateng Heili"
    # Subset matching needs at least two tokens on the shorter side: a bare
    # "Silva" would otherwise match "Jean Silva" whenever it is the only
    # candidate, and nothing downstream would flag it. Mononyms still match
    # through equality above.
    #
    # Token order is deliberately ignored. Books disagree on it for East
    # Asian names (UFC 332: ESPN and FanDuel "Wang Cong", DraftKings "Cong
    # Wang"), and a reversed-name false positive would still have to pass the
    # both-fighters, opposite-sides, single-claim rules in match_card.
    ta, tb = set(a.split()), set(b.split())
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(shorter) >= 2 and shorter <= longer  # "Jose Delgado" / "Jose Miguel Delgado"


@dataclass
class SharpFight:
    book: str
    event_id: str
    event_uuid: str | None
    names: dict[str, str]  # side ("home"/"away") -> fighter name
    rows: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))


@dataclass
class Binding:
    sharp: SharpFight
    sides: dict[str, int]  # SharpAPI side -> ESPN fighter index (0 = A, 1 = B)


@dataclass
class MatchReport:
    bindings: dict[tuple[str, str], Binding] = field(default_factory=dict)  # (fight_id, book)
    unmatched: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[tuple[str, str]] = field(default_factory=list)


def match_card(fights: list[Fight], pools: dict[str, list[SharpFight]]) -> MatchReport:
    """Bind each ESPN fight to at most one SharpAPI fight per book.

    Accepted only when both ESPN fighters match exactly one SharpAPI fighter
    each, on opposite sides of the same SharpAPI fight, and no other ESPN
    fight claims that SharpAPI fight. Every ambiguity rejects the whole fight.
    """
    report = MatchReport()
    for book, pool in pools.items():
        claims: dict[str, list[tuple[Fight, Binding]]] = defaultdict(list)
        for fight in fights:
            candidates = [
                [
                    (sharp, side)
                    for sharp in pool
                    for side, name in sharp.names.items()
                    if names_match(fighter["name"], name)
                ]
                for fighter in fight.fighters
            ]
            if any(len(found) > 1 for found in candidates):
                report.ambiguous.append((fight.fight_id, book))
                logger.warning(
                    "UFC %s ambiguous match for fight %s (%s): %s",
                    book, fight.fight_id,
                    " vs ".join(f["name"] for f in fight.fighters),
                    [[(s.event_id, side) for s, side in found] for found in candidates],
                )
                continue
            a = candidates[0][0] if candidates[0] else None
            b = candidates[1][0] if candidates[1] else None
            # Both fighters, same SharpAPI fight, opposite sides. Sides carry no
            # meaning beyond that: SharpAPI home/away never maps to ESPN order.
            if a is None or b is None or a[0] is not b[0] or a[1] == b[1]:
                report.unmatched.append((fight.fight_id, book))
                logger.info(
                    "UFC %s no line for fight %s (%s)",
                    book, fight.fight_id, " vs ".join(f["name"] for f in fight.fighters),
                )
                continue
            claims[a[0].event_id].append((fight, Binding(a[0], {a[1]: 0, b[1]: 1})))

        for event_id, claimants in claims.items():
            if len(claimants) > 1:
                for fight, _ in claimants:
                    report.ambiguous.append((fight.fight_id, book))
                logger.warning(
                    "UFC %s fight %s claimed by several ESPN fights %s; all rejected",
                    book, event_id, [fight.fight_id for fight, _ in claimants],
                )
                continue
            fight, binding = claimants[0]
            report.bindings[(fight.fight_id, book)] = binding
    return report


def build_pools(rows_by_market: dict[str, list[dict]], fights: list[Fight]) -> dict[str, list[SharpFight]]:
    """Group SharpAPI rows into per-book fights near this card's start times."""
    starts = [fight.starts_at for fight in fights if fight.starts_at]
    earliest = min(starts) - CANDIDATE_WINDOW if starts else None
    latest = max(starts) + CANDIDATE_WINDOW if starts else None
    pools: dict[tuple[str, str], SharpFight] = {}
    for market, rows in rows_by_market.items():
        for row in rows:
            # The request filters, re-checked: SharpAPI silently ignores
            # unknown parameters, so a typo would otherwise admit live rows.
            if row.get("is_live") is not False or row.get("is_alternate_line") is True:
                continue
            if row.get("is_active") is False:
                continue
            book = row.get("sportsbook")
            event_id = row.get("event_id")
            if book not in BOOKS or not event_id:
                continue
            row_start = _parse_time(row.get("event_start_time"))
            if earliest and (row_start is None or not earliest <= row_start <= latest):
                continue
            key = (book, event_id)
            if key not in pools:
                pools[key] = SharpFight(
                    book=book,
                    event_id=event_id,
                    event_uuid=row.get("event_uuid"),
                    names={"home": str(row.get("home_team") or ""), "away": str(row.get("away_team") or "")},
                )
            pools[key].rows[market].append(row)
    by_book: dict[str, list[SharpFight]] = {book: [] for book in BOOKS}
    for sharp in pools.values():
        by_book[sharp.book].append(sharp)
    return by_book


# --- per-book bundles -------------------------------------------------------


def _unique_price(rows: list[dict], selection_type: str):
    prices = {to_num(row.get("odds_american")) for row in rows if row.get("selection_type") == selection_type}
    prices.discard(None)
    return prices.pop() if len(prices) == 1 else None


def fresh_markets(binding: Binding) -> dict[str, dict]:
    """This capture's values for each market the book returned for the fight.

    A market absent from the result had no row; a market present with
    conflicting rows is present but unpriced rather than picked arbitrarily.
    """
    rows = binding.sharp.rows
    out: dict[str, dict] = {}
    if rows.get("moneyline"):
        prices = [None, None]
        for side, index in binding.sides.items():
            prices[index] = _unique_price(rows["moneyline"], side)
        out["moneyline"] = {"fighterAMoneyline": prices[0], "fighterBMoneyline": prices[1]}
    if rows.get("total_rounds"):
        lines = {to_num(row.get("line")) for row in rows["total_rounds"]}
        lines.discard(None)
        if len(lines) == 1:
            out["total_rounds"] = {
                "totalRounds": lines.pop(),
                "overOdds": _unique_price(rows["total_rounds"], "over"),
                "underOdds": _unique_price(rows["total_rounds"], "under"),
            }
        else:
            logger.warning("UFC %s %s has %d total_rounds lines; left unpriced",
                           binding.sharp.book, binding.sharp.event_id, len(lines))
            out["total_rounds"] = {"totalRounds": None, "overOdds": None, "underOdds": None}
    if rows.get("go_the_distance"):
        out["go_the_distance"] = {
            "goesDistanceYes": _unique_price(rows["go_the_distance"], "yes"),
            "goesDistanceNo": _unique_price(rows["go_the_distance"], "no"),
        }
    return out


def _win_probabilities(bundle: dict) -> tuple[float | None, float | None]:
    a, b = bundle.get("fighterAMoneyline"), bundle.get("fighterBMoneyline")
    if a is None or b is None:
        return None, None
    return de_vig_ml(a, b)


def assemble_book(
    fight: Fight,
    persisted: dict | None,
    fresh: dict[str, dict],
    *,
    event_uuid: str | None,
    captured_at: datetime,
) -> dict:
    """Layer this capture over the persisted bundle for one fight and book.

    A market with no fresh row keeps its persisted value, marked stale: last
    observed, never current. It covers both a whole-market no_match and one
    fight's market going missing from a populated response.
    """
    persisted = persisted or {}
    observed_before = persisted.get("observed") or {}
    bundle: dict = {
        "fighterA": fight.fighters[0]["name"],
        "fighterB": fight.fighters[1]["name"],
        "fighterAEspnId": fight.fighters[0]["id"],
        "fighterBEspnId": fight.fighters[1]["id"],
        "sharpEventUuid": event_uuid or persisted.get("sharpEventUuid"),
    }
    observed: dict = {}
    for market, fields in MARKET_FIELDS.items():
        if market in fresh:
            bundle.update(fresh[market])
            observed[market] = {"lastObservedAt": captured_at.isoformat(), "isStale": False}
            continue
        for name in fields:
            bundle[name] = persisted.get(name)
        had_value = any(persisted.get(name) is not None for name in fields)
        previous = observed_before.get(market) or {}
        observed[market] = {
            "lastObservedAt": previous.get("lastObservedAt"),
            "isStale": had_value,
        }
    bundle["fighterAWinProbability"], bundle["fighterBWinProbability"] = _win_probabilities(bundle)
    bundle["observed"] = observed
    return bundle


def snapshot_data(bundle: dict) -> dict:
    """The bundle as history stores it.

    Timestamps stay out, since they change every capture and would defeat
    consecutive-value dedup. Which markets are stale stays in, or a row
    written because another market moved would present a withdrawn price as
    observed at that capture.
    """
    data = {k: v for k, v in bundle.items() if k != "observed"}
    data["staleMarkets"] = sorted(
        market for market, meta in (bundle.get("observed") or {}).items() if meta.get("isStale")
    )
    return data


def _has_price(bundle: dict) -> bool:
    return any(bundle.get(name) is not None for fields in MARKET_FIELDS.values() for name in fields)


# --- collection -------------------------------------------------------------


def _load_persisted_strict(card_date: date) -> dict:
    """load_persisted_odds, but missing configuration is a failure too."""
    if utils.supabase is None:
        raise RuntimeError("Supabase not configured; persisted UFC odds unavailable")
    return load_persisted_odds(card_date, table_name=TABLE_NAME)


async def collect_card(event: dict, card_date: date, *, captured_at: datetime | None = None) -> dict:
    """One capture of the next card: every market, both books, one write.

    Raises on an invalid card, a failed persisted read, or an incomplete
    SharpAPI sweep; in each case nothing has been written and persisted values
    carry forward.
    """
    captured_at = captured_at or datetime.now(UTC)
    fights = card_fights(event)  # identity is validated before anything else
    stats: dict = {
        "latest_persistence_ok": False,
        "snapshot_persistence_ok": False,
    }
    if not fights:
        # An announced skeleton card: nothing can be joined or stored, so
        # spend no Supabase read and no shared SharpAPI capacity on it.
        return {
            "fights": 0, "fights_started": 0, "fights_written": 0,
            "bindings": 0, "unmatched": 0, "ambiguous": 0,
            "no_match_markets": [], "rows": {}, "requests_made": 0,
            "pregame_snapshot_candidates": 0, "snapshots_inserted": 0,
            "snapshots_deduplicated": 0,
            "snapshot_persistence_ok": True, "latest_persistence_ok": True,
        }
    persisted = _load_persisted_strict(card_date)

    sweep = SharpSweep()
    rows_by_market = {}
    for market in MARKETS:
        # A SharpOddsError aborts the capture: a bundle assembled from the
        # markets that happened to succeed would overwrite good data.
        rows_by_market[market] = await fetch_sharp_odds(LEAGUE, market, sweep=sweep)
    no_match = [market for market, rows in rows_by_market.items() if not rows]

    report = match_card(fights, build_pools(rows_by_market, fights))

    latest: dict[str, dict] = {}
    snapshots: list[dict] = []
    started = 0
    for fight in fights:
        if fight.started(captured_at):
            # Left the pregame feed as expected: the persisted value is the
            # final pregame price. Never stale, never overwritten.
            started += 1
            continue
        prior = persisted.get((card_date.isoformat(), fight.fight_id)) or {}
        books: dict = {}
        for book in BOOKS:
            binding = report.bindings.get((fight.fight_id, book))
            if binding is None and not prior.get(book):
                continue
            bundle = assemble_book(
                fight,
                prior.get(book),
                fresh_markets(binding) if binding else {},
                event_uuid=binding.sharp.event_uuid if binding else None,
                captured_at=captured_at,
            )
            books[book] = bundle
            if _has_price(bundle):
                snapshots.append({
                    "sport": "ufc",
                    "provider": "sharpapi",
                    "bookmaker": book,
                    "provider_event_id": bundle.get("sharpEventUuid"),
                    "game_date": card_date.isoformat(),
                    "matchup_key": fight.fight_id,
                    "starts_at": fight.starts_at.astimezone(UTC).isoformat() if fight.starts_at else None,
                    "captured_at": captured_at.isoformat(),
                    "odds_data": snapshot_data(bundle),
                })
        if books:
            latest[fight.fight_id] = books

    # One upsert carrying every fully assembled fight; upserts replace
    # odds_data whole, so per-market writes would erase each other.
    latest_ok = save_persisted_odds(latest, game_date=card_date, table_name=TABLE_NAME)
    snapshot_stats: dict = {}
    save_odds_snapshots(snapshots, stats=snapshot_stats)

    stats.update({
        "fights": len(fights),
        "fights_started": started,
        "fights_written": len(latest),
        "bindings": len(report.bindings),
        "unmatched": len(report.unmatched),
        "ambiguous": len(report.ambiguous),
        "no_match_markets": no_match,
        "rows": {market: len(rows) for market, rows in rows_by_market.items()},
        "requests_made": sweep.requests_made,
        "pregame_snapshot_candidates": len(snapshots),
        "snapshots_inserted": snapshot_stats.get("inserted", 0),
        "snapshots_deduplicated": snapshot_stats.get("deduplicated", 0),
        "snapshot_persistence_ok": snapshot_stats.get("persistence_ok", False),
        "latest_persistence_ok": latest_ok,
    })
    return stats


@router.post("/cron/fetch-odds", dependencies=[Depends(require_cron_auth)])
async def cron_fetch_odds():
    # Collection anchors on the calendar date; the 6am rollover is display-only.
    today = datetime.now(ET).date()
    try:
        found = await next_card(today)
        if found is None:
            return {"success": True, "card": None, "message": "No upcoming UFC card on the ESPN calendar"}
        event, card_date = found
        stats = await collect_card(event, card_date)
        cache.set_ttl("assembled", 0)
        return {
            "success": bool(stats.get("latest_persistence_ok")),
            "card": {"id": str(event.get("id")), "name": event.get("name"), "date": card_date.isoformat()},
            **stats,
        }
    except SharpOddsError as exc:
        logger.error("UFC SharpAPI sweep incomplete; nothing written: %s", exc)
        return {"success": False, "error": f"SharpAPI sweep incomplete: {exc}"}
    except Exception as exc:
        logger.error("UFC odds collection failed: %s", exc, exc_info=True)
        return {"success": False, "error": str(exc)}


# --- display ----------------------------------------------------------------


def _moneyline_qualifies(bundle: dict | None) -> bool:
    if not bundle:
        return False
    stale = ((bundle.get("observed") or {}).get("moneyline") or {}).get("isStale")
    return (
        bundle.get("fighterAMoneyline") is not None
        and bundle.get("fighterBMoneyline") is not None
        and not stale
    )


def select_book(books: dict | None) -> str | None:
    """First book with a complete, current moneyline pair; it prices everything shown."""
    for book in BOOKS:
        if _moneyline_qualifies((books or {}).get(book)):
            return book
    return None


def _market_meta(bundle: dict, market: str) -> dict:
    observed = (bundle.get("observed") or {}).get(market) or {}
    return {"isStale": bool(observed.get("isStale")), "lastObservedAt": observed.get("lastObservedAt")}


def _game_status(fight: Fight) -> tuple[int, str]:
    if fight.state == "in":
        return 2, fight.status_detail or "Live"
    if fight.state == "post":
        return 3, "Final"
    return 1, format_et_time(fight.starts_at.isoformat()) if fight.starts_at else ""


def _winners(fight: Fight, game_status: int) -> list[bool | None]:
    """A result only when exactly one fighter won; a draw or no contest is null."""
    flags = [fighter["winner"] is True for fighter in fight.fighters]
    if game_status != 3 or sum(flags) != 1:
        return [None, None]
    return flags


def fight_payload(fight: Fight, books: dict | None) -> dict:
    book = select_book(books)
    bundle = (books or {}).get(book) if book else None
    game_status, status = _game_status(fight)
    winners = _winners(fight, game_status)
    fighters = []
    for index, fighter in enumerate(fight.fighters):
        letter = "A" if index == 0 else "B"
        fighters.append({
            "name": fighter["name"],
            "espnAthleteId": fighter["id"],
            "moneyline": bundle.get(f"fighter{letter}Moneyline") if bundle else None,
            "winProbability": bundle.get(f"fighter{letter}WinProbability") if bundle else None,
            "winner": winners[index],
        })
    total = go = None
    if bundle and bundle.get("totalRounds") is not None:
        total = {
            "line": bundle["totalRounds"],
            "overOdds": bundle.get("overOdds"),
            "underOdds": bundle.get("underOdds"),
            **_market_meta(bundle, "total_rounds"),
        }
    if bundle and (bundle.get("goesDistanceYes") is not None or bundle.get("goesDistanceNo") is not None):
        go = {
            "yesOdds": bundle.get("goesDistanceYes"),
            "noOdds": bundle.get("goesDistanceNo"),
            **_market_meta(bundle, "go_the_distance"),
        }
    return {
        "fightId": fight.fight_id,
        "order": fight.order,
        "weightClass": fight.weight_class,
        "status": status,
        "gameStatus": game_status,
        "bookmaker": book,
        "fighters": fighters,
        "totalRounds": total,
        "goesTheDistance": go,
    }


async def _assemble_card() -> dict | None:
    found = await next_card(slate_date_et())
    if found is None:
        return None
    event, card_date = found
    fights = card_fights(event)
    # A skeleton card needs no odds, so a Supabase outage can't take it down.
    persisted: dict = {}
    if fights:
        try:
            persisted = _load_persisted_strict(card_date)
        except Exception as exc:
            logger.warning("UFC persisted odds load failed: %s", exc)
            raise HTTPException(status_code=503, detail="UFC odds feed unavailable") from exc

    main_event = fights[0] if fights else None
    return {
        "cardId": str(event.get("id")),
        "name": event.get("name") or "",
        "date": event.get("date") or "",
        "venue": ((event.get("venues") or [{}])[0]).get("fullName") or "TBD",
        "broadcast": main_event.broadcast if main_event else "",
        "fights": [
            fight_payload(fight, persisted.get((card_date.isoformat(), fight.fight_id)))
            for fight in fights
        ],
    }


@router.get("/next-card")
async def get_next_card():
    cached = cache.get("assembled")
    if cached:
        return cached
    try:
        card = await _assemble_card()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("UFC /next-card failed: %s", exc, exc_info=True)
        stale = cache.get_stale("assembled")
        if stale:
            return stale
        raise HTTPException(status_code=502, detail="UFC card unavailable") from exc
    if card is None:
        raise HTTPException(status_code=404, detail="No upcoming UFC card")
    has_live = any(fight["gameStatus"] == 2 for fight in card["fights"])
    cache.set("assembled", card, ttl=CACHE_TTL_LIVE if has_live else CACHE_TTL_DEFAULT)
    return card
