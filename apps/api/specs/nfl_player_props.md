# NFL Player Props

_Created: 2026-09-19_
_Revised: 2026-09-20 after implementation review, including runner-side gating and scoped row failures_

The props archive. This is the track `ufc_page.md` defers to: "Props will
genuinely need deep pagination and may need real rate limiting; those get added
there, against a function that already works."

The purpose is **accumulating a line-movement history nobody can buy back**.
Historical NFL prop data starts at roughly $200/month from vendors whose own NFL
prop archives only begin with the 2026 season. Every week not captured is
permanently gone. Display is out of scope for v1.

## Why SharpAPI and not SGO

An arithmetic constraint, not a preference.

SGO's amateur tier is **2,500 entities per month**. A partial single-game,
single-book prop probe returned 25 rows; a full slate is thousands. Measured SGO
usage on 2026-09-18 was 1,552/2,500 at roughly 142 entities/day — the cycle
exhausts around Sept 25–27 on MLB alone. One Sunday prop sweep would exceed the
month.

SharpAPI is **request-billed with no monthly cap**, 12 req/min on the free tier.
That is the only reason props can exist here.

The cost is book breadth: DraftKings and FanDuel only. BetOnline and Bovada are
unavailable for NFL props, and that is not a choice — SGO could never have
carried this market at this tier.

**Whether NFL main lines also move to SharpAPI is a separate decision and is
explicitly out of scope here.** It requires resolving book selection, how two
books fit `nfl_odds`'s one-row-per-game contract, de-vigging and dashboard win
probabilities, `odds_snapshots` behavior, persisted fallback through kickoff,
and whether SGO remains a fallback. See `odds_providers.md`. This spec changes
nothing about main lines.

## Market catalog

Verified live, 2026-09-19/20, `is_live=false&is_main_line=true`:

| market alias | rows @ limit 200 | `has_more` | shape |
|---|---|---|---|
| `player_receptions` | 200 | true | line-based |
| `player_receiving_yards` | 200 | true | line-based |
| `player_rushing_yards` | 200 | true | line-based |
| `player_passing_yards` | 93 | false | line-based |
| `anytime_touchdown_scorer` | paginates | true | **scorer** |
| `player_anytime_td` | — | — | **not a valid alias** |
| `player_touchdowns` | 0 | false | valid alias, currently empty |

### Two market shapes, two normalization paths

Anytime-TD is not shaped like the other props, and **the two books do not agree
with each other** inside the scorer market. Measured on a 200-row live sample:

| field | line-based | scorer (player) | scorer (DK defense) |
|---|---|---|---|
| `line` | `1.5` | `null` | `null` |
| `selection` | `"Over"` / `"Under"` | player name | `"MIN Vikings D/ST"` |
| `player_name` | player name | player name | **`"Anytime TD"`** |
| `selection_type` | `"over"` / `"under"` | `"other"` | **`"away"` / `"home"`** |
| `is_player_prop` | `true` | `false` | `false` |
| `stat_category` | `"receptions"` | `"anytime_td"` | `"anytime_td"` |

That sample split `selection_type` as `other` 186, `away` 7, `home` 7 — all 14
non-`other` rows DraftKings defenses, every one carrying
`player_name: "Anytime TD"`, the market name rather than the selection.

Three rules follow, and none of them are optional:

**Never filter on `is_player_prop`.** It is false for the entire
highest-volume NFL prop market.

**Derive shape from the requested market alias plus `line is null`** — never
from `selection_type`. `selection_type` is `other` for most scorer rows and
`away`/`home` for DraftKings defenses, so it cannot identify the shape.

**Normalize per shape, not per field preference:**

| shape | `selection_name` | `side` |
|---|---|---|
| line | `player_name` | `selection_type` |
| scorer | **`selection`** | **always `null`**, whatever `selection_type` says |

An earlier draft of this spec said to prefer `player_name` with `selection` as a
defensive fallback. That is backwards for the scorer shape and would have
collapsed all 14 DraftKings defense rows into a single selection named
`"Anytime TD"` — fourteen distinct markets silently merged, in an append-only
table.

The raw provider value is kept in a nullable `provider_player_name` column so
the derivation stays auditable.

### Selections are not always players

Both books price team defenses in the scorer market, in different formats:
FanDuel as `"Chicago Defense"`, DraftKings as `"MIN Vikings D/ST"`. The column
is therefore `selection_name`, not `player_name` — a "player" prop table
silently holding team entities under a player column breaks any future roster
join in a way that reads as a data bug rather than a modeling decision.

## Identity

### `matchup_key` is the existing NFL convention

NFL builds `matchup_key` as `f"{away_tri}@{home_tri}"` (`nfl.py:470`,
`utils.py:578`, `utils.py:777`). Props use the same key.

SharpAPI's event id carries a **book suffix** — the probe returned
`nfl_bears_vikings_2026-09-20_b2` for DraftKings. UFC established what happens
if that becomes canonical: the same contest gets two ids, two uuids, and
sometimes two dates, permanently splitting one game's history in an append-only
table.

Canonical identity and provider identity are **separate columns** and must never
be conflated:

- `matchup_key` — canonical, `{away_tri}@{home_tri}`
- `game_date` — canonical schedule date, not either book's `event_start_time`
- `provider_event_id` — SharpAPI's book-specific `event_id`, debugging only
- `provider_event_uuid` — SharpAPI's `event_uuid`, debugging only

### Team name mapping reuses the existing NFL map

`matchup_key` in `nfl.py:470` is built from **ESPN** `team.abbreviation`, so the
props join must produce byte-identical tri-codes from SharpAPI's display names.

NFL already has its own mapper at **`nfl.py:78`** — an earlier draft of this
spec wrongly said only MLB and NBA had one. `_SGO_TEAM_TO_TRI` (`nfl.py:65`) is
generated from `NFL_TEAM_NAMES` as `full_name.upper().replace(" ", "_")`, so
**all 32 full display names are already keyed correctly.** There is no 32-name
audit to do.

The single defect is on the input side: `get_tricode_from_sgo_team_id` does
`key = (team_id or "").upper()` and strips a `_NFL` suffix, but never converts
spaces to underscores. `"Chicago Bears"` becomes `"CHICAGO BEARS"` and misses
`CHICAGO_BEARS`.

Add a provider-neutral helper **in the NFL module**, wrapping the existing map:

- uppercase and trim
- strip an optional `_NFL` suffix
- **convert spaces to underscores** (the missing step)
- reuse `_SGO_TEAM_TO_TRI` and its existing aliases unchanged
- add `ARI_CARDINALS: "ARI"` — DraftKings returns `"ARI Cardinals"`, and the
  generated map only carries `ARIZONA_CARDINALS`

Do not modify `get_tricode_from_sgo_team_id` itself; `fetch_sgo_odds` is already
passing it as `tricode_for` for the main-line path, and changing its input
handling would alter SGO behavior for a props fix.

### An unmapped team fails the sweep

A row without a canonical `matchup_key` cannot be persisted correctly. Log it,
mark the collection incomplete, and **fail the workflow** — do not insert the
rest.

An unknown matchup might belong to a selected game. The provider's timestamp
alone cannot prove otherwise, so unidentified rows remain fatal.

**Known non-selected matchups have a different rule.** Invalid rows whose mapped
away/home pair differs from every selected matchup are omitted and counted as
`rows_skipped`. They do not invalidate the selected games' collection. This
includes malformed future-game prices, incidental schedule lookup failures, and
post-kickoff observations left in the pregame feed from earlier games.

Invalid selected-game rows count as `rows_rejected` and prevent all writes.
Selected-game schedule failures also set `schedule_ok=false`. All market pages
must still be fetched; a failed page has unknown scope and remains fatal.

### The sweep archives every game returned, not only the window games

SharpAPI's requests are league-level: a sweep triggered by one game returns
props for **every** upcoming game, at no additional request cost.

**Valid rows are persisted.** Discarding them would throw away line history that
has already been paid for, in an archive whose entire purpose is history that
cannot be bought back later — and it hands back the intraday movement path that
the schedule section defers as future work.

The consequence is that a collection is **a league-wide observation triggered by
the eligibility window**, not a snapshot scoped to the selected games. The
response separates the two: `games_selected` is the trigger set,
`games_persisted` is the collection's actual contents.

Incidental league-wide coverage can be partial when `rows_skipped > 0`.
`sweep_complete` guarantees complete page traversal and no fatal selected-game
or unidentified row failures, not acceptance of every incidental row.

Readers resolving a close must filter by game and `provider_timestamp`
regardless, so this costs them nothing.

### There is no stable player id

SharpAPI supplies no player id. v1 stores `selection_name` verbatim and attempts
no roster join. Do not synthesize a player id — a wrong id in an append-only
table is unrepairable, while a name needing an alias later is a lookup table
away.

## Schema

`mlb_pitcher_k_odds` is the conceptual model but **cannot be cloned**: its
`player_id` is `not null` and SharpAPI has none; `side` is constrained to
`over|under` and cannot hold a scorer; `line` is `not null` and scorer rows have
none; it has no `matchup_key`; and its `event_started`/`event_ended`/
`event_finalized`/`event_cancelled` fields come from SGO status flags SharpAPI
does not supply — so "the same `market_phase` treatment" is undefined here and
is dropped.

```sql
create table if not exists public.nfl_player_prop_odds (
    id bigint generated always as identity primary key,

    -- collection
    collection_id      text        not null,
    captured_at        timestamptz not null,

    -- canonical identity
    game_date          date        not null,
    matchup_key        text        not null,
    starts_at          timestamptz,

    -- provider identity (debugging only, never a key)
    provider           text        not null default 'sharpapi',
    provider_event_id  text        not null,
    provider_event_uuid text,
    market_id          text,
    selection_id       text        not null,

    -- market
    bookmaker_id       text        not null
        check (bookmaker_id in ('draftkings', 'fanduel')),
    market_type        text        not null,
    stat_category      text,
    market_shape       text        not null
        check (market_shape in ('line', 'scorer')),

    -- selection
    selection_name     text        not null,
    selection_raw      text        not null,
    provider_player_name text,
    side               text
        check (side is null or side in ('over', 'under')),
    line               numeric,

    -- price
    american_odds      integer     not null,
    decimal_odds       numeric,
    implied_probability numeric,

    -- provider state
    is_main_line       boolean     not null,
    is_alternate_line  boolean     not null default false,
    is_active          boolean     not null,
    is_stale_pregame_price boolean not null,
    provider_timestamp timestamptz not null,

    created_at         timestamptz not null default now(),

    -- shape invariants
    constraint nfl_player_prop_odds_line_shape check (
        (market_shape = 'line'   and line is not null and side is not null)
        or
        (market_shape = 'scorer' and line is null     and side is null)
    ),

    -- dedup: provider identity, never `line` (see below)
    constraint nfl_player_prop_odds_collection_price_key unique (
        collection_id,
        bookmaker_id,
        selection_id,
        american_odds
    )
);

create index if not exists nfl_player_prop_odds_game_market_idx
    on public.nfl_player_prop_odds (game_date, matchup_key, market_type, captured_at);

create index if not exists nfl_player_prop_odds_selection_idx
    on public.nfl_player_prop_odds (selection_name, market_type, captured_at desc);

create index if not exists nfl_player_prop_odds_close_idx
    on public.nfl_player_prop_odds (game_date, matchup_key, provider_timestamp desc)
    where is_stale_pregame_price = false and is_active = true;

alter table public.nfl_player_prop_odds enable row level security;

drop policy if exists "service role manages nfl player prop odds"
    on public.nfl_player_prop_odds;
create policy "service role manages nfl player prop odds"
    on public.nfl_player_prop_odds
    for all to service_role using (true) with check (true);

create or replace function public.reject_nfl_player_prop_odds_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'nfl_player_prop_odds is append-only';
end;
$$;

drop trigger if exists nfl_player_prop_odds_append_only
    on public.nfl_player_prop_odds;
create trigger nfl_player_prop_odds_append_only
before update or delete on public.nfl_player_prop_odds
for each row execute function public.reject_nfl_player_prop_odds_mutation();

drop trigger if exists nfl_player_prop_odds_reject_truncate
    on public.nfl_player_prop_odds;
create trigger nfl_player_prop_odds_reject_truncate
before truncate on public.nfl_player_prop_odds
for each statement execute function public.reject_nfl_player_prop_odds_mutation();
```

### Source mapping

| column | source | notes |
|---|---|---|
| `matchup_key` | derived | tri-code map from `home_team`/`away_team` |
| `game_date` | schedule | never `event_start_time` |
| `provider_event_id` | `event_id` | carries the `_b2` book suffix |
| `selection_id` | `selection_id` | **reject the row if null**, see below |
| `market_shape` | derived | `scorer` when `line is null`, else `line` |
| `selection_name` | **shape-specific** | `player_name` for line, `selection` for scorer |
| `selection_raw` | `selection` | verbatim, for auditing the derivation |
| `provider_player_name` | `player_name` | raw; `"Anytime TD"` on DK defenses |
| `side` | `selection_type` | **always null for `scorer`**, ignoring `away`/`home` |
| `implied_probability` | `odds_probability` | **raw implied, vig included** |
| `provider_timestamp` | `timestamp` | per-row, see close rules |

### Both triggers, deliberately

`mlb_pitcher_k_odds` blocks updates and deletes but has **no truncate guard** —
only `odds_snapshots` has the statement-level one
(`20260813000000_create_nfl_odds_and_history.sql:87-92`). An earlier draft of
this spec wrongly claimed MLB had both. This table copies both. The MLB table
should be fixed separately.

### Why the unique constraint excludes `line`

PostgreSQL treats nulls as distinct in unique constraints by default. A
constraint including `line` would never deduplicate scorer rows, because every
`line = null` is distinct from every other. Repeated anytime-TD prices within a
sweep would each insert.

`selection_id` is the fix and needs no `NULLS NOT DISTINCT`: verified non-null
and unique per row across both market shapes and both books, and it already
encodes market, line, and side. It is therefore `not null` in the schema, and
**the collector must reject and log any row lacking one** — a null would
silently disable dedup for that row.

## Collection semantics

**One `collection_id` per workflow run**, created before the first request and
shared by every market and page, including retried attempts.

The id is **deterministic from the workflow run**, following the existing MLB
pattern (`collection_key=${{ github.run_id }}-today`), so a retried step or a
re-run reuses it and the unique constraint absorbs duplicate inserts rather than
doubling the sweep.

Because the conflict key includes `american_odds`, a retry after a price moves
adds another observation for that selection. A collection groups attempts, not
one instant or one price per selection. Readers must use `provider_timestamp`
and `captured_at` when choosing an observation; `DISTINCT` on collection and
selection alone loses real changes. Record this contract in the table comment.

## Conditional requests are disabled

`fetch_sharp_odds` supports ETag / `If-None-Match`, and **props must not use
it.**

A 304 returns no rows. This collector deliberately keeps no latest-value cache,
so there is nothing to replay under the new `collection_id`. A closing sweep
where prices happened to be unchanged would produce **no closing collection at
all** — silently losing exactly the observation the table exists for, and losing
it most often on quiet markets.

The alternative is durable response caching plus replaying cached rows under the
new collection id, which contradicts the no-cache design. Revisit only if
request volume ever becomes a real constraint, which at 12/min it is not.

This is a props-specific deviation. UFC keeps its ETag behavior, which is
correct there because it has a latest-value cache to fall back on.

## Rate limiting

12 req/min is **one request per five seconds**. A one-second sleep would burst
through the budget in twelve seconds and then start failing — an earlier draft
of this spec specified exactly that and was wrong.

- **Serialize all requests.** No concurrency.
- **Pace from the documented rate-limit headers** (`X-Ratelimit-Remaining`,
  `-Reset`), falling back to a fixed **5-second** interval between request
  starts when headers are absent.
- **Honor 429 and `Retry-After`**, with bounded backoff.
- **Count retries against the budget.** A retried request is a request.
- **The cron endpoint's HTTP timeout must exceed the worst-case sweep.** Five
  markets, several pages each, at 5s spacing plus retries is minutes, not
  seconds. The existing workflows use `--max-time 120`; props need more.

**UFC shares this API key and therefore this rate bucket.** Do not schedule a
props sweep concurrently with the UFC cron.

## Determining the close

The latest qualifying pregame observation before kickoff is the observed close,
matching the note on `mlb_pitcher_k_odds.close_line`.

A row qualifies only when **both** `is_stale_pregame_price = false` **and**
`is_active = true`. Stale is the book's last-known value rather than a live
quote; inactive is a pulled market. Either one treated as a close silently
corrupts every CLV number derived from it.

**Order by `provider_timestamp`, not `captured_at`.** A paginated sweep spans
several minutes, so rows within one collection carry materially different
provider timestamps and `captured_at` would order them by page rather than by
market state.

The free tier delays responses **60 seconds**, so a capture at kickoff reflects
T−60s. Acceptable for CLV, arguably closer to a true close than a snapshot taken
fifteen minutes early. **Never wire this feed into a live +EV path.**

### De-vigging

`de_vig_ml` applies only after pairing over and under from the **same book,
event, selection, market, and line**. `implied_probability` is the raw implied
figure with vig still in it and is never a fair probability on its own.

**Scorer rows cannot be de-vigged from this payload.** There is no complementary
"No" price in the anytime-TD market, so only the raw implied probability is
available. Store it and do not derive a fair probability.

## Capture schedule

Fixed weekly cron windows do **not** capture every close. The NFL schedule
includes London games, Thanksgiving, Black Friday, late-season Saturdays,
Christmas, Monday doubleheaders, and Saturday playoff games — none of which fall
on a Thursday/Sunday/Monday evening grid. An earlier draft listed five fixed
windows and claimed they covered every close; they do not.

**v1 is schedule-driven.**

- **Polling cadence: every 30 minutes on the GitHub runner**, all year.
- **Eligibility window, evaluated at workflow start:**
  `now + sweep_guard <= kickoff <= now + 90 minutes`, with
  **`sweep_guard = 10 minutes`**
- Each run checks ESPN **on the runner before calling Render, including
  `/health`**. Empty windows never wake the API server. ESPN failures or malformed
  schedules fail the Action without waking Render. Check both ET dates when
  the window crosses midnight.
- Only a qualifying window wakes Render and calls the collector. Pass the gate's
  start timestamp as `window_at`; the API repeats the schedule check and enforces
  the ten-minute guard after warm-up. No month-based cutoff is needed.

The 90 minutes is the **width of the eligibility window**, not an instruction to
capture 90 minutes out. Combined with a 30-minute cadence, the final qualifying
capture normally lands **roughly 10–40 minutes before kickoff**, and at least one
further scheduled attempt remains if a run fails.

`sweep_guard` exists because a bare `now < kickoff` would let a multi-minute
sweep start one minute before kickoff. Pagination at five-second pacing, plus
cold start and retries, means later markets would then be fetched *after* the
game began — and since every request carries `is_live=false`, those markets
would return nothing rather than a pregame price. The result is a collection
whose earlier markets hold a close and whose later markets are empty, which is
worse than no collection at all. Ten minutes is conservative for v1.

Hourly polling was considered and rejected: it allows a final observation up to
60 minutes stale, which is too loose for something recorded as an observed
close.

This reuses the ESPN lookahead already in `nfl.py` and adapts to every schedule
irregularity without maintaining a cron table.

Intraday captures filling the open-to-close movement path come later. Closes
first — a series that includes every close beats a denser one that misses them.

## Fetch everything, validate everything, then insert once

The selected games' collection must pass validation before any rows are written.
Incidental games may contribute valid rows even when some of their rows fail.

Required order:

1. **Fetch** every market and every page.
2. **Normalize and validate** every row — shape invariant, `selection_id`
   present, team names mapped.
3. **Fail without inserting** if any page is incomplete or a selected-game or
   unidentified row is rejected. Count invalid known non-selected rows separately
   as `rows_skipped` and omit them.
4. **Persist the accepted row set atomically**, in a single transaction.

An earlier draft left the order unspecified and treated `rows_rejected > 0` as
non-fatal. Those two together contradict the guarantee: valid pages inserted
before a later page turns out to contain an unmapped team would leave exactly
the partial collection the spec forbids — permanently, in an append-only table.

**`rows_rejected > 0` therefore sets `sweep_complete = false` and fails the
workflow.** Log both rejects and skips with examples. `rows_skipped > 0` alone
does not fail the sweep. This deliberately limits row-level failures to the
selected window while retaining valid league-wide observations.

A measured sweep is on the order of 2,300 rows, comfortably a single
transactional insert. If that ever stops being true, add a collection manifest
carrying an explicit completed state and require every reader to consume only
completed collections — but do not fall back to incremental inserts without one.

## Endpoint

```text
POST /api/nfl/cron/fetch-player-props?collection_key=<key>
```

Requires `Authorization: Bearer $CRON_SECRET`, returning 401 without it and 503
when the server has no `CRON_SECRET` configured — the same contract as every
other cron endpoint.

`collection_key` is supplied by the workflow and becomes `collection_id`. Follow
the MLB pattern: `${{ github.run_id }}-<window>`, so retries and re-runs reuse
the id and the unique constraint absorbs duplicate inserts.

### Response

Collector failures return HTTP 200 in this codebase, so the workflow must
inspect the body — same as `fetch-nfl-close-odds.yml:65-77` already does for
`early_window_scoreboard_ok`.

```json
{
  "success": true,
  "collection_id": "17384920164-w1600",
  "schedule_ok": true,
  "games_selected": 8,
  "games_persisted": 14,
  "markets_requested": 5,
  "markets_completed": 5,
  "pages_fetched": 14,
  "rows_accepted": 2317,
  "rows_rejected": 0,
  "rows_skipped": 0,
  "rows_deduplicated": 41,
  "rows_inserted": 2276,
  "unmapped_teams": [],
  "sweep_complete": true,
  "persistence_ok": true,
  "rate_limit": { "remaining": 4, "reset_s": 22, "throttled_waits": 3 }
}
```

**The workflow fails when any of these hold:** `success` is not true,
`schedule_ok` is false, `sweep_complete` is false, `persistence_ok` is false,
`unmapped_teams` is non-empty, or `rows_rejected` is non-zero.
It also requires `markets_completed == markets_requested`. The former counts
fully fetched markets; the latter counts attempted markets, including a failed
fetch. Non-fatal `rows_skipped` is reported without failing the workflow.

### `schedule_ok` is what makes a zero-game run safe

`schedule_ok` covers eligibility-window lookups and selected-game identity
resolution. Incidental-game lookup failures count as skips and do not clear it.
Each date lookup, including failures, is cached for the current sweep only.

An ESPN failure produces an empty game list, which is indistinguishable from a
genuinely empty eligibility window unless the two are reported separately. Left
undistinguished, a scoreboard outage would take the normal no-op path and
**silently skip a close.**

| state | `schedule_ok` | `success` | outcome |
|---|---|---|---|
| schedule fetched, no game in window | `true` | `true` | successful no-op, no SharpAPI request issued |
| eligibility-window or selected-game schedule lookup failed | `false` | `false` | `sweep_complete: false`, workflow fails |

Only return the zero-game result **after** confirming the schedule request
succeeded. This mirrors the `early_window_scoreboard_ok` guard the NFL
close-odds workflow already enforces.

### `games_selected` versus `games_persisted`

`games_selected` is the count that qualified under the eligibility window — the
**trigger** for the sweep. `games_persisted` is how many distinct
`matchup_key`s the collection actually contains, which is normally larger
because SharpAPI's league-level requests return every upcoming game.

Keeping both means `games_selected: 0` remains meaningful as "nothing was due"
while the collection's real scope stays visible.

## Deferred

- **Display.** No page, no board, no endpoint beyond the cron. Capture is the
  only part with a deadline.
- **Alternate lines.** `is_main_line=true` for v1; the column is stored, so
  enabling them is a filter change rather than a migration.
- **Settlement and grading.** The MLB pitcher-K settlement pipeline is the model
  and is substantial. Until it exists, rows are prices without outcomes — enough
  for line movement, not for hit rates.
- **Roster/player-id join.** Blocked on a stable id, and complicated by team
  defenses appearing as selections.
- **CLV computation.** Needs settlement plus a bet log. The CLV *distribution*
  rather than a hit-rate percentage is the genuinely novel version.
- **NFL main lines on SharpAPI.** A separate project, scoped in
  `odds_providers.md`.
- **NCAAF and UFC props.** Would use this table's generalized successor.
- **`player_touchdowns`.** A valid alias returning zero rows today. Re-probe
  periodically rather than assuming it is permanently empty.
