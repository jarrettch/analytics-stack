# NBA Player Props

_Created: 2026-10-24 (scope; not yet implemented)_
_Revised: 2026-10-24 with first-slate measurements: all eight player markets in v1, behind a combo consistency check, and books dropping out of the feed before tip_
_Revised: 2026-10-25 after review: an exact combo-check contract, cancelled-run detection, and defined cron hours. Later the same day: NFL and NBA props combined into one workflow sized from measured Actions minutes, on NFL's existing :07/:37 ticks, with a deadline-ordered collection driver_

The NBA counterpart to `nfl_player_props.md`, with one difference in purpose:
NFL props capture a close for later CLV, while NBA props also need **intraday
movement**. Books post NBA props the morning of the game and move them on
injury and lineup news through the afternoon. A close-only archive throws
that movement away. Like the NFL archive, every day not captured is gone;
the 2026-27 season started 2026-10-20. Display is out of scope.

## What carries over unchanged

- `routers/sharp_odds.py`: `fetch_sharp_odds("nba", market, sweep=...)` already
  takes the league. Pacing, retries, the 304 rule, cursor checks and the page
  cap apply as-is.
- The complete-sweep-then-write rule, scoped row failures (`rows_rejected` vs.
  `rows_skipped`), the per-sweep schedule cache, and the result fields the
  workflow checks with `jq`.
- The append-only table pattern: both triggers, an atomic `append_*` RPC that
  returns a scalar count, and a unique key on
  `(collection_id, bookmaker_id, selection_id, american_odds)`.
- The close rule: latest `provider_timestamp` before `starts_at`, active and
  non-stale rows only. The free tier's 60-second delay applies.
- `matchup_key` is `f"{away_tri}@{home_tri}"` (`nba.py:755`).

## Markets

The aliases come from `GET /api/v1/markets?league=nba` (2026-10-24). Most
names that look like the obvious alias are invalid: `player_threes`,
`player_points_rebounds_assists` and `first_basket_scorer` all return 400.

| v1 | alias |
|---|---|
| yes | `player_points`, `player_rebounds`, `player_assists`, `player_made_threes` |
| yes, checked | `player_points_+_rebounds_+_assists`, `player_points_+_rebounds`, `player_points_+_assists`, `player_rebounds_+_assists` |
| no | `first_basket`, `player_double_double` (scorer-shaped), long-tail markets |

**Combos go in behind a consistency check.** In the WNBA Finals study
(2026-10-17/19), a FanDuel row labeled `rebounds_+_assists 13.5` was really
that player's PRA line. Mislabeled rows in an append-only table can't be
fixed later. On the first NBA slate, no combo row was mislabeled: 0 DK/FD
gaps of 3 or more across 54–76 players per market. Every combo line
also came within ±2 of the sum of the same book's single-stat lines for that
player: 400+ comparisons across both runs. The combos also move the most
intraday (see measurements), so leaving them out would drop the best
movement data.

### Combo check contract

The check runs inside one sweep, after normalization and before the insert.

- **Match key:** `(game_date, matchup_key, bookmaker_id, selection_name)`,
  using canonical ESPN fields, never provider event IDs or provider dates.
  Components and the combo must share the whole key, so one player's lines
  are never compared across books or games.
- **Component line:** a component counts only when every row of that market
  for the key in this sweep has one distinct `line`, over and under included.
  If the over and under lines differ, or the line moved between pages, the
  component is ambiguous and is treated as missing.
- **Comparison:** each combo row, over and under separately, passes when
  `|line − sum(component lines)| ≤ 3`.
- **Outcome is stored on the row,** in a `combo_check` column: `passed`,
  `failed`, `unchecked` (a component was missing or ambiguous), or null for
  single-stat markets. Failed rows are stored, not dropped. The flag is set
  at write time, so readers filter out `failed` and never see a mislabeled
  line as valid. The rows stay available for audit, and a false positive
  from the 3-point threshold doesn't destroy data that can't be
  recollected.
- **Reporting:** the sweep result carries `combo_rows_passed`,
  `combo_rows_failed` and `combo_rows_unchecked` per book, plus up to five
  `combo_failures` examples (key, market, combo line, component sum). The
  first ten failures are also logged. Failures never fail the sweep or count
  toward `rows_rejected`. A spike in `unchecked` usually means a book
  dropped out mid-sweep (see below). It is reported, not hidden.

A mislabeled R+A row holding the PRA line misses by the player's points
line, so it fails easily. `unchecked` rows are the remaining risk. A reader
that needs certainty can re-run the check across sweeps, using the nearest
component lines from the same book.

All v1 markets are line-shaped, so the table drops the NFL scorer shape:
`line` and `side` are `not null`, and there is no `market_shape` column.

## Table

Create a separate `nba_player_prop_odds` that mirrors `nfl_player_prop_odds`,
minus the scorer shape. Odds tables in this repo are per-sport. Merging into a
generic `player_prop_odds` would mean migrating an append-only NFL table that
rejects updates. If NCAAB props come later, a union view across the per-sport
tables is cheaper than that migration.

## Schedule: one combined props workflow

NBA does not get its own workflow. The NFL props workflow becomes one SharpAPI
props workflow (`fetch-player-props.yml`, replacing
`fetch-nfl-player-props.yml`). One pg_cron dispatch per tick runs one runner
gate. The gate checks the NFL and NBA windows, then the job calls each
eligible league's endpoint in deadline order, after one Render warm-up. This
was chosen over a separate NBA workflow for Actions minutes (see Cron
hours). It also means NFL and NBA never compete for the concurrency group.

| phase | league | ticks (ET) | selects |
|---|---|---|---|
| `close` | NFL | every tick | games kicking off 10 to 90 min out (unchanged) |
| `close` | NBA | every tick | games tipping 10 to 90 min out |
| `intraday` | NBA | the 11:37 and 15:37 ticks | every pregame game on today's ET slate that tips at least 10 min out |

Ticks are **:07 and :37, 08:07–22:37 ET, every day**: 30 dispatches. These
are NFL's current minutes. Only the hours extend for NBA.

### Why :07/:37

A game is eligible when the tick falls between T−90 and T−10. At a 30-minute
cadence that's two or three attempts, depending on where the slots fall
relative to the start time.

| start (ET) | :07/:37 attempts | last | :12/:42 attempts | last |
|---|---|---|---|---|
| NBA 7:00 | 5:37, 6:07, 6:37 | T−23 | 5:42, 6:12, 6:42 | T−18 |
| NBA 7:30 | 6:07, 6:37, 7:07 | T−23 | 6:12, 6:42, 7:12 | T−18 |
| NFL 1:00 | 11:37, 12:07, 12:37 | T−23 | 11:42, 12:12, 12:42 | T−18 |
| NFL 4:05 | 2:37, 3:07, 3:37 | T−28 | 2:42, 3:12, 3:42 | T−23 |
| NFL 4:25 | 3:07, 3:37, 4:07 | T−18 | 3:12, 3:42, 4:12 | **T−13** |
| NFL 8:15 | 7:07, 7:37 | T−38 | 7:12, 7:42 | T−33 |
| NFL 8:20 | 7:07, 7:37, 8:07 | T−13 | 7:12, 7:42 | T−38 (**one fewer**) |
| NFL 9:30 London | 8:07, 8:37, 9:07 | T−23 | 8:12, 8:42, 9:12 | T−18 |

An earlier draft moved both leagues to :12/:42 and claimed NFL lost
nothing. That was wrong. It cost the 8:20 kickoff an attempt and left 4:25's
last attempt only 3 minutes of slack before the 10-minute guard. :07/:37
gives every NBA :00/:30 tip three attempts with the last at T−23, only 5
minutes earlier than :12/:42, and leaves every NFL attempt as it is today.
(The first draft's :22/:52 would have put NBA's last capture at T−38.)

**Slack is the margin between a tick and its guard deadline** (start −10
min). Render warm-up (up to 3.5 min), queueing behind another run, and the
league collected first all spend it. The tightest cases are NFL 8:20 at
8:07 (3 minutes, the same as today) and NFL 4:25 at 4:07 (8 minutes). Every
NBA :00/:30 last attempt has 13 minutes. Ordering by deadline (below)
protects these as well as ordering can. It can't protect a 3-minute slack
from a slow warm-up. That limit already exists for NFL today.

### Phase from the dispatch tick

**The phase comes from the run's dispatch time, not its start time.** No
dispatch input is needed. The gate reads the run's `created_at` (`gh api
repos/{repo}/actions/runs/$GITHUB_RUN_ID --jq .created_at`, which needs
`actions: read`). That is when pg_cron dispatched it, and it stays the same
when the run queues or is rerun. The gate maps it to its tick, the latest
:07/:37 slot at or before `created_at`. The 11:37 and 15:37 ticks are NBA
`intraday`, a superset of that tick's close selection. Every other tick is
`close`. A run that starts at 11:50 after queueing still runs intraday.

The two times do separate jobs. **The phase comes from the tick. The
eligibility window comes from the gate's actual start time**, as for NFL
today, so a late or rerun job never selects games that have already tipped.
If the `created_at` lookup fails, the gate uses its own start time and logs
that it did. A late fallback can lose an intraday sweep, but never a close.

The gate passes `phase` to the API, and the collection key gets a suffix
(`<run_id>-nba-props-<phase>`). No column is needed, because readers choose
observations by `provider_timestamp`. This removes the need to change
`ops.dispatch_workflow()`. A dispatch input would have meant dropping and
recreating that shared helper, because `create or replace` with a new
argument list adds an overload, and two-argument calls then fail as
ambiguous.

### League isolation

**The two leagues are independent inside a run, gate included.**

- **Gate: concurrent lookups, per-league deadlines, immediate writes.** The
  current scoreboard fetch makes up to three 20-second attempts with 5 s and
  10 s backoff, about 75 s per date. NFL can need two dates when the
  window crosses ET midnight, so one league's worst case is about 150 s. Two
  sequential leagues could exceed any reasonable gate timeout before the
  second league's outputs are written. So each league's lookup runs in its
  own thread with a **100-second deadline**. On success it writes
  `<league>_eligible`, `<league>_gate_ok=true`, its earliest
  `<league>_deadline` (min selected start − 10 min), and for NBA the
  `nba_phase`. It appends to `$GITHUB_OUTPUT` as soon as it finishes. A
  league that errors or misses its deadline writes `<league>_gate_ok=false`
  and nothing else. The gate exits 0 once both leagues have written. The
  step limit is **3 minutes**: two 100-second deadlines run in parallel,
  plus startup and the `created_at` lookup. An ESPN NBA outage then costs
  NBA only, and the NFL result is still written.
- **Collection: one driver, ordered by deadline.** A small script
  (`scripts/run_props_collection.py`, standard library only) replaces the
  per-league curl and `jq` blocks. It takes the gate outputs, collects
  eligible leagues in order of **earliest guard deadline first**, and runs
  each league's existing success check. NFL usually goes first on overlap
  ticks, because its deadlines are the tighter ones (table above). A failure
  in the first league never skips the second.
- **Job result:** the driver exits non-zero if either league's gate failed
  or its collection failed, after attempting both. A single-league failure
  still fails the run and sends the email.

Ordering by deadline keeps the league closest to its guard first. A slow
first league can still push the second past its guard. On overlap ticks
the second league is usually NBA, with 13+ minutes of slack, which a normal
NFL sweep (~2 min) doesn't use up. Only a near-limit NFL failure (8 min)
plus a slow warm-up costs NBA a close attempt. That's an accepted tradeoff,
and the coverage check makes it visible.

### Time budget per run

| step | limit |
|---|---|
| gate | 3 min (`timeout-minutes`), 100 s per league, in parallel |
| Render warm-up | 6 attempts × (20 s + 15 s), about 3.5 min, unchanged |
| collection driver | 20 min step limit; per league `--max-time 540`-equivalent (9 min) and `--connect-timeout 20`, no retry |
| job | `timeout-minutes: 30` |

Worst case: 3 + 3.5 + 18 = 24.5 minutes, inside the job limit.

The current NFL job retries the collection request (`--retry 2
--retry-max-time 1200`) inside a 25-minute job. Carried over unchanged, a
slow NFL failure could use the whole budget. Retrying after a timeout is
also unsafe, because the database outcome is unknown and the server's
lock may still be held. **Collection requests are never retried.** curl
treats a timeout and HTTP 5xx as transient, so even plain `--retry` would
resend a request whose outcome is unknown. The driver keeps the same rule.
The warm-up step already absorbs connection problems. The 9-minute
per-league limit sits above the server's 480-second sweep timeout, so the
server reports its own timeout first.

### Intraday sweeps

With the intraday ticks, an NBA player gets up to five observations a day,
enough to show morning-to-tip movement. On the first slate all props were
already posted by 11:33 ET. When books first post is still unknown, so 11:37
is fine as long as later slates also post by late morning. If a slate shows
gaps at 11:37, move the intraday tick to 12:07. Multiple close attempts
matter more than planned because of book dropouts (below).

### Books drop out of the feed before tip

One sweep is not a complete snapshot of both books. On 2026-10-24:

| capture (ET) | DK points rows | FD points rows |
|---|---|---|
| 11:33 | 156 | 156 |
| 17:38 | 153 | 14 |
| 18:43 | 20 | 168 |

At 18:43, DraftKings had points rows for 1 of 7 pregame events, and dropping
the `is_main_line` filter didn't bring the others back. The rows were missing
from the feed entirely, not reflagged as alternates. Every market showed the
same pattern. The NFL Sunday captures (2026-10-18/19) had balanced books, so
this has only been seen on NBA so far.

Rules that follow:

- **Never require both books in a sweep.** A missing book is not a failure
  and not a pulled market.
- **The close is per book:** the latest qualifying row for that book before
  `starts_at`, wherever that row came from. A book absent from the last sweep
  keeps its close from an earlier one.
- **Log per-book row counts per game** in the sweep result, so coverage gaps
  can be measured instead of guessed.

### Cron hours

**08:07–22:37 ET every day.** NFL needs 08:07 for London games. NBA needs
the noon ET tips (Christmas, MLK Day, weekend afternoons, international
games) and 10:30 pm tips, which are caught at 9:07, 9:37 and 10:07.
Most ticks find no eligible game and exit at the runner gate without waking
Render.

**Measured cost (billing report and run durations, 2026-10-25).** GitHub
Free includes 2,000 Linux minutes a month, and each job rounds up to a whole
minute. The Actions budget was $0 with usage blocked at the limit, so running out
of the allowance would have stopped every collector until the next month.
**It was raised to $5 on 2026-10-25,** so going over now costs a few dollars
instead of halting collection.

| measurement | value |
|---|---|
| Oct 1–22, GitHub's own scheduler (late, skipping) | 10–25 min/day, avg ~16 |
| Oct 24, first full day under pg_cron | 37 min/day |
| props gate-only run | ~10 s, bills 1 min |
| props collecting run (NFL, Oct 25) | 75–127 s, bills 2–3 min |
| October total | 456 min ($2.74 gross, $0 billed) |

Projection, in minutes per month:

| | current collectors | NBA added | total |
|---|---|---|---|
| separate NBA workflow | ~1,150–1,250 | ~1,200–1,600 | ~2,400–2,800 |
| **combined workflow** | ~1,150–1,250 | ~500–850 | **~1,650–2,100** |

Combining removes about 26 gate-only runs a day. What remains is 4 extra
ticks a day plus NBA's collecting time: about 12 runs a day that each bill
1–2 minutes more than a gate-only run. **The combined plan probably fits
under 2,000, but without much headroom.** Busy NFL Sundays and full NBA
slates push it toward the top of the range.

Options if it doesn't fit, cheapest first:

1. ~~Raise the Actions budget to about $5.~~ Done 2026-10-25. Overage is
   $0.006/min, so 500 minutes over costs $3. The $5 cap is the safety net
   whatever else is chosen; past it, Actions blocks again.
2. Run the 08:07–09:37 ticks on Sundays only (London games). That saves
   about 100 minutes a month.
3. Have pg_cron call the Render endpoints directly, without GitHub runners.
   Props then use no Actions minutes, but the run's checks and email-on-
   failure are lost, and Render is woken on every tick. That's a larger
   redesign.
4. Make the repo public. Public repos get unlimited Actions minutes.

Check the billing report mid-November (`gh api
/users/jarrettch/settings/billing/usage`) against the projection.

## Shared key and concurrency group

Props (one combined run per tick, :07/:37) and UFC (:20 at 10 and 16 ET)
share the SharpAPI key and the `sharpapi-odds` concurrency group. **GitHub
keeps at most one pending run per concurrency group. A newer queued run
cancels the older pending one.** Combining NFL and NBA removes the main
collision. A drop now needs three runs in the group at once, for example a
long props run, then UFC at :20 pending, then the next props tick. That
shouldn't happen at current run lengths, but it isn't impossible.

**Detection.** The watchdog in `check-collectors.yml` only checks that each
workflow has *any* run on `main` within 26 hours. Cancelled runs and
successful empty-window runs both satisfy it, so it cannot see drops.
v1 adds two checks:

1. **Cancelled runs (watchdog, cheap and direct):** fail when any run of
   `fetch-player-props.yml` or `fetch-ufc-odds.yml` has
   `conclusion == cancelled` in the last 26 hours. That is exactly what a
   concurrency drop leaves behind. It can't say which games lost a close,
   only that one may have.
2. **Per-game coverage (data):** a daily check over yesterday's ESPN games,
   per game, book and market. A row counts only if it is a usable close
   candidate:
   - `is_active = true` and `is_stale_pregame_price = false`
   - `combo_check` is null (single-stat) or `passed`. `unchecked` rows are
     counted in a separate column, and `failed` rows never count.
   - `starts_at − interval '90 minutes' <= provider_timestamp < starts_at`

   The report lists, per game, which book × market cells have no qualifying
   row, so partial coverage shows up instead of being averaged away. It
   also catches gate errors, guard misses and book dropouts, which no run
   status shows. Treat it as a standing coverage metric, like starter
   coverage for pitcher Ks, not a pass/fail alarm, since some missing
   coverage is expected. It runs as one extra step in the existing daily
   `check-collectors.yml` job, calling a protected Render endpoint. That
   adds a warm-up and query, about 1 minute a day, ~30 minutes a month,
   which is inside the projection's error range.

Individual drops stay undetected until check 1 runs, up to a day later, and
a lost close can't be recollected.

The in-process `_request_lock` in `sharp_odds.py` serializes requests inside
one Render process. That holds only while the API runs a single worker.

## Identity

- **Schedule source:** ESPN NBA scoreboard by date. `nba.py` has
  `fetch_next_slate` but no per-date fetch that returns status the way
  `nfl._scoreboard_for_date_with_status` does. Add one, or add a date-scoped
  helper in `espn_feeds.py`.
- **Tricodes:** ESPN abbreviations are not NBA tricodes. Schedule rows go
  through `_canonical_tricode` / `_ESPN_TO_NBA_TRICODE`, and SharpAPI team
  names go through `nba.get_tricode_from_team_name`, which already handles
  `"PHO Suns"`. An unmapped name is fatal for selected games, same as NFL.
- **Players:** store `provider_player_name` raw. Resolving to ESPN athlete IDs
  is deferred to settlement. Keep `selection_id` `not null`, because dedup
  depends on it.

## First-slate measurements (2026-10-24, 8 games)

These come from a read-only probe at 11:33 ET and again at 18:43 ET (T−47 for
the 7:30 tips). A 17:38 run captured the four single-stat markets before the
laptop running it went back to sleep. Its later stalls were the laptop
sleeping, not SharpAPI (confirmed in `pmset` logs).

- **Sweep cost:** 1–2 pages per market. All eight markets took 16 requests
  and about 80 seconds in the morning, and 8 requests and about 40 seconds
  before tip. That is far inside the 8-minute sweep limit. The shared 12/min
  key has room for NFL and NBA in the same hour.
- **Posting time:** nothing at 00:06 ET, everything by 11:33 ET. SharpAPI's
  `timestamp` is its refresh time, within seconds of the capture, so it can't
  show when a book posted.
- **Coverage, morning:** DK and FD both covered all 8 events, with 112–156 rows
  per book per market. 54–76 players were on both books per market.
- **Main lines:** exactly one line per book, event, player and side. No missing
  `selection_id`, and no stale or inactive rows.
- **One-sided selections:** over and under counts differ by up to 13 in
  rebounds and assists, so some players have only one side. The schema allows
  this. De-vigging has to skip unpaired rows.
- **Intraday movement**, over side, comparing 11:33 to 18:43 for players present
  in both runs:

  | market | FD lines moved | DK lines moved |
  |---|---|---|
  | points | 23 / 66 | 5 / 9 |
  | rebounds | 7 / 64 | 4 / 18 |
  | assists | 4 / 57 | 3 / 16 |
  | made threes | 1 / 55 | 2 / 13 |
  | PRA | 22 / 57 | 5 / 9 |
  | P+R | 28 / 64 | 4 / 9 |
  | P+A | 17 / 48 | 6 / 9 |
  | R+A | 12 / 47 | 1 / 9 |

  Most other selections changed price without changing the line. The largest
  moves were 2–3 points (Naji Marshall P+A 20.5 → 23.5 FD; Zion Williamson
  PRA 28.5 → 26.5 on both books). Points and the combos move; threes and
  assists hardly change their lines.
## Build steps

1. Migration `create_nba_player_prop_odds.sql`, copied from the NFL migration
   minus the scorer shape, plus the `combo_check` column (checked against
   `market_type`: null for single-stat markets, one of the three outcomes for
   combos). Add a matching `supabase/tests/nba_player_prop_odds_test.sql`.
2. `routers/nba_player_props.py`: an NBA schedule with a per-date status fetch,
   normalize/persist/collect modeled on `nfl_player_props.py`, the `phase`
   parameter, the combo check contract, and per-book row counts per game in
   the result. Register the router in `main.py`.
3. `scripts/props_schedule_gate.py`, replacing `nfl_props_schedule_gate.py`.
   It runs concurrent per-league lookups with 100-second deadlines and
   writes each league's outputs as soon as that league finishes
   (`*_eligible`, `*_gate_ok`, `*_deadline`, `nba_phase`), plus
   `window_at`. It derives the NBA phase from the run's `created_at` tick,
   falling back to its start time, and exits 0 once both leagues have
   written. A thread's timeout doesn't stop its lookup, so outputs are
   written only by the main thread, once per league, under a lock. A worker
   that finishes after its deadline is ignored. Workers are daemon threads,
   so a stuck lookup can't delay the gate's exit.
4. `scripts/run_props_collection.py`: the collection driver. It collects
   eligible leagues in earliest-deadline order with no retries, runs each
   league's success checks (today's `jq` conditions), writes the job
   summary, and exits non-zero after attempting both if either failed.
   `urllib`'s `timeout` limits each socket operation, not the whole request,
   so the 540-second limit is enforced as a total wall-clock deadline. The
   request runs in a worker that is abandoned at the deadline, and the
   league is marked failed with an unknown outcome. A slow trickling
   response can't use up the second league's budget.
5. `.github/workflows/fetch-player-props.yml`, which replaces
   `fetch-nfl-player-props.yml` once the rollout below finishes: gate (3 min), warm-up, driver (20 min),
   job 30 min, `permissions: actions: read`. Update the `NFL_PROPS.md`
   references.
6. ~~Raise the Actions budget~~ Done 2026-10-25: the Actions budget is now $5.
7. pg_cron migration `20261026000000_schedule_combined_player_props.sql`:
   schedules `fetch-player-props` at `7,37` for ET hours 8–22 and
   unschedules `fetch-nfl-player-props` in one DO block, behind the same
   Vault-token check, so no tick goes unscheduled or doubled.
   `ops.dispatch_workflow()` is unchanged. `test_collector_schedule.py` now
   applies every migration's schedule and unschedule calls in order before
   replaying the year.
8. `check-collectors.yml`: the NFL props entry becomes
   `fetch-player-props.yml`, and `check_cancelled` fails on any cancelled or
   timed-out run of `fetch-player-props.yml` or `fetch-ufc-odds.yml` in 26
   hours. A test derives that list from the workflows in the
   `sharpapi-odds` group.
9. Per-game coverage check: a protected endpoint with the qualifying-row
   rules above, reporting book × market gaps per game, called from
   `check-collectors.yml`.
10. Tests: modeled on `test_nfl_player_props.py` and
   `test_nfl_props_schedule_gate.py`, plus gate cases (NFL only, NBA only,
   both, intraday ticks, an intraday run that starts late or is rerun, a
   failed `created_at` lookup, one league's scoreboard down while the other
   is still collected, a lookup that finishes after its deadline and must
   not write outputs or delay exit, a collection response that trickles
   past the 540-second total deadline while the second league still runs, an ET midnight crossing for 10:30 pm tips), NFL
   selection unchanged, a sweep where one book is absent, tricode
   canonicalization, and combo checks covering pass, fail, a missing
   component, over and under lines that disagree, a line that moves
   mid-sweep, and the same player on two books.
11. An `NBA_PROPS.md` operations note, and an update to `NFL_PROPS.md` for the
    new workflow and cadence.

## Rollout order

pg_cron dispatches workflows by file name on `main`, and each step below
depends on the one before it. Out of order, NBA sweeps fail, and the
combined run's failure would also hide whether NFL collected.

1. Apply `20261025000000_create_nba_player_prop_odds.sql` and
   `20261025010000_revoke_nfl_props_append_from_clients.sql` in Supabase.
2. Merge to `main` and confirm Render deployed: `POST
   /api/nba/cron/fetch-player-props` answers 401 without the cron secret, not
   404. The watchdog change ships in this merge, so from here it checks
   `fetch-player-props.yml` instead of the NFL workflow.
3. The same day, dispatch `fetch-player-props.yml` once by hand and read the
   job summary. That run also satisfies the watchdog for 26 hours.
4. Within those 26 hours, apply
   `20261026000000_schedule_combined_player_props.sql` in the SQL editor,
   then run `supabase/tests/pg_cron_schedule_test.sql`.
5. After a day of clean scheduled runs, a follow-up change deletes
   `fetch-nfl-player-props.yml` and `scripts/nfl_props_schedule_gate.py`.
   Until step 4, pg_cron still dispatches the NFL workflow by name.

## Out of scope

Display, settlement and CLV, alternate ladders (see the PrizePicks comparator
notes), and moving NBA main lines off SGO.
