"""Serve-point match model: exact games and sets distribution for a best-of-3 match.

Ported from sports/projections/scripts/tennis_fs.py and tennis_games.py. Each match is fit to the
de-vigged moneyline and the DK main total-games pair. Pure functions; no I/O.
"""
from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from functools import lru_cache

# {(games A, games B, sets A, sets B): probability}
Dist = dict[tuple[int, int, int, int], float]

# 3-point Gauss-Hermite grid for the strength mixture.
MIX_Z = [(-(3 ** 0.5), 1 / 6), (0.0, 2 / 3), (3 ** 0.5, 1 / 6)]


@dataclass(frozen=True)
class Fit:
    dist: Dist
    label: str  # "iid", "mix s=<x>" or "ml_only"
    serves: tuple[float, ...]  # every serve probability the fit used


def implied(american: float) -> float:
    return 100 / (american + 100) if american > 0 else -american / (-american + 100)


def devig(over: float, under: float) -> float:
    po, pu = implied(over), implied(under)
    return po / (po + pu)


def hold(p: float) -> float:
    q = 1 - p
    return p**4 * (1 + 4 * q + 10 * q * q) + 20 * p**3 * q**3 * p * p / (1 - 2 * p * q)


def tiebreak(pa: float, pb: float) -> float:
    """P(A wins a 7-point tiebreak), A serving the first point."""

    @lru_cache(None)
    def f(a: int, b: int) -> float:
        if a >= 7 and a - b >= 2:
            return 1.0
        if b >= 7 and b - a >= 2:
            return 0.0
        if a >= 6 and a == b:
            x, y = pa * (1 - pb), (1 - pa) * pb
            return x / (x + y)
        a_serves = (a + b) % 4 in (0, 3)
        p = pa if a_serves else 1 - pb
        return p * f(a + 1, b) + (1 - p) * f(a, b + 1)

    return f(0, 0)


def set_dist(pa: float, pb: float, a_first: bool) -> dict[tuple[int, int, bool], float]:
    """{(games A, games B, A serves first next set): prob} for one set."""
    ha, hb = hold(pa), hold(pb)
    tb_a = 0.5 * (tiebreak(pa, pb) + (1 - tiebreak(pb, pa)))
    res: dict[tuple[int, int, bool], float] = collections.defaultdict(float)
    states = {(0, 0): 1.0}
    while states:
        nxt: dict[tuple[int, int], float] = collections.defaultdict(float)
        for (a, b), w in states.items():
            if (a == 6 and b <= 4) or (b == 6 and a <= 4) or a == 7 or b == 7:
                res[(a, b, (a + b) % 2 == 0 if a_first else (a + b) % 2 == 1)] += w
                continue
            if a == 6 and b == 6:
                # 13 games played, so the first server flips.
                res[(7, 6, not a_first)] += w * tb_a
                res[(6, 7, not a_first)] += w * (1 - tb_a)
                continue
            a_serves = ((a + b) % 2 == 0) == a_first
            p = ha if a_serves else 1 - hb
            nxt[(a + 1, b)] += w * p
            nxt[(a, b + 1)] += w * (1 - p)
        states = nxt
    return res


def match_dist(pa: float, pb: float) -> Dist:
    """Best of 3, coin-flip first server."""
    res: Dist = collections.defaultdict(float)
    cache: dict[bool, dict] = {}

    def sd(first: bool) -> dict:
        if first not in cache:
            cache[first] = set_dist(pa, pb, first)
        return cache[first]

    def rec(ga: int, gb: int, sa: int, sb: int, first: bool, w: float) -> None:
        if sa == 2 or sb == 2:
            res[(ga, gb, sa, sb)] += w
            return
        for (a, b, nf), p in sd(first).items():
            rec(ga + a, gb + b, sa + (a > b), sb + (b > a), nf, w * p)

    rec(0, 0, 0, 0, True, 0.5)
    rec(0, 0, 0, 0, False, 0.5)
    return res


def p_win(d: Dist) -> float:
    return sum(w for (_, _, sa, _), w in d.items() if sa == 2)


def p_total_over(d: Dist, line: float) -> float:
    return sum(w for (ga, gb, _, _), w in d.items() if ga + gb > line)


def p_cover(d: Dist, spread: float) -> float:
    return sum(w for (ga, gb, _, _), w in d.items() if ga - gb + spread > 0)


def fit(target_win: float, target_over: float, total_line: float) -> tuple[float, float]:
    """Fixed serve probabilities (pa, pb) hitting the moneyline and, where reachable, the total."""

    def solve_d(c: float) -> float:
        lo, hi = -0.4, 0.4
        for _ in range(22):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if p_win(match_dist(c + mid / 2, c - mid / 2)) < target_win else (lo, mid)
        return (lo + hi) / 2

    lo, hi = 0.45, 0.80
    for _ in range(16):
        c = (lo + hi) / 2
        dlt = solve_d(c)
        ov = p_total_over(match_dist(c + dlt / 2, c - dlt / 2), total_line)
        lo, hi = (c, hi) if ov < target_over else (lo, c)
    c = (lo + hi) / 2
    dlt = solve_d(c)
    return c + dlt / 2, c - dlt / 2


def _mix_serves(c: float, dlt: float, s: float) -> tuple[float, ...]:
    return tuple(p for z, _ in MIX_Z for p in (c + (dlt + s * z) / 2, c - (dlt + s * z) / 2))


def mix_dist(c: float, dlt: float, s: float) -> Dist:
    """The serve gap varies match to match: dlt + s*z over the Gauss-Hermite grid."""
    out: Dist = collections.defaultdict(float)
    for z, wz in MIX_Z:
        for k, w in match_dist(c + (dlt + s * z) / 2, c - (dlt + s * z) / 2).items():
            out[k] += wz * w
    return out


def _solve_mix_gap(c: float, s: float, target_win: float) -> float:
    lo, hi = -0.6, 0.6
    for _ in range(20):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if p_win(mix_dist(c, mid, s)) < target_win else (lo, mid)
    return (lo + hi) / 2


def fit_dist(target_win: float, target_over: float, total_line: float, mix_c: float) -> Fit:
    """iid when it hits the total, else the strength mixture.

    The iid model can't reach WTA totals (a 71% favorite with DK total 20.5 bottoms out at
    P(over) ~0.63): real matches are more lopsided than fixed serve probabilities allow.
    """
    pa, pb = fit(target_win, target_over, total_line)
    d = match_dist(pa, pb)
    if abs(p_total_over(d, total_line) - target_over) < 0.01:
        return Fit(d, "iid", (pa, pb))
    lo, hi = 0.0, 0.35
    for _ in range(14):
        s = (lo + hi) / 2
        ov = p_total_over(mix_dist(mix_c, _solve_mix_gap(mix_c, s, target_win), s), total_line)
        lo, hi = (s, hi) if ov > target_over else (lo, s)
    s = (lo + hi) / 2
    dlt = _solve_mix_gap(mix_c, s, target_win)
    return Fit(mix_dist(mix_c, dlt, s), f"mix s={s:.3f}", _mix_serves(mix_c, dlt, s))


def fit_ml_only(target_win: float, mix_c: float, s: float) -> Fit:
    """No DK total: fit the serve gap to the moneyline at a tour-average serve level and spread."""
    dlt = _solve_mix_gap(mix_c, s, target_win)
    return Fit(mix_dist(mix_c, dlt, s), "ml_only", _mix_serves(mix_c, dlt, s))


def poisson(lam: float, kmax: int = 60) -> list[float]:
    out = [math.exp(-lam)]
    for k in range(1, kmax):
        out.append(out[-1] * lam / k)
    return out


def negbin(mu: float, k: float, kmax: int = 80) -> list[float]:
    q = mu / (k + mu)
    out = [(k / (k + mu)) ** k]
    for n in range(kmax - 1):
        out.append(out[-1] * (n + k) / (n + 1) * q)
    return out


def fit_ace_rate(d: Dist, line: float, target: float, ace_k: float) -> float:
    """Aces per service game that puts P(aces > line) at target."""
    lo, hi = 0.01, 2.5
    cut = int(math.floor(line)) + 1
    for _ in range(40):
        r = (lo + hi) / 2
        p = 0.0
        for (ga, gb, _, _), w in d.items():
            p += w * (1 - sum(negbin(r * (ga + gb) / 2, ace_k)[:cut]))
        lo, hi = (r, hi) if p < target else (lo, r)
    return (lo + hi) / 2


def fs_dist(d: Dist, side: int, ace_r: float, df_r: float, ace_k: float) -> dict[float, float]:
    """FS = 10 + game diff + 3 * set diff + 0.5 * aces - 0.5 * double faults."""
    out: dict[float, float] = collections.defaultdict(float)
    for (ga, gb, sa, sb), w in d.items():
        mine, theirs = (ga, gb) if side == 0 else (gb, ga)
        s_m, s_t = (sa, sb) if side == 0 else (sb, sa)
        base = 10 + mine - theirs + 3 * (s_m - s_t)
        service_games = (ga + gb) / 2
        aces, dfs = negbin(ace_r * service_games, ace_k), poisson(df_r * service_games, 25)
        for i, x in enumerate(aces):
            if x < 1e-9:
                continue
            for j, y in enumerate(dfs):
                if y < 1e-9:
                    continue
                out[base + 0.5 * (i - j)] += w * x * y
    return out
