"""Price a PrizePicks tennis board against one SharpAPI odds snapshot.

One match distribution per book event; Fantasy Score, Total Games and both players' Games Won all
read from it, so the three stats can't disagree about how the match goes.
"""
from __future__ import annotations

import collections
import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from . import model
from .config import DEFAULT_CONFIG, ModelConfig

PRICED_STATS = ("Fantasy Score", "Total Games", "Total Games Won")
_SLAMS = ("australian_open", "french_open", "roland_garros", "wimbledon", "us_open")


def norm(name: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower())


def _name_match(a: str, b: str) -> bool:
    """Books and PrizePicks spell names differently ("Daniel Merida" vs "Daniel Merida Aguilar")."""
    if not a or not b:
        return False
    return a == b or a.startswith(b) or b.startswith(a) or a.endswith(b) or b.endswith(a)


def _team_name(value) -> str | None:
    return (value.get("name") or value.get("display_name")) if isinstance(value, dict) else value


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass
class Event:
    event_id: str
    league: str
    names: tuple[str, str]  # (home, away) as the book spells them
    start: datetime | None = None
    competition_id: str = ""
    ml: dict[str, dict[str, float]] = field(default_factory=lambda: collections.defaultdict(dict))
    ladder: dict[float, dict[str, float]] = field(default_factory=lambda: collections.defaultdict(dict))
    main_total: float | None = None
    spreads: dict[str, tuple[float, float]] = field(default_factory=dict)
    aces_fd: dict[str, tuple[float, float]] = field(default_factory=dict)
    aces_dk: dict[str, tuple[float, float]] = field(default_factory=dict)
    games_won: dict[str, dict[str, float]] = field(default_factory=lambda: collections.defaultdict(dict))
    # Expected aces for the match per player, typed in with hand-entered lines.
    aces_manual: dict[str, float] = field(default_factory=dict)
    manual: bool = False

    def side_of(self, name: str) -> int | None:
        """0 for home, 1 for away; None when the name fits both participants or neither."""
        home, away = norm(self.names[0]), norm(self.names[1])
        if name == home or name == away:
            return 0 if name == home else 1
        hits = [_name_match(name, home), _name_match(name, away)]
        return hits.index(True) if sum(hits) == 1 else None

    def by_side(self, table: dict, side: int):
        """The entry of a per-player table belonging to one side of this event."""
        return next((value for key, value in table.items() if self.side_of(key) == side), None)

    def total_pair(self) -> tuple[float, float, float] | None:
        """(line, over, under) for the main total, if both sides are priced."""
        if self.main_total is None:
            return None
        sides = self.ladder.get(self.main_total, {})
        return (self.main_total, sides["over"], sides["under"]) if {"over", "under"} <= sides.keys() else None


def build_events(snapshot_rows: list[dict], cfg: ModelConfig = DEFAULT_CONFIG) -> dict[str, Event]:
    events: dict[str, Event] = {}
    for x in snapshot_rows:
        home, away = _team_name(x.get("home") or x.get("home_team")), _team_name(x.get("away") or x.get("away_team"))
        event_id = x.get("event_id")
        if not home or not away or not event_id or "/" in home:
            continue
        e = events.get(event_id)
        if e is None:
            e = events[event_id] = Event(event_id, x.get("league") or "", (home, away))
        e.start = e.start or _parse_time(x.get("event_start_time"))
        e.competition_id = e.competition_id or str((x.get("competition_ref") or {}).get("id") or "")
        market, book, odds, line = x.get("market_type"), x.get("sportsbook"), x.get("odds_american"), x.get("line")
        if odds is None:
            continue
        if market == "moneyline":
            e.ml[book][norm(x["selection"])] = odds
        # DK mixes a set-sized 11.5 market into total_games, usually as an alternate but flagged
        # main for Michelsen-Hanfmann 11-07.
        elif market == "total_games" and book == "draftkings" and line is not None and line >= cfg.min_total_line:
            e.ladder[line][x["selection_type"]] = odds
            if x.get("is_main_line"):
                e.main_total = line
        elif market == "game_spread" and book == "draftkings" and x.get("is_main_line") and line is not None:
            e.spreads[norm(x["selection"])] = (line, odds)
        elif market == "player_aces" and book in ("fanduel", "draftkings") and x.get("selection_type") == "over" and line is not None:
            # FD flags tail rungs as main (Medvedev 14.5 at +850 with 8.5 at -135 on one ladder), so
            # keep the rung closest to even money; it pins the mean best.
            table = e.aces_fd if book == "fanduel" else e.aces_dk
            key = norm(x.get("player_name") or x["selection"])
            current = table.get(key)
            if current is None or abs(model.implied(odds) - 0.5) < abs(model.implied(current[1]) - 0.5):
                table[key] = (line, odds)
        elif market == "player_games_won" and book == "draftkings" and x.get("is_main_line") and line is not None:
            entry = e.games_won[norm(x.get("player_name") or x["selection"])]
            entry["line"] = line
            entry[x["selection_type"]] = odds
    for e in events.values():
        if e.total_pair() is None:
            # No usable main (DK flagged only its stray set market): the most even full-match rung.
            two_sided = {l: model.devig(s["over"], s["under"]) for l, s in e.ladder.items() if {"over", "under"} <= s.keys()}
            e.main_total = min(two_sided, key=lambda l: abs(two_sided[l] - 0.5)) if two_sided else None
    return events


# Assumed where a hand-entered total comes without prices.
_EVEN_PRICE = -110.0


def manual_event(entry: dict) -> Event:
    """A book event built from lines typed in for one PrizePicks match (board.ManualLine)."""
    player, opponent = entry["player"], entry["opponent"]
    e = Event(f"manual:{entry['game_id']}", entry["tour"], (player, opponent), manual=True)
    e.ml["manual"] = {norm(player): entry["ml_player"], norm(opponent): entry["ml_opponent"]}
    if entry.get("total_line") is not None:
        e.main_total = entry["total_line"]
        e.ladder[e.main_total] = {
            "over": entry.get("total_over") or _EVEN_PRICE, "under": entry.get("total_under") or _EVEN_PRICE,
        }
    for name, aces in ((player, entry.get("aces_player")), (opponent, entry.get("aces_opponent"))):
        if aces is not None:
            e.aces_manual[norm(name)] = aces
    return e


def match_event(events: dict[str, Event], player: str, opponent: str, start: datetime | None, cfg: ModelConfig) -> tuple[Event | None, str | None]:
    """(event, None), or (None, reason). Never takes the first of several candidates."""
    p, o = norm(player), norm(opponent)
    window = timedelta(hours=cfg.match_window_hours)
    exact, loose = [], []
    for e in events.values():
        if start and e.start and abs(e.start - start) > window:
            continue
        home, away = norm(e.names[0]), norm(e.names[1])
        if {p, o} == {home, away}:
            exact.append(e)
        elif (_name_match(p, home) and _name_match(o, away)) or (_name_match(p, away) and _name_match(o, home)):
            loose.append(e)
    candidates = exact or loose
    if not candidates:
        return None, "no_book_line"
    if len(candidates) > 1:
        return None, "ambiguous_match"
    return candidates[0], None


@dataclass
class _EventModel:
    fit: model.Fit | None = None
    p_home: float | None = None
    unpriced_reason: str | None = None
    fit_miss: bool = False


def _moneyline(e: Event) -> float | None:
    def price(prices: dict[str, float], name: str) -> float | None:
        n = norm(name)
        return prices.get(n) or next((v for k, v in prices.items() if k.startswith(n) or n.startswith(k)), None)

    wins = []
    for prices in e.ml.values():
        ph, pa = price(prices, e.names[0]), price(prices, e.names[1])
        if ph is not None and pa is not None:
            wins.append(model.devig(ph, pa))
    return sum(wins) / len(wins) if wins else None


def _is_best_of_five(e: Event, cfg: ModelConfig) -> bool:
    slam = e.league == "atp" and any(name in e.competition_id.lower() for name in _SLAMS)
    return slam or (e.main_total is not None and e.main_total >= cfg.best_of_five_total)


def _accept(fit: model.Fit, p_home: float, total: tuple[float, float, float] | None, cfg: ModelConfig) -> bool:
    if not all(0 < p < 1 for p in fit.serves):
        return False
    if abs(sum(fit.dist.values()) - 1) > 1e-6:
        return False
    if abs(model.p_win(fit.dist) - p_home) > cfg.fit_win_tol:
        return False
    if total and fit.label != "ml_only":
        line, over, under = total
        if abs(model.p_total_over(fit.dist, line) - model.devig(over, under)) > cfg.fit_over_tol:
            return False
    return True


def model_event(e: Event, cfg: ModelConfig) -> _EventModel:
    p_home = _moneyline(e)
    if p_home is None:
        return _EventModel(unpriced_reason="no_book_line")
    if _is_best_of_five(e, cfg):
        return _EventModel(p_home=p_home, unpriced_reason="unsupported_format")
    total = e.total_pair()
    if total:
        line, over, under = total
        fit = model.fit_dist(p_home, model.devig(over, under), line, cfg.mix_c[e.league])
    else:
        fit = model.fit_ml_only(p_home, cfg.mix_c[e.league], cfg.ml_only_s[e.league])
    return _EventModel(fit=fit, p_home=p_home, fit_miss=not _accept(fit, p_home, total, cfg))


def _over_under(weights) -> tuple[float, float, float]:
    """(P(over), P(under), P(push)) from (value - line, weight) pairs; the first two exclude pushes."""
    over = push = 0.0
    for diff, w in weights:
        if diff > 0:
            over += w
        elif diff == 0:
            push += w
    return over / (1 - push), (1 - over - push) / (1 - push), push


def _lean(p_over: float, p_under: float) -> str | None:
    return "over" if p_over > 0.5 else "under" if p_under > 0.5 else None


def _fantasy_score(e: Event, em: _EventModel, side: int, pp_aces: dict[str, list[float]], cfg: ModelConfig) -> dict:
    """Everything about one player's FS that doesn't depend on the PrizePicks line."""
    d = em.fit.dist
    ladder = next(((book, label) for book, label in ((e.aces_fd, "FD"), (e.aces_dk, "DK")) if e.by_side(book, side)), None)
    assumed = False
    manual_aces = e.by_side(e.aces_manual, side)
    if manual_aces is not None:
        # A typed-in expectation (recent results, say) is a mean, not a line to fit a median to.
        service_games = sum(w * (ga + gb) for (ga, gb, _, _), w in d.items()) / 2
        ace_line, ace_r = manual_aces, manual_aces / service_games
        aces_src = f"manual {manual_aces:g}"
    elif ladder:
        ace_line, ace_odds = e.by_side(ladder[0], side)
        ace_r = model.fit_ace_rate(d, ace_line, model.implied(ace_odds) / cfg.one_sided_margin, cfg.ace_k)
        aces_src = f"{ladder[1]} {ace_line:g} {ace_odds:g}"
    elif pp_aces["goblin"] and pp_aces["demon"]:
        # PrizePicks' own ladder: the median sits between the top goblin and the bottom demon.
        ace_line = math.floor((max(pp_aces["goblin"]) + min(pp_aces["demon"])) / 2) + 0.5
        ace_r = model.fit_ace_rate(d, ace_line, 0.5, cfg.ace_k)
        aces_src = f"PP ladder ~{ace_line:g}"
    elif pp_aces["standard"]:
        ace_line = pp_aces["standard"][0]
        ace_r = model.fit_ace_rate(d, ace_line, 0.5, cfg.ace_k)
        aces_src = f"PP std {ace_line:g}"
    else:
        ace_line, ace_r, assumed = None, round(cfg.ace_default[e.league], 3), True
        aces_src = f"assumed {ace_r:g}"
    fs = model.fs_dist(d, side, ace_r, cfg.df_default[e.league], cfg.ace_k)
    cum, median = 0.0, None
    for k in sorted(fs):
        cum += fs[k]
        if cum >= 0.5:
            median = k
            break
    home_spread = e.spreads.get(norm(e.names[0]))
    spread = (home_spread[0] if side == 0 else -home_spread[0]) if home_spread else None
    rule_fs = None
    if spread is not None:
        exp_games = sum(w * (ga + gb) for (ga, gb, _, _), w in d.items())
        aces = ace_line if ace_line else ace_r * exp_games / 2
        p_me = em.p_home if side == 0 else 1 - em.p_home
        favorite = spread < 0 or (spread == 0 and p_me >= 0.5)
        rule_fs = (13 + abs(spread) if favorite else 7 - spread) + 0.5 * aces
    return {
        "dist": fs, "aces_src": aces_src, "assumed_aces": assumed, "spread": spread,
        "model_mean": sum(k * w for k, w in fs.items()), "model_median": median, "rule_fs": rule_fs,
    }


def _ladder_check(e: Event, line: float) -> dict:
    """De-vigged DK P(over) at the PP line, or at the two neighbouring half-lines."""
    ladder = {l: model.devig(s["over"], s["under"]) for l, s in e.ladder.items() if {"over", "under"} <= s.keys()}
    if line in ladder:
        return {"dk_p_over": round(ladder[line], 3)}
    lo, hi = ladder.get(line - 0.5), ladder.get(line + 0.5)
    return {"dk_p_over_lo": round(lo, 3), "dk_p_over_hi": round(hi, 3)} if lo is not None and hi is not None else {}


def price_board(
    rows: list[dict],
    snapshot_rows: list[dict],
    *,
    now: datetime,
    cfg: ModelConfig = DEFAULT_CONFIG,
    fit_cache: dict[str, _EventModel] | None = None,
    manual: list[dict] | None = None,
) -> list[dict]:
    """One result per PrizePicks line. `fit_cache` (event_id -> fit) may be reused across boards
    priced on the same snapshot. `manual` holds hand-entered lines by PrizePicks game; a match
    that has them is priced on them and not on the snapshot."""
    events = build_events(snapshot_rows, cfg)
    fit_cache = fit_cache if fit_cache is not None else {}
    # Never in fit_cache: the same game can carry different typed-in lines from run to run.
    manual_events = {entry["game_id"]: manual_event(entry) for entry in manual or []}
    manual_fits: dict[str, _EventModel] = {}
    fs_cache: dict[tuple[str, int], dict] = {}
    pp_aces: dict[tuple[str, str], dict[str, list[float]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in rows:
        if row["stat"] == "Aces":
            pp_aces[(row["game_id"], norm(row["player"]))][row["odds_type"]].append(row["line"])

    results, seen_totals = [], set()
    for row in rows:
        stat = row["stat"]
        if stat not in PRICED_STATS:
            continue
        if stat == "Total Games":
            # One line per match, but it can arrive under either player.
            key = (row["game_id"], row["odds_type"], row["line"])
            if key in seen_totals:
                continue
            seen_totals.add(key)
        line, start = row["line"], _parse_time(row["start_time"])
        out = {
            "stat": stat, "player": row["player"], "opponent": row["opponent"], "start_time": row["start_time"],
            "odds_type": row["odds_type"], "line": line, "projection_id": row["projection_id"],
            "game_id": row["game_id"], "event_id": "", "flags": [], "lean": None, "unpriced_reason": None,
        }
        results.append(out)
        if start and start <= now:
            out["flags"].append("started")

        if row["game_id"] in manual_events:
            e, reason = manual_events[row["game_id"]], None
            out["flags"].append("manual_odds")
        else:
            e, reason = match_event(events, row["player"], row["opponent"], start, cfg)
        side = e.side_of(norm(row["player"])) if e else None
        if e and side is None:
            reason = "ambiguous_match"
        if reason:
            out["unpriced_reason"] = reason
            continue
        out["event_id"], out["tour"] = e.event_id, e.league
        fits = manual_fits if e.manual else fit_cache
        if e.event_id not in fits:
            fits[e.event_id] = model_event(e, cfg)
        em = fits[e.event_id]
        if em.fit is None or (stat == "Fantasy Score" and em.fit.label == "ml_only"):
            # FS needs the total as well as the moneyline.
            out["unpriced_reason"] = em.unpriced_reason or "no_book_line"
            continue
        d = em.fit.dist
        out.update({
            "p_win": round(em.p_home if side == 0 else 1 - em.p_home, 3),
            "fav_ml": min(v for prices in e.ml.values() for v in prices.values()),
            "total": e.main_total, "fit": em.fit.label,
        })

        if stat == "Fantasy Score":
            if (e.event_id, side) not in fs_cache:
                fs_cache[(e.event_id, side)] = _fantasy_score(e, em, side, pp_aces[(row["game_id"], norm(row["player"]))], cfg)
            fs = fs_cache[(e.event_id, side)]
            dist = fs["dist"]
            p_over, p_under, push = _over_under((k - line, w) for k, w in dist.items())
            # Reprice the leaning side with the line moved against it, where the measured bias
            # works against that lean (WTA unders, ATP overs).
            bias = cfg.bias_shift[e.league]
            if p_under > p_over:
                shifted = line - max(bias, 0)
                p_side_bias = sum(w for k, w in dist.items() if k < shifted) / (1 - dist.get(shifted, 0.0))
            else:
                shifted = line + max(-bias, 0)
                p_side_bias = sum(w for k, w in dist.items() if k > shifted) / (1 - dist.get(shifted, 0.0))
            out.update({
                "spread": fs["spread"], "aces_src": fs["aces_src"],
                "model_mean": round(fs["model_mean"], 1), "model_median": fs["model_median"],
                "rule_fs": round(fs["rule_fs"], 1) if fs["rule_fs"] is not None else None,
                "p_side_bias": round(p_side_bias, 3),
            })
            if fs["assumed_aces"]:
                out["flags"].append("assumed_aces")
            if p_side_bias < 0.5:
                out["flags"].append("fails_bias_check")
        elif stat == "Total Games":
            p_over, p_under, push = _over_under((ga + gb - line, w) for (ga, gb, _, _), w in d.items())
            out["model_mean"] = round(sum(w * (ga + gb) for (ga, gb, _, _), w in d.items()), 1)
            if not e.manual:
                out.update(_ladder_check(e, line))
            if out["fav_ml"] <= cfg.blowout_ml:
                out["flags"].append("blowout")
        else:
            p_over, p_under, push = _over_under((k[side] - line, w) for k, w in d.items())
            out["model_mean"] = round(sum(w * k[side] for k, w in d.items()), 1)
            book = e.by_side(e.games_won, side)
            if book and {"line", "over", "under"} <= book.keys():
                out.update({
                    "dk_gw_line": book["line"], "dk_gw_over": book["over"], "dk_gw_under": book["under"],
                    "dk_gw_p_over": round(model.devig(book["over"], book["under"]), 3),
                })
                # The strategy notes' rule: follow a juiced DK side when PP's line is no worse.
                if book["over"] <= cfg.juice_ml and line <= book["line"]:
                    out["flags"].append("juice_over")
                if book["under"] <= cfg.juice_ml and line >= book["line"]:
                    out["flags"].append("juice_under")

        out.update({"p_over": round(p_over, 3), "p_under": round(p_under, 3), "push": round(push, 3)})
        if em.fit.label == "ml_only":
            out["flags"].append("ml_only")
        if em.fit_miss:
            out["flags"].append("fit_miss")
        # An ml_only total isn't market-pinned and a missed fit can't be trusted: no lean on either.
        if em.fit.label != "ml_only" and not em.fit_miss:
            out["lean"] = _lean(p_over, p_under)
        # Goblins and demons can only be played over.
        if row["odds_type"] != "standard" and (out["lean"] == "under" or "juice_under" in out["flags"]):
            out["flags"].append("over_only")
    return results
