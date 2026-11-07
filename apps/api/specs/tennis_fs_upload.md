# Tennis PrizePicks Upload

_Created: 2026-11-02 (scope; not yet implemented). Updated 2026-11-06: Total
Games and Total Games Won, mixture fit, strategy-rule flags, live-row filter._

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
- `tennis_odds_snapshots` and `tennis_runs` tables.

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
parse in browser, keep FS, TG, TGW + Aces rows
POST /tennis/runs  --------------->  validate payload
                                     latest snapshot older than 20 min?
                                       yes -> background refresh ---> tennis_odds_snapshots
                                     run model on freshest snapshot
                                     store run -------------------->  tennis_runs
                      <------------  run id, results, odds_age, refreshing?
if refreshing: poll GET /runs/{id}   rerun when the new snapshot lands
```

## Odds: on-demand refresh, not a scheduled collector

The other collectors run as pg_cron → `ops.dispatch_workflow` → GitHub
Actions → `curl` to the API. Every tick costs Actions minutes, and the private
repo's 2,000 free minutes already bind collector schedules. A tennis collector
every 20–30 minutes around the clock (ATP/WTA play in every time zone) would be
the most expensive schedule in the repo, and most of its pulls would never be
read.

So v1 refreshes **on upload**:

- If the newest `tennis_odds_snapshots` row is under `STALE_AFTER` (20 min),
  run immediately on it.
- Otherwise run immediately on the stale snapshot, flag every row with its
  `odds_age`, and start a background refresh. The page polls; when the refresh
  lands, the API reruns the same board and the page swaps the table.
- One refresh at a time. A second upload during a refresh waits for it.

Refresh = `fetch_sharp_odds(league, market, sweep=...)` from
`routers/sharp_odds.py` for leagues `atp`, `wta` and markets:

| Market | Books | Used for |
|---|---|---|
| `moneyline` | DK, FD | p_win (de-vigged, averaged) |
| `total_games` | DK main + alternates | fit target (main); ladder check at PP lines (alternates) |
| `game_spread` | DK main | spread check, rule-of-thumb FS |
| `player_aces` | FD ladder (over-only) | ace rate |
| `player_games_won` | DK main only, both sides | Total Games Won book line |

Do **not** pull `player_total_games`: despite the name, SharpAPI's DK rows for
it are "Break On Opponent's Serve" (`player_name` says so; checked 11-05).

Measured: 1,064–1,327 rows across 51–60 events per pull before
`player_games_won` (28–60 rows more on 11-05/11-06). At the free tier's 12
requests/min (the client paces at 5 s), the ten market×league sweeps take
about 1.5–2.5 minutes. Data is 60 s delayed.

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

Drop rows whose `status` is not `pre_game`, and doubles (player name contains
`/`). **Do not filter on `is_live` / `in_game`**: PrizePicks sets both true on
pre-match Total Games, Total Games Won and Total Sets rows that are also
offered live (47 such rows on the 11-05 board, all `status: pre_game`).
Filtering on them would drop most Games Won standards.

`Total Games` is one line per match but may arrive under either player; the
server dedupes on (match, odds_type, line).

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
              "start_time": "2026-10-31T12:00:00-04:00" } ] }
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

Response: `{ run_id, odds_snapshot_at, odds_age_s, refreshing, results[] }`.

### `GET /tennis/runs/{id}`

Same auth. Returns the run, rerun against the newest snapshot if one has landed
since.

### Result row

One row per PrizePicks line. Every stat shares the first block; the rest
depend on `stat`.

| Field | Meaning |
|---|---|
| `stat`, `player`, `opponent`, `tour`, `start_time`, `odds_type`, `line` | from the board |
| `p_win` | the row player's de-vigged moneyline, averaged over DK/FD |
| `fav_ml` | shortest moneyline price in the match (blowout rule) |
| `total` | DK main total games line |
| `fit` | `iid`, `mix s=<x>`, or `ml_only` (Model) |
| `model_mean` | mean of the priced quantity (FS, match games, or player games) |
| `p_over`, `p_under`, `push` | at the line; over/under exclude the push |
| `lean` | the side above 50%, or none. Goblin/demon rows can only lean over (below) |

FS only:

| Field | Meaning |
|---|---|
| `spread` | DK game spread for the player |
| `aces_src` | `FD <line> <odds>`, `PP ladder ~x`, or `assumed <rate>` |
| `model_median` | FS distribution |
| `p_side_at_minus2` | the lean's side repriced as if the model ran 2 points low (Bias check) |
| `rule_fs` | the strategy notes' rule of thumb, for reference only: favorite `13 + |spread| + 0.5 × aces`, underdog `7 − spread + 0.5 × aces`. Not a lean; shown so disagreement with the model is visible (Stefanini 11-05: rule 14.8 vs line 13, model under 0.56) |

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
- `fails_bias_check` (FS): the lean drops under 50% at `p_side_at_minus2`.
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
- `whole_line` (TG): PP posted a whole number, so a push is possible.
- `no_book_line`: no moneyline match (FS also needs a total). Row is shown,
  not priced.
- `stale_odds`: snapshot older than `STALE_AFTER`.
- `doubles_skipped` (run level): how many doubles rows were dropped.

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
0.706). It raises WTA favorites' FS 1–1.5 points, in the direction of the
bias check's suspected 2-point shortfall. Cirstea u21 on 11-04 (won 7-5 6-1,
base 23) read 0.744 under iid and 0.650 mixed, and would have failed the bias
check mixed (0.501).

Not yet shown: that it grades better. The only graded board with a saved
snapshot (10-31) is mostly ATP and can't separate the two. `TENNIS_FIT=iid`
forces the old behaviour in the script for comparisons.

### Carry over the fixes already in the scripts

- Moneyline names match exactly first, then by prefix. DK and FD spell some
  players differently ("Daniel Merida" vs "Daniel Merida Aguilar").
- Ace ladders and games-won lines are matched to a player by surname suffix.
- FD `player_aces` is over-only: divide by `ONE_SIDED_MARGIN = 1.045`.
- DK sometimes posts identical templated total ladders for near-even WTA
  matches (Shubladze–Liu and Eala–Sakkari, 11-06). Identical rows across two
  matches are expected, not a cache bug.

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
- Total Games under both players' names prices once.
- An under lean on a goblin/demon is flagged `over_only`.

## Bias check

Applies to FS only. Graded boards (10-24, 10-25, 10-31) suggest the model's
mean FS runs about 2 points low. That's unconfirmed, about 1.5 SE. A
favorite's under can look strong only because of that bias.
`p_side_at_minus2` reprices each lean with the line moved 2 points against it:

- under at line L: P(under at L − 2), the question being whether the lean
  survives if the model is 2 points low.
- over at line L: the suspected bias already works for overs, so
  `p_side_at_minus2` is just P(over at L) and overs never fail the check.

On 10-31 every lean that passed this check hit (Tagger, Lehecka, Machac,
Zverev). Alcaraz u19.5 couldn't be checked and missed. On 11-05 the three
passing overs went 2 of 3. Strong-favorite unders keep failing it (Eala u21,
Alcaraz u20.5 on 11-05; Alcaraz u23, Shelton u24 on 11-06). The page sorts
passing leans first.

The 2-point figure was measured on iid runs. Once mixed runs have been graded,
re-measure it per tour: the mixture may have removed part of the WTA bias, in
which case the check is too strict there.

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

Keep 7 days; a nightly delete is enough. A snapshot is only used whole: never
mix rows from two pulls into one model run.

`tennis_runs`

| column | |
|---|---|
| `id` | uuid |
| `created_at` | timestamptz |
| `board_saved_at` | timestamptz (client-reported) |
| `snapshot_id` | fk → `tennis_odds_snapshots` |
| `board` | jsonb: the validated rows |
| `results` | jsonb |
| `model_version` | text: git sha of the module |

`board` is kept so v2 grading can settle every line, not just the ones played.

## Page

`apps/visualize-ev/app/prep/tennis/page.tsx`:

- Textarea plus file drop. Parse with `JSON.parse` in a try/catch, extract the
  rows, and show "N Fantasy Score, M Total Games, K Games Won lines; J doubles
  skipped" before sending.
- Secret entered once, kept in `localStorage` (wrapped in try/catch; the page
  works if storage is unavailable by asking again).
- Tabs or a filter by stat. Columns per stat:
  - FS: start time (local), player, line, type, lean, P, P at −2, rule FS,
    aces source, flags.
  - Total Games: start, match, line, type, lean, P, DK P at line, fav ML,
    flags.
  - Games Won: start, player, line, type, lean, P, DK line and prices, flags.
- Default sort: playable leans first (passes bias check, not `over_only`
  under, not `ml_only`), then by P. Mobile layout, since phone use is the
  point of this feature.
- Banner when `stale_odds` or `refreshing`.
- Render all strings as text, never as HTML.

## Later

- **Grading (v2):** a nightly job settles `tennis_runs` from ESPN set scores
  with `grade_tennis_fs.py` logic. Total Games and Games Won settle from set
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
- **Other sports:** same upload, `pp_vs_books.py` instead of the tennis model.
  NBA and NFL player-prop odds are already archived in Supabase by the props
  collectors (`nba_player_props.md`, `nfl_player_props.md`), so those runs
  read the archive instead of pulling. Boards are 3–10 MB, which is why
  extraction stays in the browser.

## Open questions

1. Does the API host keep a background task alive for the 1.5–2.5 minute
   refresh after the response returns, or does it sleep? If not, the refresh
   runs inside the request and the page shows a spinner.
2. Is copying a 1 MB JSON page from mobile Safari reliable?
3. `STALE_AFTER = 20 min` is a guess. FD ace ladders appear close to match time
   and have moved a lot within a day (Medvedev ~9 → ~17 aces between 10-27 and
   10-29), so it may need to be tighter for matches starting within 2 hours.
4. Goblin/demon payouts vary by line and aren't in the board JSON. Without
   them the page can show P but not whether a goblin at 0.66 beats its
   multiplier. Manual payout entry is out of scope for v1; is P alone enough
   to pick goblins from?
