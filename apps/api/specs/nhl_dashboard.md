# NHL Dashboard

_Created: 2026-05-14_

Mirrors the NBA dashboard structurally — same `/today` + `/cron/fetch-odds`
shape, same card layout, same SGO odds plumbing. This doc captures the
decisions that differ from NBA so we don't re-discover them.

---

## Data sources (all free, no auth)

| Need | Endpoint | Notes |
|---|---|---|
| Scoreboard | `https://api-web.nhle.com/v1/score/now` | 307s to `/score/<YYYY-MM-DD>`. Per-game `gameState` is `FUT` / `PRE` / `LIVE` / `FINAL` / `OFF`. Filter `gameType` to `2` (regular) and `3` (playoffs); skip preseason. |
| Standings | `https://api-web.nhle.com/v1/standings/now` | Per-team `wins`, `losses`, `otLosses`, `points`, `streakCode`+`streakCount`, `l10Wins`/`l10Losses`/`l10OtLosses`, `conferenceAbbrev`, `divisionAbbrev`, `wildcardSequence`. |
| Team summary | `https://api.nhle.com/stats/rest/en/team/summary?cayenneExp=seasonId=20252026` | `goalsForPerGame`, `goalsAgainstPerGame`, `powerPlayPct`, `penaltyKillPct`, `faceoffWinPct`, `shotsForPerGame`, `shotsAgainstPerGame`. NHL's analogue of ORtg/DRtg/Pace. |
| Odds | SGO `leagueID=NHL` | Markets: `points-{home,away}-game-{ml,sp}-{home,away}` and `points-all-game-ou-{over,under}`. "points" = goals in NHL. Same `fairOdds` → win-prob path as NBA. |

Seasons roll on the autumn boundary; hard-code `seasonId=20252026` for now,
revisit in October.

## Game state → card status

Match the NBA card contract: `gameStatus` is the canonical numeric (1=pre, 2=live,
3=final). The display string is composed from `periodDescriptor.periodType`,
`clock.timeRemaining`, and `gameScheduleState`, not pulled raw from the API.

| API field combo | `gameStatus` | Display string |
|---|---|---|
| `gameState` ∈ `FUT` / `PRE` | 1 | Start time, ET (`"7:05 PM ET"`) |
| `gameState` = `LIVE`, `periodType` = `REG` | 2 | `"<N>{st,nd,rd,th} <mm:ss>"` (e.g. `"2nd 14:32"`) |
| `gameState` = `LIVE`, `gameScheduleState` = `INT` | 2 | `"INT"` (intermission, no clock) |
| `gameState` = `LIVE`, `periodType` = `OT` | 2 | `"OT <mm:ss>"` |
| `gameState` = `LIVE`, `periodType` = `SO` | 2 | `"SO"` |
| `gameState` ∈ `FINAL` / `OFF`, `periodType` = `REG` | 3 | `"Final"` |
| `gameState` ∈ `FINAL` / `OFF`, `periodType` = `OT` | 3 | `"F/OT"` |
| `gameState` ∈ `FINAL` / `OFF`, `periodType` = `SO` | 3 | `"F/SO"` |

`F/OT` and `F/SO` must be distinct from `Final` — they affect bet grading
(spread/total resolution) and the user expects to see them.

## Team identifiers

First-class deliverable, not a task bullet. Three maps live in `nhl.py`:

```python
# NHL official tricode (from api-web.nhle.com) is the canonical key.
# 32 teams. Use teamAbbrev.default from standings.

NHL_TRI_TO_NAME = {
    "ANA": "Ducks", "BOS": "Bruins", "BUF": "Sabres", ...
}

# SGO teamID → NHL tricode. Verify against a live SGO response before shipping.
# Mirror the NBA pattern: include _NHL-suffixed, non-suffixed, and short forms.
SGO_TEAM_ID_TO_TRI = {
    "ANAHEIM_DUCKS_NHL": "ANA", "ANAHEIM_DUCKS": "ANA", "ANA": "ANA", ...
}

# ESPN CDN slug exceptions. Spot-checked; validate each URL before shipping.
# Re-run the check whenever a team rebrands (UTA, FLA-era ARI, etc.).
ESPN_SLUG_OVERRIDES = {
    "TBL": "tb",
    "LAK": "la",
    "SJS": "sj",
}
```

When `ESPN_SLUG_OVERRIDES.get(tri)` is `None`, fall back to `tri.lower()`. This is
the same pattern as the `AZ → ari` fix in `MatchupCard` — keep the overrides
in the API payload so the frontend stays sport-agnostic.

## Persistence contract

Same shape as NBA, just a different table name:

- Matchup key format: `"AWAY@HOME"` using NHL tricodes (e.g. `"BOS@TOR"`).
- Supabase table: `nhl_odds`, DDL identical to `nba_odds` — composite unique on `(game_date, matchup_key)`, columns mirror `nba_odds` 1:1.
- Call sites use `save_persisted_odds(..., table_name="nhl_odds")` and `load_persisted_odds(table_name="nhl_odds")` — generalize the helper in `utils.py` if it isn't already parameterized.

## MVP scope

- `apps/api/routers/nhl.py` with:
  - `/api/nhl/today` — assembled game payload (same envelope as `/api/nba/today`)
  - `/api/nhl/cron/fetch-odds?offset=0|1` — daily odds prefetch, identical to NBA
- Reuse everything in `routers/utils.py` (cache, SGO helpers, persistence, http client).
- New Supabase table: `nhl_odds` — see [Persistence contract](#persistence-contract). RLS + service-role policy as per cleanup.md #1.
- Frontend: add NHL to the sport switcher in `apps/visualize-ev`. Logos via the `ESPN_SLUG_OVERRIDES` map from [Team identifiers](#team-identifiers) — the API payload should hand the frontend a ready-to-use slug, not the raw tricode.
- **Polling cadence:** match NBA. When the assembled response contains a `gameStatus == 2`, set `assembled` TTL to `CACHE_TTL_LIVE` (20s); otherwise `CACHE_TTL_DEFAULT` (60s). Frontend interval mirrors the NBA dashboard's `hasLiveGames`-aware polling — see cleanup.md #8 (the stale-closure fix applies to NHL too once that lands).

### Card data shape

The frontend already ships `NHLTeamStats` and `NHLRatings` components with a
sport-specific shape — produce the payload to match, **not** the NBA shape.

**Principle:** matchup-level fields are cross-sport uniform; team-level fields
are sport-specific. The envelope (`gameStatus`, `period`, `gameClock`, spread/
total/ML bundle) looks the same across NBA, MLB, NHL — `page.tsx` and other
shared surfaces key off these. Inside `away` / `home`, each sport has its own
shape (NBA's `offRating`, NHL's `goalsFor`, MLB's `era`).

Per-team fields (extending `NHLTeamData` in `packages/ui/src/types/sports-types.ts`):

| Payload key | Source | Notes |
|---|---|---|
| `name` | standings `teamName.default` | |
| `abbreviation` | standings `teamAbbrev.default` | The canonical NHL tricode. |
| `espnSlug` | `ESPN_SLUG_OVERRIDES.get(tri, tri.lower())` | New field — frontend reads this directly, no client-side mapping. |
| `record` | `"{wins}-{losses}-{otLosses}"` | Three segments, not two. |
| `winPct` | `points / (gamesPlayed * 2)` | Points percentage. The NHL card does **not** render `winPct`, so the PTS%/WIN% label concern is moot — keep the field for downstream use. |
| `seed` | `divisionSequence` for v1 | Temporary display rank, not true playoff seeding. Wildcard handling is post-launch. |
| `conference` | standings `conferenceAbbrev` | |
| `streak` | `"{streakCode}{streakCount}"` (e.g. `"W3"`) | |
| `score` | scoreboard `awayTeam.score` / `homeTeam.score` | **New field** — `NHLTeamData` currently lacks this; extend the type. |
| `goalsFor` | team-summary `goalsForPerGame` | Per-game; display name `GF/G` in UI is already wired. |
| `goalsAgainst` | team-summary `goalsAgainstPerGame` | |
| `powerPlayPct` | team-summary `powerPlayPct * 100` | NHL returns 0–1; UI expects 0–100. Multiply server-side. |
| `penaltyKillPct` | team-summary `penaltyKillPct * 100` | Same scaling. |
| `savesPct` | computed: `1 - (goalsAgainstPerGame / shotsAgainstPerGame)` | Not in the team-summary response directly; derived. |
| `predictedScore` | naive blend (see Projected score) | |
| `winProbability` | from SGO `fairOdds` | Same path as NBA. May be `null` when no odds are available — see rendering rule below. |

**Rendering rule:** if `winProbability` is `null` on either team (no odds
available), hide the probability bar entirely — do not render an even 50/50
split, which silently implies "we modeled this as a coin flip." Consistent
with the None-means-hide principle used elsewhere for missing stats.

`NHLMatchupData` currently lacks `gameStatus`, `period`, `gameClock`, the
spread/total/ML bundle, and the `seriesGameNumber`-style fields NHL needs
for playoffs. Extend it to mirror `NBAMatchupData`'s envelope (same names —
`gameStatus`, `pregameSpread`, `spreadOddsHome`, `homeMoneyline`, etc.). The
dashboard's `hasLiveGames` check in `page.tsx` already keys off `gameStatus`,
so this is required, not optional.

Nullability for `period` / `gameClock` across states (pin this so the type
stays consistent across routers):

| `gameStatus` | `period` | `gameClock` |
|---|---|---|
| 1 (pre) | `null` | `null` |
| 2 (live, REG) | 1–3 | `"mm:ss"` |
| 2 (live, OT) | 4 | `"mm:ss"` |
| 2 (live, SO) | 5 | `null` (no clock in shootouts) |
| 3 (final) | last period played (3 / 4 / 5) | `null` |

The type signatures land as `period: number | null` and
`gameClock: string | null` — already-nullable, matching what the table dictates.

#### Team summary fallback

The team-summary endpoint can return a sparse row early-season. Treat any
missing source field (`goalsForPerGame`, `goalsAgainstPerGame`, `powerPlayPct`,
`penaltyKillPct`, `shotsAgainstPerGame`) as `None` in the payload and hide the
stat in the card UI — never render `0` for missing data. A zero implies a
shutout team / failed PK, which is meaningfully wrong.

`savesPct` needs an explicit denominator guard in the router, not just the
None-handling above: if `shotsAgainstPerGame` is `0` *or* `None`, set
`savesPct` to `None`. Don't rely on truthiness — `0` is falsy in Python and
will catch most cases, but spell it out so the next reader doesn't have to
recheck:

```python
if (
    shots_against_pg is not None
    and shots_against_pg > 0
    and goals_against_pg is not None
):
    saves_pct = 1 - (goals_against_pg / shots_against_pg)
else:
    saves_pct = None
```

Log misses at `WARNING` with the team tricode so we can spot upstream breakage.
Same defensive pattern as the MLB stats path.

### Projected score

Naive: `gameGoalRate = (awayGFpG + awayGApG + homeGFpG + homeGApG) / 4`, split
by team rating. Blend 70% market (from SGO total) / 30% naive, same as NBA.
Round to integers. NHL totals run small (typically 5.5–7) — clamp negative
margins gracefully so `predictedScore` never goes below 0.

## Deferred (explicit non-goals for v1)

- **Win-probability sparkline.** NHL has `api-web.nhle.com/v1/gamecenter/<gameId>/play-by-play` but the WP model is not a quick port from basketball/baseball (low scoring, empty-net effects, shootouts). Ship the dashboard without it; design WP separately.
- **Playoff series context.** `seriesStatus` field is right there on each game in playoffs — easy follow-up, but not blocking v1.
- **Goalie / starting lineups.** Useful but downstream of the basic card.
- **Power-play / penalty-kill split in card UI.** Capture the data, surface later.

## Decisions (closed)

- **Zero-game days:** match the MLB cron behavior shipped in `83d199d` —
  `cron/fetch-odds` returns `{success: true, fresh_from_api: 0, ...}` and
  no-ops cleanly. No special-casing needed in the NHL router beyond reusing
  the same pattern.
- **Playoff payload shape:** even though v1 doesn't populate them, the
  `NHLMatchupData` type gets `seriesGameNumber: number | null` and
  `seriesStatus: string | null` from the start so the playoff follow-up
  isn't a type-system migration. Router emits `null` for both in v1.

## Open questions

- SGO entity cost: NHL playoffs is ≤8 games/day; should be a small marginal hit on the budget. Sanity-check after one week.

## Tasks

- [x] Add `nhl_odds` table in Supabase + RLS policy ([Persistence contract](#persistence-contract)) — _shipped with the sport-split migration_
- [x] `save_persisted_odds` / `load_persisted_odds` parameterized on `table_name`; default removed — _shipped 2026-05-14, commit `44b1666`_
- [ ] Extend `NHLMatchupData` + `NHLTeamData` in `packages/ui/src/types/sports-types.ts`: add `gameStatus`, `period`, `gameClock`, `pregameSpread`, `spreadOddsHome/Away`, `overOdds`, `underOdds`, `homeMoneyline`, `awayMoneyline`, **`seriesGameNumber: number | null`**, **`seriesStatus: string | null`** on the matchup; add `score` + `espnSlug` on the team
- [ ] Fill in `NHL_TRI_TO_NAME`, `SGO_TEAM_ID_TO_TRI`, and `ESPN_SLUG_OVERRIDES` in `nhl.py` ([Team identifiers](#team-identifiers))
- [ ] `routers/nhl.py` — scoreboard + standings + team-summary + odds, assembled `/today` (mirrors `nba.py`; uses `table_name="nhl_odds"`)
- [ ] Status-string composer driven by the [Game state → card status](#game-state--card-status) table
- [ ] Register the router in `main.py`
- [ ] Wire `/api/nhl/cron/fetch-odds` into the GH Actions workflow (offset=0 and offset=1)
- [ ] Frontend: add `"nhl"` to `SPORTS` in `apps/visualize-ev/app/page.tsx`; route the fetch through `/api/nhl/today`; use `espnSlug` from the payload for logos
- [ ] One-week SGO entity-usage check after launch
