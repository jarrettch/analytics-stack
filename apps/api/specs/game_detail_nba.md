# Game Detail — NBA

_Created: 2026-05-30_  
_Revised: 2026-06-02_

Layers on `game_detail_common.md`. The most concrete of the sport specs:
half the data path already exists. Prototype view:
`../analytics-v0/game-detail-v0-design/components/game-detail/nba-detail.tsx`.
Payload type: `NBAGameDetail` in `lib/game-detail-types.ts`.

Tabs (`getGameDetailTabs("nba")`): **Win Prob · Shooting · Matchup · Boxscore**.

---

## Tab-by-tab data map

| Tab | What it renders | Source | Status |
|---|---|---|---|
| **Win Prob** | `WinProbabilityChart` (`winProbability[]`) + `PlayByPlayTable` (`playByPlay[]`) | win-prob reconstruction + NBA CDN PBP feed | ⚠️ exists client-side, needs server packaging |
| **Shooting** | scoring efficiency (`PPP`, `TS%`) + optional scoring opportunities | derived from box score + PBP | 🔨 new derivation |
| **Matchup** | OffRtg, DefRtg, Pace, Record, Streak | `leaguedashteamstats` + standings, already in `/today` | ✅ reuse |
| **Boxscore** | per-player traditional line + team totals | `nba_api` boxscore endpoint (not yet wired) | 🔨 new wiring |

## Locked decisions (2026-06-02)

1. **`Shooting` ships efficiency-only in v1.** `scoringOpportunities`
   (base/ORB/TOV) is **deferred** until its definition is locked — see the
   Shooting section. Do not build it during implementation.
2. **Possessions / PPP are derived from box-score totals** we already fetch, not
   `boxscoreadvancedv2`. The advanced endpoint is the fallback only if the
   derived numbers prove unreliable.
3. **The payload shape stays WNBA-reusable where it's free**, but NBA delivery is
   **not gated** on proving WNBA upstream parity — that's validated later. See
   "WNBA reuse posture".

## State behavior

Expected behavior by game state:

- **Preview**
  - `matchup` is populated from `/today`
  - `playByPlay = []`
  - `winProbability = []` unless we explicitly choose to expose a pregame prior
    point
  - `boxscore = null` or empty collections
  - `shooting = null`

- **Live**
  - `matchup` remains populated
  - `playByPlay[]` is populated from the proxied NBA CDN feed
  - `winProbability[]` is populated from the same replay/reconstruction used by
    the dashboard logic, now assembled server-side
  - `boxscore` is populated from the live box score source when available
  - `shooting` may be partial if derived inputs are incomplete

- **Final**
  - all supported tabs should be fully populated
  - `playByPlay[]` and `winProbability[]` are immutable once final payload is
    cached

## Win Prob — ⚠️ already most of the way there

`win_probability.md` is the full spec for this and it already ships on the
dashboard. The pieces:

- **Live WP model**: logistic on score margin + time remaining + pre-game
  spread, dynamic `k` by pace. Lives client-side in `LiveWinProbSection`.
- **Timeline reconstruction**: `hydrateWinProbHistory()` pulls the NBA CDN PBP
  feed and replays it into a full `WinProbPoint[]`.
- **PBP feed**: `cdn.nba.com/static/json/liveData/playbyplay/playbyplay_{gameId}.json`,
  proxied via Next rewrites at `/api/nba-cdn/:path*` (CORS). Same feed backs
  both the WP timeline and the `playByPlay[]` table.

**Work to do:** lift this from a dashboard-section concern into the
`/api/nba/game/{gameId}` payload so the detail page gets `winProbability[]` and
`playByPlay[]` server-assembled instead of re-deriving in the browser.

### Win Prob fallback rules

The detail endpoint owns win-prob packaging for NBA. Fallback behavior should be
explicit:

- If the PBP feed is available, return both `playByPlay[]` and reconstructed
  `winProbability[]`.
- If the PBP feed is available but timeline reconstruction fails, still return
  `playByPlay[]` and set `winProbability = []`.
- If the PBP feed is unavailable, return `playByPlay = []` and `winProbability = []`
  rather than failing the whole detail payload.
- Optional per-event `winProbAway` / `winProbHome` should only be attached when
  the event can be aligned to a reconstructed WP point confidently.

## Boxscore — 🔨 new wiring, upstream is free

We already depend on `nba_api` (`apps/api/routers/nba.py` imports
`nba_api.live.nba.endpoints.scoreboard` and stats endpoints). The traditional
box score is the same library:

- `nba_api.stats.endpoints.boxscoretraditionalv3` (or the live `boxscore`
  endpoint for in-progress games) → per-player Min/Pts/Reb/Ast/Stl/Blk/TO/FG/3P/FT/+- and team totals

This maps directly onto `NBAPlayerBoxScore` / `NBATeamBoxScore` — the
prototype's columns were modeled on this shape.

Watch the same Akamai 403 / Referer header issue already handled by
`NBA_LIVE_HEADERS` in `nba.py`.

### Boxscore source rule

Source selection should be explicit:

- use the live box score endpoint for in-progress games
- use `boxscoretraditionalv3` for final games unless the live endpoint remains
  the more reliable normalized source
- normalize both sources into the same `NBAPlayerBoxScore` /
  `NBATeamBoxScore` shape before returning from `/api/nba/game/{gameId}`

The client should never branch on upstream source shape.

## Shooting — 🔨 the only real design work

Two panels are possible, but only one is fully specified today.

### v1 (locked): efficiency-only

Ship:
- `tsPct`
- `ppp`
- optional `possessions`

Definitions (possessions derived from box-score totals per the locked decision):
- `TS% = PTS / (2 * (FGA + 0.44 * FTA))`
- `PPP = PTS / possessions`
- `possessions ≈ FGA + 0.44*FTA - ORB + TOV`. `boxscoreadvancedv2` is the
  fallback source only if these derived numbers prove unreliable.

### Deferred: full tab (scoring opportunities)

If the full tab ships, define `scoringOpportunities` as:

- `base`: initial scoring chances from offensive possessions excluding
  second-chance possessions created by ORB
- `orb`: second-chance possessions created by offensive rebounds
- `tov`: scoring chances or points created directly from opponent turnovers
- `total`: `base + orb + tov`

If this path is chosen, the implementation must also lock:
- whether these are counts of chances, possessions, or points
- whether `tov` is sourced from box score totals or PBP classification
- whether displayed totals must add up exactly across the UI

Until those are defined, `scoringOpportunities` should be treated as **deferred**
rather than “to be figured out during build.”

## WNBA reuse posture

The NBA page contract is expected to be the closest future template for WNBA.
That does **not** automatically guarantee identical upstream availability or
identical matchup inputs, but the page shape and most payload semantics should
be kept reusable where practical.

## Effort read

- Win Prob + Plays: **S** — relocate existing logic to the server.
- Matchup: **XS** — reuse `/today`.
- Boxscore: **M** — wire `nba_api` box score, map fields, handle live/final.
- Shooting: **S–M** — efficiency is mechanical; scoring-opportunities remains
  deferred unless explicitly defined.

NBA is realistic inside a two-week playoff window because only Boxscore is net-new
ingestion; everything else is reuse or relocation.