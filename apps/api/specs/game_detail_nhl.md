# Game Detail — NHL

_Created: 2026-05-30_  
_Revised: 2026-06-02_

Layers on `game_detail_common.md`. Heavier than NBA: the team-level header/meta
data is reused from `/today`, but everything inside the tabs (PBP, box score,
win prob) is net-new. Prototype view:
`../analytics-v0/game-detail-v0-design/components/game-detail/nhl-detail.tsx`.
Payload type: `NHLGameDetail` in `lib/game-detail-types.ts`.

Tabs (`getGameDetailTabs("nhl")`): **Win Prob · Matchup · Boxscore · Plays**.

---

## Locked decision (2026-06-02)

**Win Prob is deferred for v1 (Path B).** v1 ships `Matchup + Boxscore + Plays`,
plus `Three Stars` for final games. The NHL win-prob model is **deferred**, not
"at risk" — see "If Win Prob is deferred in v1" for the payload posture. Build
the rest of the page first; revisit Win Prob only if the schedule opens up.

## Tab-by-tab data map

| Tab | What it renders | Source | Status |
|---|---|---|---|
| **Win Prob** | `WinProbabilityChart` (`winProbability[]`) + optional Three Stars region | WP model (none yet) + gamecenter landing | 🔨 model from scratch; likely deferred for v1 |
| **Matchup** | Team Stats: shots, hits, blocks, PIM, PP, faceoffs, give/takeaways | gamecenter boxscore `teamStats` | 🔨 new wiring |
| **Boxscore** | skaters (G/A/P/+-/PIM/SOG/HIT/BLK/TOI) + goalies (SV/SA/SV%/TOI) | gamecenter boxscore | 🔨 new wiring |
| **Plays** | `PlayByPlayTable` (`playByPlay[]`) | gamecenter play-by-play | 🔨 new wiring |

Note: unlike NBA, the Matchup tab here is **per-game team stats from the box
score**, not season ratings — so it does not reuse `/today`. It comes for free
once the boxscore call is wired.

## State behavior

Expected behavior by game state:

- **Preview**
  - `matchup` may be unavailable if it depends on per-game `teamStats`
  - `boxscore = null` or empty collections
  - `playByPlay = []`
  - `threeStars` absent
  - `winProbability = []` or absent depending on the v1 scope decision

- **Live**
  - `boxscore` and per-game `teamStats` populate from `/boxscore`
  - `playByPlay[]` populates from `/play-by-play`
  - `threeStars` absent
  - `winProbability[]` only present if the model ships in v1

- **Final**
  - `boxscore`, `teamStats`, `playByPlay`, and `linescore` should be complete
  - `threeStars` may be present from `/landing`
  - preserve final subtype in `rawStatus` (`F`, `F/OT`, `F/SO`) even when
    normalized `status` is `"final"`

## Upstream feeds — all on api-web.nhle.com (free, no auth)

We already hit `api-web.nhle.com` for the dashboard (see `nhl_dashboard.md`:
`score/now`, `standings/now`, team summary). The gamecenter endpoints are the
same host and round out the detail page:

| Need | Endpoint | Fills |
|---|---|---|
| Box score | `/v1/gamecenter/{gameId}/boxscore` | skaters, goalies, `teamStats` (Matchup tab) |
| Play-by-play | `/v1/gamecenter/{gameId}/play-by-play` | `playByPlay[]`, scoring plays, linescore |
| Landing / recap | `/v1/gamecenter/{gameId}/landing` | `threeStars`, period linescore, game state, recap-only metadata |

`gameId` is the NHL official game id already present in the `score/now`
scoreboard, so keying the detail endpoint is straightforward. Reuse the
`gameState`→status mapping table from `nhl_dashboard.md` (FUT/PRE/LIVE/FINAL/OFF,
plus `F/OT` and `F/SO` distinctions — these matter for bet grading).

## Source-of-truth rules

Multiple NHL gamecenter endpoints overlap. The detail endpoint should declare one
authoritative source for each field family to avoid cross-feed drift.

Suggested default:
- `/boxscore` → skaters, goalies, `teamStats`
- `/play-by-play` → `playByPlay`, `scoringPlays`
- `/landing` → `threeStars`, recap-only metadata
- normalized `status` / `rawStatus` → whatever scoreboard/game-state mapping is
  already canonical in the dashboard stack

If `linescore` appears in more than one endpoint, choose one canonical source
and do not merge field-by-field unless necessary.

## Win Prob — 🔨 the expensive tab

There is **no NHL win-probability model today** (`NBA` has one in
`win_probability.md`; NHL does not).

### If Win Prob ships in v1

Minimum contract:
- `winProbability[]` is derived server-side from the PBP feed plus a pregame
  prior from odds
- the first point may represent pregame prior before puck drop
- the full timeline is rebuilt by replaying the play stream in order
- live updates append or revise the series as new events arrive
- if model output fails but PBP succeeds, return `playByPlay[]` and
  `winProbability = []`
- if PBP fails, return `playByPlay = []` and `winProbability = []`

Possible implementation approach:
1. Port the NBA-style logistic approach using goal margin + time remaining +
   pregame moneyline / puck-line prior
2. Use `calculate_win_probability_from_odds` in `utils.py` to convert pregame ML
   into a prior
3. Replay the play-by-play feed to construct the timeline

Hockey is lower-scoring and more variance-driven than NBA, so the curve will be
jumpier and `k` tuning will matter more.

### If Win Prob is deferred in v1

- return `winProbability = []` or omit the field consistently, per the common
  payload contract
- do not imply live model support in the UI
- keep the tab hidden, disabled, or clearly marked deferred — this is a product
  choice, but it must be explicit and not ad hoc

## Three Stars

`threeStars?` should be independent of NHL win probability.

- source: `/landing`
- availability: final games only
- if present, it can render in the first panel or header-adjacent area even when
  `winProbability[]` is unavailable
- absence of `threeStars` should not be treated as an endpoint failure for the
  rest of the page

This keeps a cheap, useful final-game enhancement shippable even if Win Prob
misses v1.

## Recommended v1 scope

Recommended v1 scope for the two-week window:

- ship `Matchup`
- ship `Boxscore`
- ship `Plays`
- ship `Three Stars` for final games once `/landing` is wired
- defer `Win Prob` unless time permits

If this recommendation is accepted, NHL win probability should be marked
**deferred**, not merely “at risk.”

## Effort read

- Boxscore + Matchup: **M** — one `/boxscore` call, map skaters/goalies/teamStats.
- Plays: **S–M** — one `/play-by-play` call, map to `PlayByPlayEvent`, derive linescore.
- Three Stars: **XS** — landing feed, final games only.
- Win Prob: **L** — new model + tuning + timeline replay.

NHL is heavier than NBA for the two-week window. Budget it as:
**boxscore + plays + matchup ship; win prob defer unless schedule opens up**.