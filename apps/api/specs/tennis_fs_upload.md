# Tennis PrizePicks Upload

_Created: 2026-11-02 (scope; not yet implemented). Updated 2026-11-06: Total
Games and Total Games Won, mixture fit, strategy-rule flags, live-row filter.
2026-11-09: measured bias, tour-specific bias check, stray DK set total; then
review pass: alt-line pulls, refresh lifecycle, immutable runs, event-scoped
matching, fit acceptance, unsupported formats, result grouping._

Price a PrizePicks tennis board pasted or dropped into the site, from any
device, instead of from scripts on the Mac. Three stat types are priced:
Fantasy Score (FS), Total Games (match) and Total Games Won (player). Today the
flow is: save the PrizePicks projections JSON in a browser, pull SharpAPI odds
with `sports/projections/scripts/pull_sharp_tennis.py` (plus a separate
`player_games_won` pull), run `tennis_fs.py` and `tennis_games.py`, read two
CSVs. Every step needs the laptop. This spec moves the odds pull and both
models onto the API and puts a page in front of them.

Tennis goes first because it is where the model adds the most: PrizePicks
standard lines in NBA/NFL/CFB sit on the book main line (measured
2026-10-26 to 11-02), while the tennis FS model's standard leans of 60% or
more have held up. The 10-31 board went 5 of 7; the 11-05 top three went 2 of
3 (the miss, Linette o17, scored 16 after 4 double faults).

Total Games standards, by contrast, have sat on DK's main total on every board
since they were added (11-05, 11-06 ×2): they price at 50–54% and are rarely
the play. The value of pricing them is the goblins and the occasional
standard that PrizePicks sets a game off DK (Van Assche games won o10.5 vs DK
11.5, 11-06).

## Why upload, not a folder watcher

| | Folder watcher (local) | Upload page |
|---|---|---|
| Works away from the Mac | no | **yes** |
| Needs the Mac awake | yes | no |
| Where the model runs | Mac | API |
| Where odds come from | local pull | API, cached in Supabase |
| History for grading | local CSVs | `tennis_runs` table |

The board itself still has to come from the user's own logged-in browser.
PrizePicks' API sits behind DataDome. **Nothing in this feature fetches
PrizePicks**: no server-side request, no headless browser, no iOS Shortcut
calling the URL. The user opens the projections URL, copies or saves the
JSON, and hands it to the page.

## Scope

v1:

- `/prep/tennis` page: paste or drop a board, see the model's table.
- Browser-side extraction of the tennis rows (below), so the upload is small.
- `POST /tennis/runs` and `GET /tennis/runs/{id}` on the API.
- On-demand SharpAPI refresh for ATP/WTA when the cached odds are stale.
- `tennis_fs.py` and `tennis_games.py` ported into `apps/api` as one tested
  module.
- `tennis_odds_snapshots`, `tennis_odds_refreshes` and `tennis_runs` tables.
- An optional main-line filter on the shared `fetch_sharp_odds` (Odds).

Out of scope for v1: grading, NBA/NFL/CFB/EPL boards, slip/EV math, payout
entry, notifications, and every other tennis stat type (below).

### Stat types not priced

Aces, Double Faults and Break Points Won are skipped. The strategy notes skip
them because PrizePicks posts them as goblins/demons at reduced payouts (e.g.
2.25x). That holds for Aces (1 standard of 60 on the 11-05 board) but not the
other two (Break Points Won 10 standard of 34, Double Faults 6 of 11). The
reason that covers all three is that there is no book line to price them
against. SharpAPI has no break-point or double-fault market. DK's "Break On
Opponent's Serve" (the mislabeled `player_total_games`) runs about twice PP's
Break Points Won (Cirstea 9.5 vs 4.5, Ruzic 6.5 vs 2.5, 11-05), probably
break-point chances against conversions. Unconfirmed, so don't pair them. 1st Set Total Games, Total Sets and Total Tie
Breaks have no model yet (the match distribution could price all three; see
Later). `Aces` rows are still extracted, as model input only.

## Flow

```
phone/laptop browser                 API                              Supabase
--------------------                 ---                              --------
open PP projections URL
(league_id=5), copy JSON
paste/drop on /prep/tennis
parse in browser, keep tennis FS, TG, TGW + Aces rows
POST /tennis/runs  --------------->  validate payload
                                     snapshot fresh: price on it
                                     stale: price on it, flag rows,
                                       start or join a refresh ----->  tennis_odds_refreshes
                                     none usable: run is pending
                                     store run -------------------->  tennis_runs
                      <------------  run (ready | pending), refresh state
poll GET /runs/{id}                  refresh lands ---------------->  tennis_odds_snapshots
                                     reprice waiting runs as new
                                       revisions ------------------>  tennis_runs
                      <------------  newer_run_id; page loads it
```

See Refresh lifecycle for the states and failure cases.

## Odds: on-demand refresh, not a scheduled collector

The other collectors run as pg_cron → `ops.dispatch_workflow` → GitHub
Actions → `curl` to the API. Every tick costs Actions minutes, and the private
repo's 2,000 free minutes already bind collector schedules. A tennis collector
every 20–30 minutes around the clock (ATP/WTA play in every time zone) would be
the most expensive schedule in the repo, and most of its pulls would never be
read.

So v1 refreshes **on upload** (Refresh lifecycle, below).

Refresh = `fetch_sharp_odds(league, market, sweep=..., main_only=...)` from
`routers/sharp_odds.py` for leagues `atp`, `wta` and markets:

| Market | Books | `main_only` | Used for |
|---|---|---|---|
| `moneyline` | DK, FD | true | p_win (de-vigged, averaged) |
| `total_games` | DK main + alternates | **false** | fit target (main); ladder check at PP lines (alternates) |
| `game_spread` | DK main | true | spread check, rule-of-thumb FS |
| `player_aces` | FD ladder (over-only) | **false** | ace rate |
| `player_games_won` | DK main only, both sides | true | Total Games Won book line |

`fetch_sharp_odds` hard-codes `is_main_line=true` today, which drops the alt
total ladder and every FD ace rung but the one FD flags main (often a tail
rung; see Model module). Add a keyword-only `main_only: bool = True`; with
`False` the parameter is omitted. Existing callers don't change. The local
`pull_sharp_tennis.py` never sent the filter, so the row counts below already
include the ladders.

`total_games` also carries a stray set-sized DK market, always 11.5, with its
own `market_id`. It is usually flagged alternate, but on 11-07 it was the only
`is_main_line` row for Michelsen–Hanfmann and took over that match's fit. Drop
total lines under 15 (no best-of-3 match total is that low). When no main
remains, use the ladder rung whose de-vigged price is closest to even.

Do **not** pull `player_total_games`: despite the name, SharpAPI's DK rows for
it are "Break On Opponent's Serve" (`player_name` says so; checked 11-05).

Measured: 1,064–1,327 rows across 51–60 events per pull before
`player_games_won` (28–60 rows more on 11-05/11-06). At the free tier's 12
requests/min (the client paces at 5 s), the ten market×league sweeps take
about 1.5–2.5 minutes. Data is 60 s delayed.

### Refresh lifecycle

`tennis_odds_refreshes` has one row per refresh: `status` (`running`, `done`,
`failed`), `started_at`, `finished_at`, `snapshot_id`, `error`. A partial
unique index on `status = 'running'` allows one running refresh at a time.

On `POST /tennis/runs`:

| Newest snapshot | Run | Refresh |
|---|---|---|
| under `STALE_AFTER` (20 min) | `ready`, priced on it | none |
| older, but under `MAX_SNAPSHOT_AGE` (6 h) | `ready`, priced on it, every row `stale_odds` | start one, or join the running one |
| none, or older than `MAX_SNAPSHOT_AGE` | `pending`, no prices | start or join |

`MAX_SNAPSHOT_AGE` is a guess, like `STALE_AFTER`. Past it, the matches on the
board have mostly changed and stale prices mislead more than they help.

- The request returns at once. The refresh runs as an `asyncio` task in the
  API process, and the run records its `refresh_id`. A second upload during a
  refresh joins it (its run gets the same `refresh_id`); no request waits.
- A refresh is all ten league × market sweeps or nothing. If any sweep raises,
  the refresh fails and writes no snapshot. `fetch_sharp_odds` already refuses
  to return a partial market, and a snapshot is only used whole. An empty
  market is not a failure; the WTA season ends in November.
- On success the task inserts the snapshot, marks the refresh `done`, and
  prices every run with that `refresh_id` on it. Each reprice is a **new run**
  with `revises` set to the old run's id. A stored run never changes.
- On failure the refresh is `failed` with its error. Waiting runs keep their
  stale results, or stay `pending` with none. The page shows the error and a
  Retry button: `POST /tennis/runs/{id}/refresh` starts or joins a refresh and
  points the run's `refresh_id` at it.
- A `running` refresh older than `REFRESH_LEASE` (6 min, about twice the
  slowest measured pull) has lost its task to a deploy or restart. The next
  request that sees it marks it `failed` (`abandoned`), which frees the index.

Hosting: the API is a Render web service, a long-lived process, so the task
outlives the response; deploys and restarts kill it, which the lease covers.
A free instance sleeps after 15 minutes without requests, but the page polls
during a refresh, which keeps it awake.

Pricing is CPU-bound pure Python. On the Mac the scripts take 3.3 s (FS) and
7.3 s (Total Games and Games Won, which reruns the FS script) for the 11-08
board. Run `price_board` in `asyncio.to_thread` so the event loop keeps
serving the dashboard. Fit each event once per snapshot, cached on
`(snapshot_id, event_id)`, so repeat uploads against one snapshot don't refit.
**Benchmark on Render before building the page.** If the 11-05 board takes
more than about 30 s there, move pricing to a process pool or loosen the fit's
bisection counts.

Revisit: if waiting 1–2 minutes on a stale board becomes the common case, add
a pg_cron job limited to the hours the user actually uploads, priced against
the Actions budget first. Or call the API from pg_cron directly with
`net.http_post` and a Vault-held bearer token, which skips Actions entirely.

## Browser-side extraction

PrizePicks tennis boards are 0.2–1 MB (NBA about 10 MB). The page parses the
JSON in the browser and sends only what the models read:

| Field | From |
|---|---|
| `player` | `included[type=new_player].attributes.display_name`, via `relationships.new_player.data.id` |
| `opponent` | `data[].attributes.description` |
| `stat` | `data[].attributes.stat_type`: keep only `Fantasy Score`, `Total Games`, `Total Games Won`, `Aces` |
| `odds_type` | `standard` / `goblin` / `demon` |
| `line` | `line_score` |
| `start_time` | `start_time`. **Eastern with an offset, not UTC** |
| `status` | `status` |
| `projection_id` | `data[].id` |
| `game_id` | `relationships.game.data.id`: PrizePicks' match id, shared by both players' rows |

Keep only rows whose `relationships.league.data.id` is `5` (tennis). NBA and
other boards carry `Fantasy Score` too, so the stat name alone would admit
them. If no rows are tennis, the page says so and sends nothing.

Drop rows whose `status` is not `pre_game`, and doubles (player name contains
`/`). The server rejects any row whose `status` isn't `pre_game`. That guards
against a page bug, not a hostile client, which could send anything. **Do not filter on `is_live` / `in_game`**: PrizePicks sets both true on
pre-match Total Games, Total Games Won and Total Sets rows that are also
offered live (47 such rows on the 11-05 board, all `status: pre_game`).
Filtering on them would drop most Games Won standards.

`Total Games` is one line per match but may arrive under either player; the
server dedupes on (`game_id`, `odds_type`, `line`).

`Aces` rows are kept because the FS model uses PrizePicks' own ace
goblin/demon ladder when FanDuel has no `player_aces` for that player
(`tennis_fs.py`: `PP ladder ~x`).

Capture on a phone: open the projections URL in Safari or Chrome while signed
in, then **Select All → Copy** into the page's textarea, or **Share → Save to
Files** and pick the file. Confirm early that mobile Safari can select and copy
a 1 MB JSON page; if not, the file route is the fallback.

## API

### `POST /tennis/runs`

Auth: `Authorization: Bearer <PREP_UPLOAD_SECRET>`, checked like
`require_cron_auth` in `routers/auth.py`: fail closed (503) when the env var is
unset, `secrets.compare_digest` on the token. A separate secret, not
`CRON_SECRET`: this one lives in a browser.

Body:

```json
{ "board_saved_at": "2026-10-31T00:39:00-07:00",
  "rows": [ { "player": "Jiri Lehecka", "opponent": "Jan-Lennard Struff",
              "stat": "Fantasy Score", "odds_type": "demon", "line": 23,
              "start_time": "2026-10-31T12:00:00-04:00", "status": "pre_game",
              "projection_id": "7712345", "game_id": "202852" } ] }
```

Validation (pydantic). The payload is untrusted. It comes from a third-party
page via the clipboard.

- At most 1,000 rows and 256 KB.
- `stat` in {`Fantasy Score`, `Total Games`, `Total Games Won`, `Aces`},
  `odds_type` in {`standard`, `goblin`, `demon`}, `line` a finite number in
  [0, 60].
- Names: at most 80 characters, Unicode letters, spaces, `.`, `'`, `-` only.
  They are matched and displayed, never interpolated into queries or HTML.
- `start_time` must parse and fall within 4 days of now (WTA Finals lines were
  posted 3+ days out on 11-06).
- `status` is `pre_game`. `projection_id` and `game_id` are digit strings of
  at most 20 characters.

Response: `{ run_id, status, board_saved_at, odds_snapshot_at, odds_age_s,
refresh: { id, status, started_at, error } | null, results[] }`. `status` is
`pending` or `ready`.

### `GET /tennis/runs/{id}`

Same auth. Returns the run as stored, its refresh's current state, and
`newer_run_id` once a revision exists. It never reprices: an old run opened
next week shows what it showed then. The page follows `newer_run_id` while it
is polling.

### `POST /tennis/runs/{id}/refresh`

Same auth. Retry after a failed refresh: starts or joins one and points the
run at it. Returns the same shape as `GET`.

### Result row

One row per PrizePicks line. Every stat shares the first block; the rest
depend on `stat`.

| Field | Meaning |
|---|---|
| `stat`, `player`, `opponent`, `tour`, `start_time`, `odds_type`, `line`, `projection_id`, `game_id` | from the board |
| `event_id` | the matched SharpAPI event, or empty |
| `p_win` | the row player's de-vigged moneyline, averaged over DK/FD |
| `fav_ml` | shortest moneyline price in the match (blowout rule) |
| `total` | DK main total games line |
| `fit` | `iid`, `mix s=<x>`, or `ml_only` (Model) |
| `model_mean` | mean of the priced quantity (FS, match games, or player games) |
| `p_over`, `p_under` | at the line, **conditional on no push**: P(over \| not exactly the line). They sum to 1 |
| `push` | P(landing exactly on the line), shown beside them whenever above 0. FS moves in half-points, so most FS lines can push; so can whole-number Total Games and Games Won lines |
| `lean` | the side above 50%, or none. Goblin/demon rows can only lean over (below). A direction, not a value call: without payouts, 55% on a goblin and 55% on a standard aren't comparable (Open questions) |
| `unpriced_reason` | set when the row has no probabilities: `no_book_line`, `ambiguous_match`, `unsupported_format` |

FS only:

| Field | Meaning |
|---|---|
| `spread` | DK game spread for the player |
| `aces_src` | `FD <line> <odds>`, `PP ladder ~x`, or `assumed <rate>` |
| `model_median` | FS distribution |
| `p_under_bias` | P(under) with the line moved `BIAS_SHIFT[tour]` against it (Bias check) |
| `rule_fs` | the strategy notes' rule of thumb, for reference only: favorite `13 + |spread| + 0.5 × aces`, underdog `7 − spread + 0.5 × aces`. Not a lean; shown so disagreement with the model is visible (Stefanini 11-05: rule 14.8 vs line 13, model under 0.56). The FS script's `system` column differs (Intended differences) |

Total Games only:

| Field | Meaning |
|---|---|
| `dk_p_over` | de-vigged DK P(over) at the PP line from the alt ladder; for a whole-number PP line, the two neighbouring half-lines (`lo/hi`). Empty when DK has no rung there |

Total Games Won only:

| Field | Meaning |
|---|---|
| `dk_gw_line`, `dk_gw_over`, `dk_gw_under` | DK `player_games_won` main line and prices |
| `dk_gw_p_over` | de-vigged at DK's line (not the PP line when they differ) |

Flags:

- `assumed_aces`: `aces_src` is the tour default. Provisional (Tagger 10-26
  read 86% on the default, then 60% once FanDuel posted her ladder). Big
  servers are badly served by the default: Rybakina u20 read 0.72 at 0.22
  aces/service game, 0.59–0.64 at a realistic 0.45–0.6 (11-05).
- `fails_bias_check` (FS): an under lean whose `p_under_bias` is below 50%.
- `ml_only` (TG/TGW): no DK total, so the match is fit to the moneyline alone
  at a tour-average serve level and typical mixture spread. The total is not
  market-pinned; show the row, never as a lean. On 11-06 these read 0.65 over
  on a PP line (Dzumhur 20.5) that the market almost certainly sets lower for a
  reason the moneyline can't see (clay Challenger, weak servers).
- `blowout` (TG): `fav_ml` −400 or shorter. The strategy notes' rule is under
  on these, but so far PrizePicks has already priced it in: Montgomery −650,
  PP 19.5 = DK 19.5 at 0.50 (11-05); Alcaraz −580, PP 21.5 = DK 21.5 at 0.50
  (11-06). Informational until a board shows PP above DK on one.
- `juice_over` / `juice_under` (TGW): DK's games-won side is −135 or shorter
  and the PP line is no worse than DK's. The strategy notes' rule; −140/+100
  de-vigs to only 0.538, so it marks a side, not an edge.
- `over_only`: goblin and demon rows can only be played over. Any under lean
  or `juice_under` on them is not playable and the page greys it out (most
  `juice_under` flags on 11-05/11-06 were 12.5 demons).
- `no_book_line`: no moneyline match (FS also needs a total). Row is shown,
  not priced.
- `ambiguous_match`: more than one book event, or both or neither
  participants, match the row (Matching). Shown, not priced.
- `unsupported_format`: the match isn't best of three (Supported formats).
  Shown, not priced.
- `fit_miss`: the fit failed its acceptance check (Fit acceptance).
  Probabilities shown, no lean.
- `stale_odds`: snapshot older than `STALE_AFTER`.
- `started`: `start_time` has passed, checked when the run is priced and again
  when the page renders. Never actionable; PrizePicks pulls pre-match lines at
  the start anyway.
- `doubles_skipped`, `not_pre_game_skipped` (run level): how many rows the
  page dropped.

## Model module

Port `sports/projections/scripts/tennis_fs.py` (argv read at import) and
`tennis_games.py` (which loads `tennis_fs.py` via `runpy`) to
`apps/api/tennis_pp/` with pure functions:

- `fit(p_win, p_over_total, total_line) -> (p_serve_a, p_serve_b)`
- `fit_dist(p_win, p_over_total, total_line, league) -> (dist, label)`: iid
  when it hits the total, else the strength mixture (below)
- `fit_ml_only(p_win, league) -> dist`: no DK total; c = `MIX_C[league]`,
  s = `ML_ONLY_S[league]` (atp 0.06, wta 0.07)
- `match_dist`, `mix_dist`, `set_dist`, `tiebreak`, `hold`: the serve-point
  model
- `ace_rate(...)`: NB aces (`ACE_K = 5`) fit to the FD rung **closest to
  even money**, not the one FD flags `is_main_line`. FD flags tail rungs as
  main (Medvedev 14.5 at +850 with 8.5 at −135 on the same ladder, 10-27).
- `fs_dist(...)`: FS = 10 + game diff + 3 × set diff + 0.5 × aces − 0.5 × DF
  (scoring confirmed in the app 2026-10-22; Poisson DF)
- `games_over(dist, line)`, `player_games_over(dist, side, line)`: read off
  the same distribution, push excluded
- `price_board(rows, snapshot) -> results`

One distribution per match, cached: FS, Total Games and both players' Games
Won all read from it, so the three stats can never disagree about how the
match goes.

### Mixture fit (added 2026-11-05)

The iid serve model can't reach WTA totals. A 71% favorite with DK total 20.5
bottoms out at P(over) ≈ 0.63 with c pinned at its 0.45 floor; every WTA match
on the 11-05 board pinned this way, and ATP matches occasionally do
(Alcaraz–Ruud 11-06). Real matches are more lopsided than fixed serve
probabilities allow. When the iid fit misses the DK total by more than 0.01,
the serve gap varies match to match: delta + s·z, z on a 3-point Gauss-Hermite
grid (±√3 at 1/6, 0 at 2/3), c fixed at `MIX_C` (atp 0.64, wta 0.56), and
(delta, s) solved to hit the moneyline and total. The result is insensitive to
c (0.50 vs 0.56 moves mean games by 0.1).

Evidence it is the right fix: the mixed fit reproduces DK's alt-total ladder at
lines it was not fit to (Montgomery o18.5: model 0.577, DK 0.567; iid gave
0.706). It changes the shape of the distribution more than its mean: across
52 graded WTA favorites it raised the expected base score by only 0.3 points
on average (`measure_fs_bias.py`, 11-09), though single matches move more
(Cirstea u21 on 11-04, won 7-5 6-1 with base 23, read 0.744 under iid and
0.650 mixed).

Not yet shown: that it grades better. The only graded board with a saved
snapshot (10-31) is mostly ATP and can't separate the two. `TENNIS_FIT=iid`
forces the old behaviour in the script for comparisons.

### Matching board rows to book events

The scripts match on names alone. Events are keyed on the two normalized names
with suffix matching, sides and ace ladders on the last six letters, and
games-won lines on the player across the whole pull. That works for a one-day
board but breaks on shared endings (normalized Anderson and Henderson both end
`derson`) and when a pull holds a player's next match too. The port matches in
two steps:

1. **Event.** A SharpAPI event (`event_id`) matches a PP `game_id` when both
   PP names match its two participants (exact normalized name first, then
   prefix or suffix as the scripts do) and its `event_start_time` is within
   12 h of PP's `start_time` (order-of-play times drift). No candidate:
   `no_book_line`. More than one: `ambiguous_match`, unpriced. Never take the
   first.
2. **Within the event.** Side, ace ladder and games-won line are looked up
   among that event's rows only, by the same name rule. A PP name matching
   both participants or neither is `ambiguous_match`.

Moneyline prices keep the scripts' exact-then-prefix rule inside the event: DK
and FD spell some players differently ("Daniel Merida" vs "Daniel Merida
Aguilar").

PrizePicks gives no tour, and SharpAPI's `league` (`atp`/`wta`) comes with the
matched event, so tour isn't a match key.

### Supported formats

The match model is best of three sets with a 7-point tiebreak at 6-6. Any
other match is `unsupported_format` and unpriced:

- best of five (men's Grand Slams, from the Australian Open in January): an
  ATP event whose `competition_ref.id` names a Slam, or any DK main total of
  30 or more.

A 10-point final-set tiebreak (all four Slams, both tours) still scores as one
game at 7-6, so games and FS barely change; WTA Slam matches stay supported.
`competition_ref` was missing on 276 of 685 rows in the 11-08 pull, so an
`ml_only` match with no competition id is assumed best of three. Best-of-five
support is in Later.

### Fit acceptance

`fit_dist` returns whatever its last bisection step gives; nothing checks it.
The mixture's search bounds also allow serve probabilities out of range: at
c = 0.64, delta at its 0.6 bound and s at its 0.35 cap give the top grid point
a serve probability of 1.24. Real fits sit far inside the bounds, but nothing
enforces it. After every fit, check:

- every serve probability used, across the mixture grid, is in (0, 1)
- the distribution sums to 1 within 1e-6
- the fit's P(win) is within 0.01 of the de-vigged moneyline
- the fit's P(over) at the DK main total is within 0.02 of DK's (skipped for
  `ml_only`)

A fit that fails any check is `fit_miss`: probabilities shown, no lean.

### Carry over the fixes already in the scripts

- FD `player_aces` is over-only: divide by `ONE_SIDED_MARGIN = 1.045`.
- Total lines under 15 are dropped; with no main left, the most even ladder rung
  stands in (Odds). This applies to FS too (see below).
- DK sometimes posts identical templated total ladders for near-even WTA
  matches (Shubladze–Liu and Eala–Sakkari, 11-06). Identical rows across two
  matches are expected, not a cache bug.

### Intended differences from the scripts

Where the port and the scripts disagree, the port follows this spec. The
parity tests compare everything else and list the rows these change as
expected diffs, so a parity pass can't lock in a script bug.

- **`rule_fs`.** The FS script's `system` column is `13 − spread + 0.5 × aces
  − 0.5 × expected DF` for both sides, with spread signed for the player. For
  favorites that matches the rule apart from the DF term; for underdogs it
  gives 13 − spread, not 7 − spread (Berrettini +1.5 on 11-04: script 16.4).
  The port uses the strategy notes' formula above, with no DF term.
- **Alt-total fallback in FS.** `tennis_games.py` falls back to the most even
  ladder rung when DK flags no main; `tennis_fs.py` doesn't and returns no
  book line. The port builds events once and both stats use the fallback.
- **Event-scoped matching** (Matching). Can change rows where a suffix match
  crossed players.
- **Fit acceptance** and **unsupported formats** are new.

### Tests

Fixtures saved from real inputs, committed under
`apps/api/tests/fixtures/tennis_pp/`:

- Parity, iid: the 10-31 board plus the saved SharpAPI pull reproduce
  `tennis_fs_model_10-31-26.csv` to 3 decimals with the mixture disabled.
- Parity, current: the 11-05 board plus `tennis_sharp_odds_11-05-26.json` and
  `tennis_sharp_pgw_11-05-26.json` reproduce `tennis_fs_model_11-05-26_late.csv`
  and `tennis_games_model_11-05-26.csv`.
- Mixture: a pinned WTA match hits its DK total within 0.01 and an off-main
  DK alt rung within 0.02.
- FD tail-main ladder picks the near-even rung.
- Name-mismatch moneyline still prices.
- Player with no FD ladder but a PP ace goblin/demon pair uses `PP ladder`.
- Player with neither gets `assumed_aces`.
- No DK total: FS gets `no_book_line`, TG/TGW get `ml_only`.
- `pre_game` rows with `is_live: true` are kept; non-`pre_game` rows and
  doubles are rejected client-side and again server-side.
- A board with no league-5 rows (an NBA board, which has Fantasy Score) is
  refused by the page.
- Total Games under both players' names prices once.
- An under lean on a goblin/demon is flagged `over_only`.
- Two events that both match a name pair: `ambiguous_match`. Games-won lines
  for Anderson and Henderson in different events don't cross.
- An ATP event with a Slam `competition_ref`, or a DK main total of 30 or
  more: `unsupported_format`.
- A fit pushed off its targets: `fit_miss`, no lean.
- `rule_fs` for an underdog uses 7 − spread.

Operational (`fetch_sharp_odds` mocked, as in `test_sharp_odds.py`):

- `fetch_sharp_odds` still sends `is_main_line=true` by default and omits it
  with `main_only=False`.
- Cold start: no snapshot gives a `pending` run with no prices; when the
  refresh lands, a revision with prices exists and `newer_run_id` points to it.
- Refresh failure: one sweep raises; no snapshot is written, the refresh is
  `failed` with its error, the run keeps its stale results, and the retry
  endpoint starts a new refresh.
- Concurrency: two uploads during one refresh make one refresh row, and both
  runs get revisions when it lands.
- Abandoned: a `running` refresh older than `REFRESH_LEASE` is marked failed
  and a new one can start.
- Reproducibility: `GET` on an old run after a newer snapshot lands returns
  the stored results unchanged.
- Started: a row whose `start_time` has passed is `started` and never in the
  actionable group.
- Snapshot cleanup deletes old unreferenced snapshots and keeps referenced
  ones.

## Bias check

Applies to FS under leans. A favorite's under can look strong only because
the model underrates how much favorites score. `p_under_bias` reprices the
under with the line moved `BIAS_SHIFT[tour]` points against it; an under lean
fails when that drops below 50%. Overs are not checked.

| Tour | `BIAS_SHIFT` | Why |
|---|---|---|
| WTA | 2.0 | WTA favorites scored 1.9 points above the model (SE 1.3, n 52) |
| ATP | 0.0 | ATP favorites scored 1.95 points *below* it (SE 1.2, n 57) |

Measured 2026-11-09 with `sports/projections/scripts/measure_fs_bias.py` over
119 graded player-matches (10-24 to 11-08): actual base (10 + game diff +
3 × set diff) minus the model's expected base. Pooled, the model is close to
unbiased (+0.4, SE 0.8), so the original flat 2-point shift (from the
WTA-heavy 10-24/10-25/10-31 boards) was the WTA effect read as a general one.
It is not a game-spread misfit: the model's spread cover probability matches
DK's on both tours. The ace part shows no bias (n 8, the close lines whose
aces were entered).

Each tour's effect is only 1.5–1.7 SE, and the graded leans don't confirm the
split yet: under the old flat check, rejected ATP unders went 2–3 and WTA
unders 3–1 (11-05 to 11-08). Underdogs are barely measured (n 10). Rerun
`measure_fs_bias.py` as boards are graded (about 90 s) and update
`BIAS_SHIFT` from it; the port should read it from config, not hard-code it.

Earlier record under the flat check: on 10-31 every passing lean hit
(Tagger, Lehecka, Machac, Zverev); on 11-05 the three passing overs went 2 of
3. Failing leans drop out of the page's actionable group (Page).

## Storage

Both tables: no `anon`/`authenticated` access. Only the API's service role
writes and reads, as with the props archives.

`tennis_odds_snapshots`

| column | |
|---|---|
| `id` | bigint identity |
| `pulled_at` | timestamptz |
| `rows` | jsonb: the flat SharpAPI rows for all five markets, as `pull_sharp_tennis.py` writes them |
| `row_count`, `event_count` | int |

A nightly job deletes snapshots older than 7 days **that no run references**.
Referenced snapshots stay, so any run can be repriced exactly under a later
model version. Snapshots are only written by a refresh, at most one per upload
session, and jsonb is compressed on disk; the 11-08 pull is 0.9 MB as raw
JSON. Revisit if the table passes ~200 MB. A snapshot is only used whole:
never mix rows from two pulls into one model run.

`tennis_odds_refreshes`: see Refresh lifecycle.

`tennis_runs`

| column | |
|---|---|
| `id` | uuid |
| `created_at` | timestamptz |
| `status` | `pending` or `ready` |
| `board_saved_at` | timestamptz (client-reported) |
| `snapshot_id` | fk → `tennis_odds_snapshots`; null while `pending` |
| `refresh_id` | fk → `tennis_odds_refreshes`: the refresh this run is waiting on, if any |
| `revises` | fk → `tennis_runs`: the run this one reprices, if any |
| `board` | jsonb: the validated rows |
| `results` | jsonb |
| `model_version` | text: git sha of the module (`RENDER_GIT_COMMIT` on Render) |
| `model_config` | jsonb: every tunable the run used (`BIAS_SHIFT`, `MIX_C`, `ML_ONLY_S`, `ACE_DEFAULT`, `DF_DEFAULT`, `ACE_K`, `ONE_SIDED_MARGIN`, `STALE_AFTER`, fit tolerances). Config changes independently of code |

A run's `results` and `snapshot_id` never change, `pending` runs included:
the refresh that prices one writes a revision. Only `refresh_id` moves, when
Retry points the run at a new refresh.

`board` is kept so v2 grading can settle every line, not just the ones played.
Grade the newest revision of each board.

## Page

`apps/visualize-ev/app/prep/tennis/page.tsx`:

Phone use is the point of this feature, so the phone layout comes first.

**Upload.** Textarea plus file picker/drop. Parse with `JSON.parse` in a
try/catch, extract the rows, and show "N Fantasy Score, M Total Games, K Games
Won lines; J doubles, L not pre-game skipped" before sending. Collapses to one
line once a run exists. Secret entered once, kept in `localStorage` (wrapped in
try/catch; the page works if storage is unavailable by asking again).

**Status line.** Two times, kept separate: "Board copied 14:02" and "Odds
13:40". Fresh odds don't make the board fresh: PrizePicks moves lines too, so
a board over 60 minutes old gets a banner to copy it again. While refreshing:
progress and elapsed time. On failure: the error and Retry.

**Filters.** Stat tabs (Fantasy Score default), odds type (Standard default;
Goblin, Demon, All), tour.

**Grouping and order.** Rows fall into groups, in this order:

1. Actionable: a lean that passes the bias check, on a playable side, not
   `ml_only` or `fit_miss`, odds fresh, aces not assumed, not `started`.
2. Provisional: would be actionable but for `stale_odds` or `assumed_aces`.
   `assumed_aces` has been badly wrong (Tagger 0.86 → 0.60, 10-26).
3. No lean, or not playable (`over_only` unders, failed bias check,
   `ml_only`, `fit_miss`).
4. Unpriced, with the reason in words ("no DK total", "two matches fit this
   name", "best of five").
5. Started.

Within a group, by P. With "All" types selected, group by odds type first:
without payouts, P on a goblin and P on a standard aren't comparable. The page
labels the order "by model probability", not as a ranking.

**Phone: cards.** Player vs opponent, local date and time, line and type,
lean and P, push when above 0, flags as chips (`assumed_aces`, `stale_odds`
and `fit_miss` visible without expanding). Tap to expand: book comparison (DK
P at line, DK games-won line and prices, fav ML), `p_under_bias`, `rule_fs`,
`aces_src`, model mean and median, fit label.

**Desktop: compact sortable table.** Columns per stat:

- FS: start (local), player, line, type, lean, P, push, P under (bias),
  rule FS, aces source, flags.
- Total Games: start, match, line, type, lean, P, push, DK P at line, fav ML,
  flags.
- Games Won: start, player, line, type, lean, P, push, DK line and prices,
  flags.

Render all strings as text, never as HTML.

## Later

- **Grading (v2):** a nightly job settles `tennis_runs` from ESPN set scores
  with `grade_tennis_fs.py` (FS) and `grade_tennis_games.py` (Total Games,
  Games Won) logic. Total Games and Games Won settle from set
  scores alone. ESPN has no tennis aces, so FS lines whose result depends on
  aces stay `needs_aces` until entered on the page. The morning grade has run
  before late matches finished (Cirstea 11-04 stayed "not final"); regrade
  unsettled rows the next night.
- **Per-player double-fault rates:** DF is a tour default (WTA 0.30/service
  game). Linette o17 lost by one point after 4 DFs (11-05). Tennis Abstract
  DF% × ~6.3 service points per game, as `tennis_history.py` suggests for
  aces, would replace it.
- **Per-player ace defaults** for when FD has no ladder, same source. The WTA
  Finals rows ran on the default for days before FD posted.
- **More stat types:** 1st Set Total Games, Total Sets and Total Tie Breaks
  read straight off the match distribution; SharpAPI has
  `1st_set_total_games` and `total_sets` to check against.
- **Best of five:** men's Slam matches are `unsupported_format` in v1. The
  serve model extends directly (first to three sets); the fit targets and the
  bias measurement would need redoing on five-set data.
- **Other sports:** same upload, `pp_vs_books.py` instead of the tennis model.
  NBA and NFL player-prop odds are already archived in Supabase by the props
  collectors (`nba_player_props.md`, `nfl_player_props.md`), so those runs
  read the archive instead of pulling. Boards are 3–10 MB, which is why
  extraction stays in the browser.

## Open questions

1. ~~Does the API host keep a background task alive?~~ Yes: Render runs a
   long-lived process (Refresh lifecycle). Still open: how fast pricing runs on
   the Render instance's CPU. Benchmark first.
2. Is copying a 1 MB JSON page from mobile Safari reliable?
3. `STALE_AFTER = 20 min` is a guess. FD ace ladders appear close to match time
   and have moved a lot within a day (Medvedev ~9 → ~17 aces between 10-27 and
   10-29), so it may need to be tighter for matches starting within 2 hours.
4. Goblin/demon payouts vary by line and aren't in the board JSON. Without
   them the page can show P but not whether a goblin at 0.66 beats its
   multiplier. Manual payout entry is out of scope for v1; is P alone enough
   to pick goblins from?
