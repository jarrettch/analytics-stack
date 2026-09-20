# Odds Providers

_Created: 2026-09-19_

Four odds providers are in use or available. They are **not interchangeable**,
and none of them dominates the others. This document records what each one is
uniquely for, so the choice for a new sport is a lookup rather than a
rediscovery.

## The portfolio

| Provider | Books | Rationing | Sharp reference? | Helper |
|---|---|---|---|---|
| SportsGameOdds | BetOnline + de-vigged `fairOdds` | **2500 entities/month**, billed per event returned | Yes | `fetch_sgo_odds` |
| The Odds API | Pinnacle, BetOnline, Betfair Exchange, Matchbook (`eu`); DK/FD/MGM (`us`) | **500 credits/month** — one request costs markets × regions, so a request is **not** one credit | **Yes — the only sharp source not billed per event** | `fetch_odds_api_odds` |
| SharpAPI | DraftKings, FanDuel (free tier) | 12 req/min, **no monthly cap** | No — recreational only | `fetch_sharp_odds` (planned) |
| ESPN | DraftKings | **None** | No — recreational only | `fetch_espn_odds` (planned) |

## What each is uniquely for

### SportsGameOdds — the default for team sports

The only provider giving **de-vigged `fairOdds`**, which
`calculate_win_probability_from_odds` prefers over the raw moneyline pair. Every
sport whose win-probability path depends on that stays on SGO.

Its constraint is the tightest and the least forgiving: one entity per **event
returned**, so an unscoped query bills for games you never wanted. See
`odds_fix.md` — an unbounded `limit: 50` with no date window burned 99% of a
monthly cap in seven days.

Always scope `/v2/events` with `startsAfter` + `startsBefore`.

### The Odds API — the sharp reference

**The only free access to Pinnacle and the betting exchanges.** SharpAPI's free
tier and ESPN both carry recreational books only; SGO's free tier carries
BetOnline but is entity-rationed.

**Tennis is the only current Pinnacle consumer. WNBA is not**, despite
appearances.

`ODDS_API_BOOKS` leads with `pinnacle`, but `fetch_odds_api_odds` hardcodes
`"regions": "us"` (`utils.py:743`) — and Pinnacle is an `eu`-region book. The
preference chain therefore **cannot select Pinnacle on the WNBA path**; it falls
straight through to DraftKings and the other US recreational books. The
preference is not wrong, it is just unreachable from where it is called.

So today:

- **WNBA** (`wnba.py:336`) receives **US recreational books only**, and at
  `markets=h2h,spreads,totals` × `regions=us` each call costs **3 credits**.
- **Tennis** (`scripts/fetch_tennis_odds.py`) pins `regions=eu`, which is where
  both preferred books actually live — BetOnline is listed in `us` and `eu`,
  Pinnacle only in `eu`. Requesting `eu` alone gets full preferred-book coverage
  at half the cost of `eu,us`.

To get a sharp price on the WNBA path, `regions` has to become a parameter on
`fetch_odds_api_odds`. That is a real change with a real cost: adding `eu`
alongside `us` **doubles** the per-call credit spend, from 3 to 6, against a
500/month allowance. Decide it deliberately rather than assuming the existing
book preference already does the job.

Its second use is one the app does not yet exploit: **a sharp price to measure
the recreational board against.** Comparing an unlimited DK/FD board against a
rationed Pinnacle price is, mechanically, +EV detection — the feature SharpAPI
gates behind its $229/mo Pro tier.

Two caveats on that idea. Pinnacle's edge concentrates in the **closing** line,
so a once-daily pull mostly misses it; the value is a near-kickoff snapshot on a
narrow set of games, not a broad sweep. And exchange prices (Betfair, Matchbook)
are liquidity-dependent — a thin market there is noise, not signal.

**Budget in credits, not calls.** A request costs `markets × regions`, so
`markets=h2h&regions=eu` is 1 credit (verified live via `x-requests-last: 1`),
while `markets=h2h,totals&regions=eu,us` on the same endpoint is 4. The
500/month allowance therefore buys anywhere from 500 calls to 125, depending
entirely on how each call is shaped. The tennis script's `REGIONS = "eu"` is a
deliberate halving of that cost **without sacrificing preferred-book coverage** —
`eu` carries both Pinnacle and BetOnline, so adding `us` would spend twice as
much to gain nothing the script prefers.

At 1 credit per call, that is roughly 16 sharp snapshots a day if nothing else
is spending.

> **Budget observation, 2026-09-19:** remaining dropped from 492 to 435 across
> thirteen days — about 57 **credits** not accounted for by any deliberate call.
> That is an unknown number of calls, since a multi-market or multi-region call
> spends more than one.
> Worth identifying what is spending it. The tennis script defaults to US Open
> sport keys, which went inactive when the tournament ended.

### SharpAPI — breadth without a cap

Free tier is **DraftKings + FanDuel, 60-second delay, 12 req/min, no monthly
cap**. The absence of a monthly cap is the whole point: it is the only provider
you can poll without arithmetic.

Its league list is far wider than the others — `ufc`, `ncaab`, `atp`, `wta`,
`boxing_matches`, `ncaaf`, plus all four major US leagues — and leagues are
**first-class**, which matters for sports where the alternative is inferring a
promotion from clustered start times.

Backs **UFC** (see `ufc_page.md`), and is the intended answer for NCAAB when
college basketball starts in November — the sport that would otherwise force a
paid tier, since it runs ~100 games a night with no cheap alternative.

Its weakness is books: two recreational US books, no sharp reference, no
de-vigged price.

### ESPN — free, pre-joined, pregame-only

Undocumented but keyless and uncapped. Odds arrive **inside the scoreboard
response**, already keyed to the game, so there is no name join to maintain.
Verified to carry DraftKings moneyline, spread, and total — each with both
`open` and `close` — for college football (70/71 games on a game-day Saturday)
and MLB (15/15 on a future date).

Two hard limits. **One book.** And the `odds` block **disappears at kickoff**,
so anything not captured pregame is unrecoverable, and any route that filters on
"has a line" must count persisted values or live games will vanish from the
board mid-game.

Backs **NCAAF** (see `ncaaf_dashboard.md`).

## Choosing for a new sport

1. Does the win-probability path need de-vigged `fairOdds`? → SGO, and budget for it.
2. Is it a high-volume sport that would blow the entity cap? → SharpAPI.
3. Is it a sport where promotion or league identity is ambiguous in the feed? → SharpAPI, whose leagues are first-class.
4. Does ESPN already return odds inside a scoreboard call you are making anyway? → ESPN, and accept one book.
5. Do you need a sharp or exchange price to compare against? → The Odds API, rationed.

## Nothing here is retired

All four stay supported. The specs for NCAAF and UFC pick a provider **for those
sports**; they do not deprecate anything. WNBA and tennis continue on The Odds
API.

## Deferred

- SharpAPI `/odds/delta?since=` — returns only changed rows plus a `removed`
  list, a better fit for odds history than snapshot-and-hash, but adopting it
  for one sport would split how history is gathered across the app
- SharpAPI `/health` `newest_odds_age_s` for freshness monitoring
- A cross-provider +EV comparison using Pinnacle as the reference price
- A kill-switch that stops SGO fetching above 80% of the monthly cap with more
  than five days left in the cycle (carried over from `odds_fix.md`)
