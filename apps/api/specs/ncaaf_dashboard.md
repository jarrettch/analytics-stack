# NCAAF Dashboard

_Created: 2026-09-18_

NCAAF v1 follows the existing multi-sport dashboard contract and mirrors the NFL
card shape. It differs from every other sport in one structural way: **odds come
from ESPN, not SportsGameOdds.** That choice keeps NCAAF off the SGO entity
budget entirely, which matters because the budget is already ~53% consumed
before college football is added.

## Why ESPN for odds

Measured on 2026-09-19 (Week 4 Saturday), comparing candidate sources:

| | ESPN | SharpAPI free | SGO |
|---|---|---|---|
| Games with a line | **70 of 71** | 58 | — |
| Books | DraftKings | DraftKings + FanDuel | BetOnline |
| Join cost | **none — odds are inside the game object** | ~10-entry alias map (108/117 exact on `team.location`) | tricode map |
| Monthly cap | none | none (12 req/min) | **2500 entities, ~53% used** |
| Requests per run | **0 extra — same call as the scoreboard** | 3–6 | 1 |

ESPN wins on the two things that matter here: it has more priced games on game
day, and its odds arrive already keyed to the game, so there is no name join to
maintain across 136 teams.

The tradeoff accepted: **one book, and pregame only.** ESPN drops the `odds`
block the moment a game kicks off. A line not captured before kickoff is
unrecoverable, which is the whole reason NCAAF persists rather than reading
through.

Revisit if ESPN's `close` prices turn out not to move during the week. SharpAPI
is the fallback, and its two books would allow cross-book de-vigging with the
existing `de_vig_ml`.

## Data sources

| Need | Source | Notes |
|---|---|---|
| Slate / live scores | ESPN site scoreboard JSON | `dates=YYYYMMDD&groups=80&limit=200`. `groups=80` scopes to FBS. Keyless but undocumented — isolate behind router helpers and retain stale cache, same as NFL. |
| Odds | **Same ESPN scoreboard response** | Select from `competitions[0].odds[]` by `provider.id == "100"` — never index zero. Carries `moneyline`, `pointSpread`, `total`, each with both `open` and `close`. Absent once a game starts. |
| Records | ESPN scoreboard competitor | `records[]` where `name == "overall"`, read inline off the competitor. No second call. |
| PF / PA / streak | ESPN college-football standings JSON | Provides `pointsFor`, `pointsAgainst`, `pointDifferential`, `streak`, `overall`. |
| Rankings | ESPN scoreboard competitor | `curatedRank.current`; **99 means unranked.** |
| Conference | ESPN scoreboard competitor | `team.conferenceId`. |
| Logos | ESPN CDN | `team.abbreviation.toLowerCase()` as the slug, matching the NFL pattern. |

### Never trust the odds array's index or the provider name

The extractor must **select** the DraftKings entry, not assume it, and must
match on **`provider.id == "100"`** rather than on `provider.name`.

Both halves of that are load-bearing, and the second is not hypothetical:

| Feed | `provider.id` | `provider.name` |
|---|---|---|
| college-football | `100` | `"Draft Kings"` |
| nfl | `100` | `"Draft Kings"` |
| mlb | `100` | **`"DraftKings"`** |

The same provider is spelled two different ways across ESPN's own endpoints. A
name match would work for NCAAF today and break silently the moment
`fetch_espn_odds` is pointed at another sport — exactly the reuse the helper
exists to enable.

The odds array happens to have length 1 in every sample taken, which is
precisely why index zero is dangerous: it works until it doesn't, and nothing
announces the change.

**If no entry has `provider.id == "100"`, log it and treat the game as having no
fresh line.** Falling back to whichever provider occupies index zero would
persist another book's price labeled `bookmaker: 'draftkings'` into an
append-only history table — a wrong row that cannot be deleted, and that looks
correct forever after.

## Team identity

ESPN abbreviations are unique across the slate — verified 160 unique of 160
competitors on 2026-09-12 — so **no 136-team tricode map is required.**

`matchup_key` is `AWAY@HOME` using ESPN abbreviations, matching the format every
other sport already uses. ESPN's numeric `team.id` values are additionally
stored inside the `odds_data` JSON payload.

The numeric ids are the hedge: `odds_snapshots` is append-only with a truncate
guard, so a `matchup_key` written today can never be rewritten. College
abbreviations do shift on rebrands and conference moves. Keeping the ids in the
payload means identity stays reconstructable without diverging the key format
for one sport.

## Team context

`NCAAFTeamData` extends the `NFLTeamData` shape with two college-only fields
that the dashboard filters require:

```ts
export interface NCAAFTeamData extends BaseTeamData {
  score: number
  pointsFor: number | null
  pointsAgainst: number | null
  yardsPG: number | null
  yardsAllowedPG: number | null
  turnoverDiff: number | null
  rank: number | null        // curatedRank.current, normalized: 99 -> null
  conferenceId: string       // ESPN team.conferenceId
}
```

`BaseTeamData` already carries `conference` as a display string; `conferenceId`
is added because a filter needs a stable key rather than a label. `rank` has no
equivalent anywhere in the existing types.

Without these two fields the filters described below cannot be built from the
payload, which is why NCAAF gets its own team type rather than reusing
`NFLTeamData` outright.

`NCAAFMatchupData` must also be declared outright. It **cannot extend**
`NFLMatchupData`, because that interface pins `sport` to the literal `"nfl"` and
its `away`/`home` to `NFLTeamData` — both of which NCAAF has to change, and
neither of which an `extends` clause can narrow:

```ts
export interface NCAAFMatchupData extends BaseMatchupData {
  sport: "ncaaf"
  gameStatus: number
  period: number
  gameClock: string | null
  away: NCAAFTeamData
  home: NCAAFTeamData
  week: number
  weekLabel: string
  seasonType: string
  statsSeason: number
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
}
```

And it must be added to the union, or nothing on the dashboard will accept it:

```ts
export type MatchupData =
  | NBAMatchupData | NFLMatchupData | MLBMatchupData
  | NHLMatchupData | WNBAMatchupData | NCAAFMatchupData
```

Field-for-field this mirrors `NFLMatchupData` (`sports-types.ts:112`) apart from
the discriminant and the two team fields. That duplication is the existing
pattern in this file — WNBA duplicates NBA the same way — and is preferable to a
generic, since the discriminated union is what makes the per-sport narrowing in
`MatchupCard` work.

Note that NFL's `yardsPG`, `yardsAllowedPG`, and `turnoverDiff` are hardcoded
`None` at `nfl.py:409-411` and have never been populated — so NCAAF has no
capability gap against NFL, and the same three fields stay nullable here.

v1 uses:

- current record from the scoreboard competitor;
- PF/G and PA/G from standings;
- standings streak and conference;
- `curatedRank.current`, normalized so 99 becomes `null`;
- nullable yards/game, yards allowed/game, and turnover differential.

Missing metrics render `--`, never fabricated league averages.

The card must not claim SP+, FPI, or any other proprietary rating. The model
label is `MARKET (DK)` — naming the single book out loud, because one-book
pricing is a real caveat for anyone reading the number.

## Slate behavior

- `GET /api/ncaaf/today` anchors on **`slate_date_et()`** (`utils.py:75`), not
  the calendar date. The slate day runs to **6am ET**, so the previous day's
  board stays up past midnight.

  This is not optional for college football — it is the sport that needs it
  most. A 10:30pm ET kickoff on the West Coast runs past 1am ET routinely, and a
  calendar-day anchor would drop that game from the board **while it was still
  being played**, taking its live score with it and leaving its persisted odds
  stranded against a date nothing queries.

  Collection stays on the calendar date, matching the rule in `slate_date_et`'s
  docstring: the rollover decides which slate to *display*, never which date to
  fetch or persist.
- **Only games with a line are returned** — where "has a line" means **fresh
  ESPN odds _or_ a persisted value**, not fresh odds alone.

  This distinction is load-bearing. ESPN removes the `odds` block at kickoff, so
  a filter on fresh odds only would make every game **vanish from the board the
  moment it started** and never return, taking live scores and finals with it.

  The route must call `load_persisted_odds(table_name="ncaaf_odds")` and merge
  before filtering — the same load-then-merge order `fetch_sgo_odds` already
  uses. A game that was priced pregame stays on the board through its final
  whistle, showing the last line captured before kickoff.

  **`load_persisted_odds` raises on failure.** It retries three times with
  backoff and then raises `RuntimeError` rather than returning a partial map —
  previously it swallowed per-date errors, so a Supabase outage was
  indistinguishable from "no odds today." `/api/ncaaf/today` must therefore
  return **503** when that load fails, matching what `mlb.py` and `wnba.py` now
  do, rather than assembling a board that silently drops every game for failing
  the has-a-line filter.

  Coverage is ~70/71 on game day, so the filter drops very little, and "has a
  market" is a better relevance cut than "has a ranking" for a board built
  around market prices.
- `week.number` and `season.type` come off the event, the same shape NFL uses,
  so `NCAAFMatchupData` carries `week`, `weekLabel`, `seasonType`, and
  `statsSeason` identically.
- Status normalizes to `gameStatus`: 1 preview, 2 live, 3 final.
- Kickoffs run Thursday through Sunday, midweek in November, and daily through
  bowl season. There is no fixed game-day assumption anywhere in the router.

## Filtering

Conference and ranked-involved filters are **client-side toggles only**. Both
read the `conferenceId` and `rank` fields on `NCAAFTeamData`, so this is
filtering an array already in the browser — no endpoint, no parameter, no
backend work beyond emitting those two fields.

Spread-magnitude filtering is deliberately excluded. "Close spread" and "good
bet" are not the same claim, and encoding a betting opinion into a display
filter is not v1's job.

## Persistence

NCAAF is a full odds-history sport.

- Latest values upsert to `ncaaf_odds`, mirroring `nfl_odds`.
- Changed pregame observations append to `odds_snapshots` with
  `provider = 'espn'` and `bookmaker = 'draftkings'`.

Persistence is not optional here. ESPN's pregame-only behavior means a game's
line vanishes from the API at kickoff; uncaptured is unrecoverable.

ESPN's `open` and `close` prices ride along in every capture at no extra cost —
one observation carrying two prices. See Normalized `odds_data` below for how
they are stored and why they are not two separate snapshots.

## Normalized `odds_data`

ESPN's odds object is shaped nothing like SGO's flat `oddID` keys, so
`fetch_espn_odds` normalizes to the **same `odds_data` contract every other
sport already writes** — no new fields, so `MatchupCard` and Slate Prep consume
it unchanged.

| `odds_data` field | ESPN source | Notes |
|---|---|---|
| `homeMoneyline` | `moneyline.home.close.odds` | string with sign, e.g. `"-205"` |
| `awayMoneyline` | `moneyline.away.close.odds` | |
| `homeSpread` | `pointSpread.home.close.line` | e.g. `"-3.5"` |
| `awaySpread` | `pointSpread.away.close.line` | |
| `homeSpreadOdds` | `pointSpread.home.close.odds` | |
| `awaySpreadOdds` | `pointSpread.away.close.odds` | |
| `overUnder` | `total.over.close.line` | **strip the leading `o`** — `"o50.5"` → `50.5` |
| `overOdds` | `total.over.close.odds` | |
| `underOdds` | `total.under.close.odds` | |
| `homeWinProbability` | derived | `calculate_win_probability_from_odds(...)` |
| `awayWinProbability` | derived | |

Every value arrives as a **string**, some sign-prefixed and some letter-prefixed
(`"+170"`, `"o50.5"`, `"u50.5"`). Parse with the existing `to_num` after
stripping any leading `o` / `u`.

**Win probability has no `fairOdds` shortcut here.** ESPN publishes raw book
prices only, so probabilities come from the moneyline pair via
`calculate_win_probability_from_odds`, which de-vigs internally — the same path
`fetch_odds_api_odds` already takes for WNBA.

### Close versus open

**`close` is the value. `open` is a fallback, never a competing snapshot.**

- Display and persistence both read `close`.
- If a `close` leg is missing, fall back to that leg's `open`.
- If both are missing, fall back to the persisted value, matching
  `fetch_sgo_odds`'s rule that a temporarily off-board line must not wipe the
  prior one.

Opening prices are carried as **additional fields inside the same
`odds_data` bundle** — `openHomeMoneyline`, `openHomeSpread`,
`openOverUnder`, and so on — not as separate `odds_snapshots` rows.

This is deliberate. ESPN gives no capture timestamp for the opening price, so a
separate observation would need a **fabricated `captured_at`**, and
`odds_snapshots` is append-only: a made-up timestamp is permanent and
indistinguishable from a real one. Two named fields in one honestly-timestamped
row say the same thing without inventing history.

An earlier draft called `open`/`close` "two free points of line movement." That
overstated it — it is one observation carrying two prices, which is useful but
is not two observations.

## Endpoints

```text
GET  /api/ncaaf/today
POST /api/ncaaf/cron/fetch-odds?offset=0|1
```

The cron endpoint requires `Authorization: Bearer $CRON_SECRET` and returns 401
without it, or 503 when the server has no `CRON_SECRET` configured.

No `/api/ncaaf/game/{id}` in v1 — see Deferred.

## Cron cadence

**Its own workflow file, not a step in `fetch-odds.yml`.** That file is the SGO
entity-budget surface; mixing a zero-cost ESPN sport into it makes the thing you
have to reason about during a quota scare larger for no benefit. Separate file,
separate failure domain.

Daily, twice:

- morning ET, catching the full day's board pregame;
- evening ET, catching late kickoffs.

The evening run will find that early games have no `odds` block, since ESPN
drops it at kickoff. Nothing new is captured for those games, which is correct —
the pregame-only rule the other sports apply rejects started events anyway. They
remain on the board via their persisted values, per the merge rule above.

The practical consequence: **the morning run is the only chance to price an
early kickoff.** If it fails, that game has no line for the rest of the day and
no line in history, permanently. Treat a failed morning run as worth
re-dispatching rather than waiting for evening.

Daily rather than Saturday-only because the season runs Thursday through Sunday,
adds midweek games in November, and goes daily during bowls. There is no entity
cost to argue against it.

## Shared extraction

`fetch_espn_odds()` lives in its **own module, `routers/espn_odds.py`** — not in
`utils.py` — and is **wired only to NCAAF**.

An earlier draft put it in `utils.py` alongside `fetch_sgo_odds`. The
provider-client-per-module pattern is the one that actually emerged: SharpAPI
shipped as `routers/sharp_odds.py` (`49f173f`), and it reads better than a fourth
provider crammed into an 800-line shared module. It also keeps NCAAF's new code
off the file every other sport touches, which matters when unrelated odds-feed
work is in flight.

Keep the signature parallel to `fetch_sgo_odds` regardless — the point of a
generic extractor is that switching a sport's provider stays a small change.

Written generic because the extractor is the same code either way, and because
ESPN carries DraftKings odds for MLB too — verified 15/15 games on a future
date. If the SGO entity budget runs dry mid-NBA-season, switching a sport to a
free fallback should be a one-line change rather than a project.

Wiring it into the other five sports now is explicitly **not** part of this
work: those sports depend on SGO's de-vigged `fairOdds` in the win-probability
path, and changing that is its own decision.

## Dashboard wiring

- `ncaaf` joins the `Sport` union, `SPORT_LABELS`, `SPORT_MODELS`, and
  `LIVE_SPORTS`.
- `SPORT_MODELS.ncaaf = "MARKET (DK)"`.
- The tab strip gets `overflow-x-auto` with no wrap, rather than becoming a
  dropdown. `visibleSports` already filters to sports with live slates, so the
  realistic worst case is four or five tabs most days. A dropdown would hide
  which sports have games — information the current design gives for free.
  Revisit at seven tabs, not before.
- Watch the auto-select fan-out: it becomes six parallel cold-Render requests,
  and NCAAF Saturdays return the largest payload on the board. If load degrades,
  the fix is a cheap slate-count endpoint, not trimming sports. Do not
  pre-solve it.

### `MatchupCard` branches that must be widened

Registering the type is **not** enough. `MatchupCard` gates behavior on
`sport === "nfl"` in four places, and each fails differently and quietly:

| Location | Current gate | Symptom if untouched |
|---|---|---|
| `TeamStats` switch (`MatchupCard.tsx:415`) | `case "nfl"` | `default: return null` — **no team stats render at all** |
| Week label (`:941`) | `matchup.sport === "nfl"` | `weekLabel` never displays |
| Score section (`:1042`) | five-sport `\|\|` chain excluding `ncaaf` | a **live or final game shows projected scores instead of the actual score** |
| Ratings row (`:1167`) | `matchup.sport === "nfl"` | `NFLRatings` never renders |

All four take `ncaaf` alongside `nfl`, reusing `NFLTeamStats` and `NFLRatings`
directly — the team shape is identical apart from the two added fields.

The score-section case is the one worth care: it is the only failure that
produces a *plausible-looking wrong answer* rather than an obviously missing
element. A live NCAAF game would sit on the board showing model projections
styled as though they were the score.

**Do not** widen the detail-link gate at `:949`. NCAAF has no detail page in v1,
so `ncaaf` is deliberately excluded there.

### Slate Prep changes

`prep/page.tsx` needs more than `SUPPORTED_SPORTS`:

- **`emptyLoadStates()` (`:58`)** returns `Record<Sport, LoadState>`, so adding
  `ncaaf` to `Sport` makes this a compile error until an `ncaaf` key is added.
  That is the good case — it fails loudly.
- **`normalizeGame` (`:74`)** discards `weekLabel`; `PrepGame` has no week
  field. Add one, populated for `nfl` and `ncaaf`, or accept that prep output
  carries no week context for either.
- **`groupHeading` (`:152`)** has **only an NFL _preseason_ branch** — there is
  no week-label branch to reuse. An earlier draft of this spec claimed
  otherwise; that was wrong. Either add a week-label branch (which also improves
  NFL) or let NCAAF fall through to the plain `SPORT_LABELS[sport]` heading.

  Falling through is acceptable for v1. A ~40-game college board grouped under a
  bare "NCAAF" heading is still the thing worth having; the week number is
  garnish.

`formatGame` needs no change — NCAAF carries spread, total, and moneyline in the
same fields NFL uses, and the `sport === "mlb"` branch for run lines is
untouched.

## Migration

One migration adds both `'ncaaf'` and `'ufc'` to the `odds_snapshots` sport
CHECK constraint, and creates `ncaaf_odds` and `ufc_odds` mirroring `nfl_odds`
exactly — **including its `(game_date, matchup_key)` primary key, with no
`bookmaker` column.**

That last point is load-bearing for UFC, which carries two books. Since the
primary key cannot express a bookmaker, `ufc_odds` bundles both books inside one
`odds_data` JSON row rather than writing two rows that would silently overwrite
each other on upsert. See `ufc_page.md` → Persistence. The upside is that both
tables stay readable by the existing `load_persisted_odds` with no per-sport
loader.

Both sports in one migration because otherwise two migrations touch the same
constraint within a week, and constraint churn on an append-only table with a
truncate guard is exactly the kind of thing that fails at the worst moment.

The CHECK constraint stays. It is the only thing standing between a typo in a
new router and orphaned rows that cannot be deleted.

## Deferred

- game detail page and `/api/ncaaf/game/{id}` — college box-score mapping is its
  own job, and the detail view is where most per-sport work lives
- SP+/FPI-style ratings and ranks
- FCS games and `groups` beyond 80
- second book via SharpAPI, and cross-book de-vigging
- yards/game, yards allowed/game, turnover differential (shared with NFL)
- injuries, weather, spread-magnitude filtering
