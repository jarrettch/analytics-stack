# NBA props collection

Design, measurements and the rollout order are in `specs/nba_player_props.md`.
This note covers running and checking it. NFL props share the workflow; see
`NFL_PROPS.md` for the parts both leagues share.

Apply these in Supabase before deploying the API:

- `20261025000000_create_nba_player_prop_odds.sql`: the append-only table and
  the atomic `append_nba_player_prop_odds` function.
- `20261025010000_revoke_nfl_props_append_from_clients.sql`: removes client
  access to the NFL append function.
- `20261025020000_create_nba_player_prop_close_coverage.sql`: the daily
  coverage query.

None of them backfill observations. The API server needs the same
credentials as NFL props: `SHARP_API_KEY`, `SUPABASE_URL`, the service-role
`SUPABASE_KEY`, and `CRON_SECRET`.

## Schedule

Supabase pg_cron dispatches `Fetch Player Props` (`fetch-player-props.yml`)
at :07 and :37, 08:07–22:37 ET, every day. One run collects NFL and NBA. The
runner gate (`scripts/props_schedule_gate.py`) checks each league's ESPN
schedule in parallel, and Render is woken only if a league has an eligible
game. The driver (`scripts/run_props_collection.py`) collects eligible
leagues earliest tip-off guard first, calls each league's endpoint once, and
never retries a collection request.

NBA has two phases. The phase comes from the tick the run was dispatched
for, read from the run's `created_at`, so a run that waited in the queue or
was rerun keeps its tick's phase.

- **Intraday**, on the 11:37 and 15:37 ticks: every game on today's ET slate
  that tips at least 10 minutes out. Props were all posted by 11:33 ET on the
  first measured slate.
- **Close**, on every other tick: games tipping 10–90 minutes after the
  gate started, across ET midnight when the window crosses it. A 10:30 pm tip
  is swept at 9:07, 9:37 and 10:07.

## Route

```text
POST /api/nba/cron/fetch-player-props?collection_key=<run-id>-nba-props-<phase>&phase=close|intraday&window_at=<gate start>
Authorization: Bearer <CRON_SECRET>
```

`phase` defaults to `close`, and manual calls without `window_at` use the
request time. A second call while a sweep is running returns
`success: false` without collecting. The API repeats the schedule check
before collecting, and the sweep times out after eight minutes. The driver
allows 540 seconds per league, so the API reports its own timeout first. A
timeout or 5xx leaves the database outcome unknown. Check the collection's
rows before collecting again by hand.

A sweep fetches all eight markets before writing anything, then writes in one
transaction: points, rebounds, assists, made threes, and the four combos. Row
failures follow the NFL rules: an invalid row for a selected game is
`rows_rejected` and blocks every write, and an invalid row for another game is
`rows_skipped`. When the same teams play on consecutive days, the provider's
start time picks the game within six hours, and the date in the SharpAPI
event ID must agree.

## Reading the data

- **Books drop out near tip.** On the first slate, DraftKings had 153 rows
  and FanDuel 14 at 5:38 pm; at 6:43 pm it was FanDuel 168 and DraftKings 20.
  A sweep never requires both books. The close is per book, and `book_rows`
  in the response counts rows per game and book.
- **Combo rows carry `combo_check`.** `passed` means the line was within 3
  of the same book's component lines in the same sweep. `failed` means it
  wasn't, and the row is probably mislabeled: it's kept for audit, never used
  as a price. `unchecked` means a component was missing or ambiguous in that
  sweep. Single-stat rows have null. `combo_checks` in the response counts
  each outcome per book.
- **Observed close:** per book, the latest `provider_timestamp` before
  `starts_at` where `is_active`, not `is_stale_pregame_price`, and
  `combo_check` is null or `passed`. SharpAPI's timestamp is when it last
  refreshed the price, not when the book posted it.
- `implied_probability` includes vig.

## Monitoring

`check-collectors.yml` runs daily at 18:00 UTC and fails (and so emails) when:

- `fetch-player-props.yml` has no run on `main` in 26 hours, or
- a run of `fetch-player-props.yml` or `fetch-ufc-odds.yml` was cancelled or
  timed out in 26 hours. They share the `sharpapi-odds` concurrency group,
  where GitHub cancels an older pending run when a newer one queues, so a
  cancelled run may be a lost close. Cancelling a run by hand also trips it,
  once.

The same job then reports yesterday's close coverage in its job summary, even
when the checks above failed. It shows a book × market grid of how many
games had a usable close, and the gaps per game. Gaps add a warning
annotation but don't fail the job, because some gaps are expected. Only a
report that can't be produced fails it. Postponed and cancelled games aren't
expected; rows archived for them are listed as unplayed matchups. For another
date:

```text
POST /api/nba/cron/player-props-coverage?date=YYYY-MM-DD
Authorization: Bearer <CRON_SECRET>
```

## Actions minutes

The repo is private, so the free allowance is 2,000 Actions minutes a month,
and the budget allows $5 of overage. The combined workflow projects to about
1,650–2,100 minutes a month across all collectors. Compare with the billing
report:

```sh
gh api "/users/jarrettch/settings/billing/usage?year=2026&month=11"
```

## Tests

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest apps/api/tests -q
```

The NBA-specific files are `test_nba_player_props.py`,
`test_props_schedule_gate.py`, `test_run_props_collection.py` and
`test_report_props_coverage.py`. Run `supabase/tests/nba_player_prop_odds_test.sql`
and `supabase/tests/nba_player_prop_close_coverage_test.sql` after the
migrations in a disposable PostgreSQL database.
