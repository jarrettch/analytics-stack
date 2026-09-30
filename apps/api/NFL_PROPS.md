# NFL props collection

Apply `supabase/migrations/20260920000000_create_nfl_player_prop_odds.sql` before
deploying the API. The migration creates the append-only table and the atomic
`append_nfl_player_prop_odds` function. It does not backfill observations.

The API server needs `SHARP_API_KEY`, `SUPABASE_URL`, the service-role
`SUPABASE_KEY`, and `CRON_SECRET`. `SHARPAPI_KEY` is also accepted for the provider
key. These credentials stay on the API server. GitHub's `production` environment
needs the existing `API_URL` and `CRON_SECRET` secrets.

The `Fetch NFL Player Props` workflow is configured to poll ESPN on the GitHub
runner at minutes 7 and 37 every hour, using a standard-library Python script with no API-server
dependencies. It wakes Render only when a game kicks off between 10 and 90 minutes
after the gate starts. Empty windows never call Render, including `/health`.
Failed or malformed ESPN responses fail the Action without waking Render.
The gate checks both ET dates when the window crosses midnight.

GitHub's scheduled trigger has not delivered that cadence reliably. On September
24, the actual runs at 6:18 p.m. and 8:45 p.m. ET missed the 6:45–8:05 p.m.
eligibility window for the 8:15 p.m. game. A successful skipped run does not
prove a close was captured. Reliable automatic capture still needs a replacement
scheduler; until then, manually dispatch within the window and confirm inserts.

A 20-second `/health` timeout is a failed warm-up attempt, not a collection
failure. The workflow retries warm-up up to six times before requesting props.
Read the final collection response and job summary to determine whether rows
were saved. A collection request timeout can have an unknown database outcome;
verify the collection before retrying.

The API repeats the schedule check using the gate's timestamp. `schedule_ok`
must be true before an empty window is treated as a successful no-op. If server
warm-up leaves less than the ten-minute guard, collection fails before
requesting prices.

The protected route is:

```text
POST /api/nfl/cron/fetch-player-props?collection_key=<run-id>-nfl-props
Authorization: Bearer <CRON_SECRET>
```

`collection_key` is required and is stored unchanged. Workflow reruns reuse the
same key. The optional `window_at` query parameter takes a timestamp with a
timezone; the workflow supplies the runner gate's start time. Manual calls default
to the API request time. A rerun can capture changed prices under the same
`collection_id`. The key groups attempts, not a single instant or one price per
selection. Readers must use provider and capture timestamps when choosing an
observation, rather than `DISTINCT` on collection and selection alone.

A qualifying run fetches five markets from both books, including all upcoming
games returned. It resolves each game against ESPN, validates every row, and
sends the accepted rows in one database transaction. Provider timestamps,
inactive prices, and stale-price flags are retained. The provider's start time
only locates the ESPN game within adjacent dates; ESPN supplies the stored date
and kickoff.

Row failures have two scopes. Invalid rows for selected matchups count as
`rows_rejected` and prevent all writes. Unknown or unmapped matchups also remain
fatal because they might belong to a selected game. Invalid rows whose mapped
away/home pair differs from every selected matchup count as `rows_skipped` and
are omitted. Their provider date alone is never evidence that they are outside
the window. An incidental game's failed schedule lookup is also a skip; a
selected game's failed lookup sets `schedule_ok=false` and fails the sweep.
Each successful or failed date lookup is cached for the sweep's lifetime, so
hundreds of incidental rows cannot repeatedly retry an unavailable date. The
next sweep starts with a fresh cache and can recover from the outage.

This preserves tonight's collection if a game five days out has a malformed
price, or an earlier game still has a post-kickoff row in the pregame feed.
The timestamp guard still rejects those post-kickoff prices. Valid observations
from other games are archived as before, but incidental coverage can be partial.
`sweep_complete` means all market pages were fetched with no fatal row rejects;
it does not promise that all league-wide rows were accepted.

The sweep has an eight-minute timeout. Requests are serialized, paced at least
five seconds apart, and slowed further by rate-limit headers or retry responses.
Conditional requests are disabled; an unexpected 304 fails the sweep because
there is no response cache to replay. Page caps, exhausted retries, fatal row
rejects, and database failures fail the run.
HTTP 200 alone does not mean collection succeeded; the workflow checks all
success and completeness fields. `markets_requested` counts attempted markets;
`markets_completed` counts fully fetched markets. Check `rows_rejected`,
`rows_skipped`, `unmapped_teams`, and the server's error logs after a run.

Future UFC workflows using this key must use the same `sharpapi-odds` concurrency
group. The shared request limiter also serializes callers in a single API
process. Multiple API workers or separate services sharing this key need a
shared limiter before running concurrent collectors.

Observed closes use the latest `provider_timestamp` before `starts_at`, filtered
to active, non-stale rows for that game. `implied_probability` includes vig.
Scorer markets have no complementary No price. Display, settlement, and CLV
computation are deferred. NFL main-line collection is unchanged.

Run the Python coverage with:

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest apps/api/tests -q
```

Run `supabase/tests/nfl_player_prop_odds_test.sql` after the migration in a
disposable PostgreSQL database. It checks atomic rollback, duplicate handling,
shape constraints, append-only guards, permissions, and a 2,300-row insert.
