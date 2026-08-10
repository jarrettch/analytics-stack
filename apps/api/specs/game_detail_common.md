# Game Detail — Common Spec

_Created: 2026-05-30_  
_Revised: 2026-06-02_

Shared contract for the per-game detail page. The page lives behind the
parked user/auth + bet-tracking work, so this is a forward spec, not a
build order. Sport-specific specs (`game_detail_nba.md`, `game_detail_nhl.md`,
later MLB/WNBA) layer on top of this and own their tabs, box-score shapes,
and sport-specific data gaps.

**Source of truth for shapes:** the v0 prototype at
`../analytics-v0/game-detail-v0-design` already types every payload
(`lib/sports-types.ts`, `lib/game-detail-types.ts`) and renders per-sport
views (`components/game-detail/{nba,nhl,mlb,nfl}-detail.tsx`). The prototype
fakes data via `lib/sample-game-detail.ts`. This spec's job is to say where
each field actually comes from — the UI is already specified by the prototype.

---

## Page shape

- Route: `app/game/[gameId]/page.tsx` — single dynamic route, fetches one
  `GameDetail`, dispatches on `detail.sport` to the per-sport view
  (`components/game-detail/index.tsx`).
- Header (`GameHeader`): teams, abbreviations, totals, status badge, plus
  matchup meta (venue, broadcast, series, spread line, O/U, model name).
- Linescore: per-period scoring grid (quarters / periods / innings) — sport-specific.
- Tabs: 4 per sport, defined by `getGameDetailTabs(sport)`. First tab is the
  signature panel (Win Prob for NBA/NHL/NFL, Gamefeed for MLB).

## Status model

Reuse the existing dashboard card contract — do **not** invent a new one.

Top-level status fields:
- `status`: normalized string `"preview" | "live" | "final"`
- `rawStatus`: original source-specific game state / status string, preserved for
  grading edge cases and sport-specific display details

`status` maps to the existing dashboard semantics:
- preview → scheduled / pregame
- live → in progress
- final → completed

`rawStatus` preserves distinctions the normalized field intentionally collapses:
- NHL examples: `F`, `F/OT`, `F/SO`, `OFF`
- future sports may preserve their own final subtypes or postponement labels

Period/clock display strings are composed the same way the dashboards compose
them (see `nhl_dashboard.md` for the NHL period→string table, `nba.py` for NBA).

## Shared payload types

These are sport-agnostic and live in `game-detail-types.ts`:

| Type | Fields | Source posture |
|---|---|---|
| `PlayByPlayEvent` | `eventId`, `sequence`, clock, quarter/period, team, event, away/home score, optional `winProbAway`/`winProbHome`, optional `sourceTimestamp` | Per-sport PBP feed (see each spec). `eventId` should be stable from upstream when available, otherwise deterministic. Optional WP fields piggyback on the win-prob reconstruction. |
| `WinProbPoint` | `x` (game progress 0–100), awayProb, homeProb, label | Derived alongside PBP, not a separate feed. |
| `ScoringPlay` | period, time, team, description, away/home score | Filtered subset of PBP (scoring events only). |

Ordering rule:
- The API returns `playByPlay[]` already sorted in display order.
- Clients should not re-sort except as a defensive fallback.

## Payload contract

Unless a sport-specific spec says otherwise, the detail endpoint should prefer
**stable keys with nullable values** over shape-shifting payloads. The UI should
not have to infer whether a section is unsupported vs. temporarily unavailable
from missing object keys.

Top-level field posture on `GameDetail`:

| Field | Contract |
|---|---|
| `sport` | required |
| `gameId` | required |
| `status` | required |
| `rawStatus` | required |
| `header` | required |
| `linescore` | required once supported by the sport; may be empty in preview |
| `matchup` | required when the sport defines a matchup view |
| `playByPlay` | optional; use `[]` when supported but no events yet |
| `scoringPlays` | optional; use `[]` when supported but no scoring events yet |
| `winProbability` | optional; use `[]` when supported but no timeline exists yet |
| `boxscore` | optional; may be `null` or contain empty collections in preview |
| sport-specific extras | optional; must be declared by the sport spec |

Rule of thumb:
- Use **required + nullable/empty** for fields the page always understands.
- Use **optional** only for genuinely sport-specific or deferred features.

## State availability

The endpoint should be explicit about preview/live/final behavior so the page
does not make ad hoc assumptions.

Common expectations:
- **Preview**: header and matchup metadata should exist; linescore is empty;
  play-by-play is empty; win probability may be empty or omitted if no pregame
  series is defined by the sport spec.
- **Live**: header, linescore, and any supported live tabs should be populated
  with partial data.
- **Final**: all available per-game data should be complete; final-only add-ons
  like recap-derived fields may appear here.

Sport-specific docs override this section where needed.

## Matchup / header data — already available

The header-level `MatchupData` block (records, win pct, streak, predicted score,
win probability, spread, O/U, model) is exactly what `/api/{sport}/today`
already assembles for the dashboards and should be reused per game keyed by
`gameId`.

Sport-specific Matchup tabs may differ:
- NBA-style Matchup tabs can reuse season/team-comparison numbers already fetched
  by `/today`
- NHL-style Matchup tabs may instead use per-game team stats from the game box
  score and therefore require new wiring

The common contract guarantees reused header/meta assembly. Tab-level Matchup
content is owned by the sport spec.

## Reuse boundary

Header and shared matchup metadata must come from the same assembly helpers used
by `/api/{sport}/today`. The detail endpoint should call into shared assembly
logic rather than duplicate that mapping in a second code path.

Goal:
- one source of truth for records, streak, odds, model name, venue, and
  normalized display strings
- no drift between dashboard cards and detail headers

## What's genuinely new vs. reused

| Bucket | Status | Notes |
|---|---|---|
| Header + matchup meta | ✅ reuse `/today` | records, ratings, odds, model, venue |
| Team-comparison numbers (sport-dependent Matchup tab) | ✅ / 🔨 | NBA can reuse season ratings; NHL per-game matchup stats need boxscore wiring |
| Win-prob timeline | ⚠️ partial | NBA reconstructs it client-side today (`win_probability.md`); NHL has no model yet |
| Per-game play-by-play | 🔨 new wiring | upstream feeds exist for both sports, not yet surfaced by our API |
| Player box scores | 🔨 new wiring | upstream feeds exist for both sports, not yet surfaced |
| Sport-signature metrics | 🔨 new derivation | NBA scoring efficiency, NHL three-stars, etc. |

Legend: ✅ data exists · ⚠️ partial / one sport · 🔨 needs new ingestion or derivation.

## Partial-data / fallback behavior

The detail endpoint should be resilient to upstream gaps.

Rules:
- If one tab's data is unavailable, do **not** fail the entire page payload if
  header and core game metadata can still be assembled.
- Prefer returning empty arrays / null sub-objects for unsupported-or-missing
  tab data over throwing route-level 500s.
- A sport-specific spec should define when a field is intentionally deferred vs.
  temporarily unavailable.

This allows the page to render header + stable tabs even when an upstream feed
is degraded.

## API surface (proposed)

One endpoint per sport, keyed by the same `gameId` the dashboards use:

```txt
GET /api/{sport}/game/{gameId}   → GameDetail
```

Assembles:
- header (from the `/today` path)
- per-game box score
- play-by-play
- linescore
- win-prob series where supported
- sport-specific extras declared by the sport spec

## Cache and correction policy

Cache like `/today` with one clarification:
- live games use short TTL
- final games may be cached long/hard once the upstream source is considered
  stable for that sport

If a sport has meaningful final subtypes or late corrections, preserve them in
`rawStatus` even when normalized `status` is `"final"`.

## Build order (when unparked)

1. NBA first — it's the most concrete (see `game_detail_nba.md`); Win Prob +
   Plays already have a data path, Matchup reuses `/today`. Only Boxscore +
   Shooting need new work.
2. NHL second — heavier; needs gamecenter box score + PBP ingestion and a
   win-prob model from scratch unless Win Prob is deferred for v1
   (see `game_detail_nhl.md`).
3. MLB / WNBA later — prototype types exist for MLB. WNBA is expected to be able
   to reuse much of the NBA page contract, but should get its own spec once
   upstream feed details are known.