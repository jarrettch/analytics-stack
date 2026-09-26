# UFC Page

_Created: 2026-09-18_ · _Revised: 2026-09-25 — SharpAPI client now shipped; see
"The SharpAPI client already exists"_

UFC is the first non-team sport to enter the app proper. It gets **its own route
at `/ufc`, not a tab in `SportsDashboard`.**

Every variant of `MatchupData` is built around `away`/`home` team objects with
records, logos, and predicted scores. A fight has two fighters, no venue
advantage, no spread, and no points total. Forcing a card through a component
designed for home-vs-away would cost more than writing a second component, and
would degrade the shared one for the five sports that do fit it.

Tennis is the existing precedent for a non-team sport (`scripts/fetch_tennis_odds.py`),
and it never touched the dashboard. UFC goes further — a real page — but on the
same principle.

## Data sources

Two providers, each doing the thing it is actually good at.

| Need | Source | Notes |
|---|---|---|
| Card identity, fight order, weight class | ESPN `mma/ufc/scoreboard` | One event **per card**. `competitions[]` are the fights. |
| Fighter names and ids | ESPN competitor | `athlete.displayName`, stable ESPN athlete `id`, `order`, `winner`. |
| Odds | SharpAPI `league=ufc` | DraftKings + FanDuel on the free tier. |

### Why UFC uses SharpAPI

The Odds API exposes only `mma_mixed_martial_arts`. There is no UFC key and the
event object has six fields — `id`, `sport_key`, `sport_title`,
`commence_time`, `home_team`, `away_team` — with **no promotion field.** Its
rows mix UFC with RIZIN, PFL, and regional promotions, and the only way to
separate them is to name-match against an ESPN card. Measured on a 13-fight
card: 15 of 26 fighter names matched exactly, 17 of 26 after accent and case
normalization.

SharpAPI has `ufc` as a first-class league. The `event_id` is
`ufc_gigachikadze_joandersonbrito_2026-09-19_b3` — **the promotion is in the
slug.** The filtering problem disappears, and FanDuel comes along with it.

The ESPN join is still required, because SharpAPI is odds-only and carries no
card structure, weight class, or fight order. But the join becomes "attach a
line to a fight on a card I already know is UFC," over a candidate set of ~13
fighter pairs, rather than "guess which promotion these rows belong to."

**This does not replace or retire The Odds API.** It remains the only free
source of Pinnacle and exchange prices, and it continues to back WNBA
(`wnba.py:336`, via `fetch_odds_api_odds`) and the tennis script. The choice
here is scoped to UFC. See `odds_providers.md` for what each provider is
uniquely for.

## SharpAPI free tier

Confirmed against live responses, and echoed in each payload's `meta.tier`:

- books: `draftkings`, `fanduel`
- 60-second delay — irrelevant for a pregame board
- 12 requests/minute, **no monthly cap**
- markets present for UFC: `moneyline`, `total_rounds`, `go_the_distance`,
  `method_of_victory`, `round_betting`, `double_chance`

### Measured request cost

| market | rows | `has_more` |
|---|---|---|
| `moneyline` | 32 | false |
| `total_rounds` | 24 | false |
| `go_the_distance` | 22 | false |
| `method_of_victory` | 100 | **true** (120 total) |

v1 pulls the first three: **three requests, one page each, twice a day** against
a 12/min ceiling. `method_of_victory` is the only market that paginates, because
it emits a row per outcome per fighter per book.

### Parameter names

Use the **documented** names: `market` (not `market_type`), `league`, `is_live`,
`is_main_line`, `limit`, `cursor`. `market_type` happens to work as an alias
today, but see the silent-ignore warning below — an undocumented alias that
stops being honored would fail exactly as quietly as a typo.

`limit` maxes at **200**, and `offset` caps at **500**, which is the documented
reason deep scans must use `cursor`.

### Required request filters

Every collection request **must** include `is_live=false&is_main_line=true`.

Both are real server-side filters, verified: an unfiltered `ncaaf` `total_points`
page returned 70 live rows and 14 alternate lines out of 100; adding
`is_live=false` returned 0 live, and `is_main_line=true` returned 0 alternate
lines.

Without them:

- **live prices would be written to history as pregame observations**, silently
  corrupting the one thing the snapshot table exists to record;
- `total_rounds` would carry alternate lines alongside the main one with **no
  deterministic selection rule**, so which line got stored would depend on
  response ordering.

Do not rely on the parameters alone. **The collector must also reject any
returned row where `is_live` is true**, because SharpAPI silently ignores
unrecognized query parameters rather than erroring — a typo in a filter name
degrades to no filter at all, with no signal.

## The SharpAPI client already exists

**Do not write this.** `fetch_sharp_odds` shipped on 2026-09-20 in
`apps/api/routers/sharp_odds.py` as part of the NFL player-props work
(`49f173f`), which reached implementation before UFC did.

```python
async def fetch_sharp_odds(league: str, market: str, *, sweep: SharpSweep) -> list[dict]
```

It returns every cursor page as a flat row list, or raises `SharpOddsError`
rather than exposing a partial market. UFC calls it and adds nothing.

An earlier draft of this spec placed the helper in `utils.py` and described the
props track as extending it. The order ran the other way: UFC ended the night
before the NFL season opened, props took precedence, and props built the client.
`routers/sharp_odds.py` is the better home regardless — it is a provider client,
not a sport helper.

### What it already does

Everything this spec asked for, and in several places more:

| Requirement | Implementation |
|---|---|
| `X-API-Key` auth | `_request_page`, with `SHARP_API_KEY` / `SHARPAPI_KEY` fallback |
| cursor-only pagination | rejects a missing cursor **and a repeated one**, and empty data alongside `has_more` |
| validates `has_more` | raises unless it is strictly a `bool` |
| page cap | `MAX_PAGES = 40`; logs **and raises**, so a truncated sweep can never look complete |
| `is_live=false&is_main_line=true` | baked into the default params |
| `limit` 200 | default |
| `meta.store.reason` | retries `warming` / `store_empty`; raises on empty without an authoritative `no_match` |
| rate-limit headers | logged, **and** used for pacing |

It also pins `sportsbook=draftkings,fanduel`, which this spec never specified and
which correctly scopes the free tier.

### Pacing, which this spec did not anticipate

The spec said no rate-limiting logic was needed, since UFC is three requests
twice a day against 12/min. Props needed it, so it exists and is **process-wide**:
a module-level lock, a `REQUEST_INTERVAL` floor of 5 seconds, and a delay that
spreads `X-RateLimit-Remaining` across the reset window.

Consequences for UFC:

- a three-market sweep takes **at least 15 seconds**. Budget for it in the cron
  and in the Render wake-up, rather than treating collection as instant.
- UFC and props **share the pacing lock**. A UFC sweep running while a props
  sweep is mid-flight will wait. Do not schedule them at the same minute.

### Conditional requests were deliberately dropped

This spec previously required storing an ETag per `(league, market)` and sending
`If-None-Match`. **The shipped client does the opposite**: its docstring opens
"No conditional requests or cached rows," and a 304 raises
`SharpOddsError("Unexpected 304: archive sweeps require full responses")`.

That is correct, not an oversight. An archive sweep needs the complete current
state on every run, and a 304 returns no rows at all — there is nothing to
append.

**Keep it that way for UFC.** The ETag rationale here was bandwidth, which is
meaningless at three small requests twice a day, and dropping it makes UFC
simpler: every capture returns full rows for every market, so there is no
partial-refresh hole to defend against. The retention rules below are written
for a world with no 304s.

## Page behavior

- **Next numbered or Fight Night card only.** The card name and date are the
  page header, e.g. "Noche UFC: Silva vs. Delgado". Multiple cards is a
  date-picker problem and the app has no date picker anywhere.

### Which events count as a card

ESPN's UFC calendar is **not UFC-only**. `leagues[0].calendar` currently holds
52 entries, of which **10 are Dana White's Contender Series** ("Dana White's
Contender Series: Season 10, Week 1", and so on).

Contender Series events are **excluded** from this page. They are a development
showcase with different stakes, thin and late markets, and they would displace
the actual next card whenever one falls on the nearer Tuesday.

The rule: include an event whose calendar label starts with `UFC ` (numbered
cards), or contains `UFC Fight Night`, or is a branded special such as
`Noche UFC`. Exclude anything matching `Contender Series`. Log any event that
matches neither rule rather than silently dropping it — the UFC's branding
changes, and a new format should surface as a warning, not as a missing card.

### Finding the next card

Do not rely on the scoreboard's undocumented default. Read
`leagues[0].calendar`, filter by the rule above, and select the first entry at
or after today's ET date. Fetch that card's scoreboard explicitly by date.

This mirrors `_next_scoreboard` in `nfl.py`, which looks ahead rather than
assuming today is populated — with the difference that UFC's gaps are measured
in weeks, not days, so a bounded day-by-day lookahead is the wrong shape here.
The calendar is a published list; use it.
- **The full ESPN card renders**, in ESPN's fight order, with weight class from
  `competition.type.abbreviation`.
- **Fights with no matching line render "no line", not omitted.** On a measured
  13-fight card, six fighters had no counterpart in the odds feed at all —
  prelims are not all priced. A UFC card reads as an ordered event; dropping
  half of it looks broken rather than sparse.

This inverts the matching problem usefully: **iterate ESPN's fights and attach a
line when one matches.** A failed match degrades to "no line shown" rather than
a missing fight.

## Name matching

Fighter names are joined between ESPN and SharpAPI within a single card, so the
candidate set is ~13 pairs rather than a global match.

The normalizer handles, in order: accent stripping, case folding, hyphen-to-space
(`Waldo Cortes-Acosta` / `Waldo Cortes Acosta`), and token-subset matching
(`Jose Delgado` / `Jose Miguel Delgado`).

Reversed name order (`Rongzhu` / `Zhu Rong`) is a **known, accepted miss.** It
degrades to "no line", which is the designed failure mode.

### The match is fight-level, one-to-one, and rejects ambiguity

**Never attach the first individual name that matches.** Token-subset matching
in particular can match more than one fighter — `Silva` is a subset of both
`Jean Silva` and `Bruno Silva`, and MMA cards routinely carry both.

A candidate SharpAPI row is accepted only when **all** of these hold:

1. **Both** ESPN fighters match, not one.
2. They match **two distinct** SharpAPI fighters — never the same row twice.
3. The match is **order-independent**: SharpAPI's `home_team` / `away_team`
   assignment carries no meaning for a fight and must not be trusted to align
   with ESPN's `order`.
4. Each SharpAPI row is **consumed at most once** per card. Once bound to a
   fight, it is removed from the candidate pool.
5. **Any fighter with more than one candidate match is rejected**, and the whole
   fight falls through to "no line". Ambiguity is never broken by a tiebreak,
   ordering heuristic, or edit distance.

Rule 5 is the one that feels excessive and is not. A false-positive join
persists one fight's price under **another fight's ESPN competition id**, into
an append-only table with a truncate guard. It cannot be deleted, it cannot be
corrected, and it will look like a legitimate observation forever. "No line" on
a prelim costs nothing by comparison.

Log every unmatched pair **and every ambiguity rejection, separately.** Without
the distinction there is no way to tell a formatting bug from a fight the books
have not priced from a name collision that needs a manual alias — and all three
need different responses.

## Types

UFC deliberately shares no type with `MatchupData`. A fight has no home side, no
spread, and no points total; the union's variants all assume otherwise.

```ts
export interface UFCFighterData {
  name: string
  espnAthleteId: string
  moneyline: number | null
  winProbability: number | null   // de-vigged from the moneyline pair
  winner: boolean | null          // null until the fight is final
}

export interface UFCFightData {
  fightId: string                 // ESPN competition id
  order: number                   // ESPN fight order; the card renders in this order
  weightClass: string             // competition.type.abbreviation, e.g. "W Strawweight"
  status: string                  // display text, e.g. "10:15 PM ET", "Final"
  gameStatus: number              // 1 scheduled, 2 live, 3 final — matches every other sport
  bookmaker: string | null        // which book priced this fight; null when unpriced
  fighters: [UFCFighterData, UFCFighterData]
  totalRounds: {
    line: number
    overOdds: number | null
    underOdds: number | null
  } | null
  goesTheDistance: {
    yesOdds: number | null
    noOdds: number | null
  } | null
}

export interface UFCCardData {
  cardId: string                  // ESPN event id
  name: string                    // "Noche UFC: Silva vs. Delgado"
  date: string
  venue: string
  broadcast: string
  fights: UFCFightData[]
}
```

`gameStatus` keeps the 1/2/3 convention every other sport uses, so live-state
logic reads the same way even though nothing else is shared.

### Book handling

**Both books are persisted; one book is displayed per fight.**

History keeps both, so the DraftKings/FanDuel spread survives for later
comparison. Display picks exactly one, because a moneyline from DK beside a
total from FD is two different opinions presented as one.

**Selection rule, in order:**

1. Walk `["draftkings", "fanduel"]` in order.
2. Select the **first book offering a complete moneyline pair** — both fighters
   priced. A one-sided moneyline does not qualify.
3. Every displayed market for that fight comes from that book and no other.
4. If neither book has a complete pair, `bookmaker` is `null` and the fight
   renders fully unpriced — **even if a book has a total or distance price.**

Step 4 is the part that looks wasteful and is deliberate. Showing a total from a
book whose moneyline was rejected would reintroduce exactly the cross-book mix
the rule exists to prevent, and a totals line with no moneyline beside it is not
usable for the thing this page is for. The moneyline is the anchor market; a
book that cannot price it does not get to price the rest.

This makes `UFCFightData.bookmaker` a **fight-level** field with one meaning:
the book every price on this fight came from. If markets ever need to come from
different books, that is a schema change — a `bookmaker` on each market — not a
loosening of this rule.

### Missing prices

Once a book is selected by the rule above, markets are evaluated **within that
book only**:

- selected book has no `total_rounds` row → `totalRounds` null;
- selected book has no `go_the_distance` row → `goesTheDistance` null;
- **no book qualified** → `bookmaker` null, both fighters' `moneyline` and
  `winProbability` null, and `totalRounds` / `goesTheDistance` null.

All render `--`, never a fabricated or league-average value. A fight with a
moneyline and no total is the normal case for prelims.

The absence of a market is never filled from the other book.

### Example stored `odds_data`

The per-book market bundle. `odds_snapshots` stores one row per fight **per
book** holding exactly this object; `ufc_odds` stores one row **per fight** with
this object nested under each book key. See Persistence below.

```json
{
  "fighterA": "Giga Chikadze",
  "fighterB": "Joanderson Brito",
  "fighterAEspnId": "4356421",
  "fighterBEspnId": "4685102",
  "fighterAMoneyline": 320,
  "fighterBMoneyline": -410,
  "fighterAWinProbability": 0.222,
  "fighterBWinProbability": 0.778,
  "totalRounds": 1.5,
  "overOdds": -150,
  "underOdds": 120,
  "goesDistanceYes": 200,
  "goesDistanceNo": -260,
  "sharpEventUuid": "3d42aeaaaf8f3a7f"
}
```

This is the **per-book bundle**, keyed by `matchup_key` = the ESPN competition
id (`"401897736"`) with the ESPN card date. The two tables nest it differently:

- **`odds_snapshots`** — one row per book, this object as `odds_data`, with the
  book named in the table's own `bookmaker` column.
- **`ufc_odds`** — one row per fight, `odds_data` being
  `{"draftkings": {…}, "fanduel": {…}}` with this object under each key, because
  that table's primary key has no room for a bookmaker.

Fighter names and ESPN athlete ids live in the payload because the key itself is
opaque. `sharpEventUuid` is kept only for debugging a bad join, and differs per
book by design — it is never an identity.

`fighterA` is the fighter at ESPN `order: 1`, so the ordering is stable across
captures and independent of how SharpAPI happened to fill `home_team` /
`away_team`. Win probabilities are de-vigged from the moneyline pair with the
existing `de_vig_ml` — SharpAPI provides `odds_probability`, but that is the raw
implied probability with vig still in it.

### Example `/api/ufc/next-card` response

```json
{
  "cardId": "600059993",
  "name": "Noche UFC: Silva vs. Delgado",
  "date": "2026-09-13T22:00Z",
  "venue": "T-Mobile Arena",
  "broadcast": "ESPN+",
  "fights": [
    {
      "fightId": "401611234",
      "order": 1,
      "weightClass": "Featherweight",
      "status": "10:15 PM ET",
      "gameStatus": 1,
      "bookmaker": "draftkings",
      "fighters": [
        { "name": "Jean Silva", "espnAthleteId": "4915123",
          "moneyline": -275, "winProbability": 0.717, "winner": null },
        { "name": "Jose Miguel Delgado", "espnAthleteId": "5201887",
          "moneyline": 225, "winProbability": 0.283, "winner": null }
      ],
      "totalRounds": { "line": 1.5, "overOdds": -145, "underOdds": 115 },
      "goesTheDistance": { "yesOdds": 180, "noOdds": -240 }
    },
    {
      "fightId": "401611240",
      "order": 7,
      "weightClass": "W Strawweight",
      "status": "7:30 PM ET",
      "gameStatus": 1,
      "bookmaker": null,
      "fighters": [
        { "name": "Jessie Rosas", "espnAthleteId": "5310044",
          "moneyline": null, "winProbability": null, "winner": null },
        { "name": "Regina Tarin", "espnAthleteId": "5298771",
          "moneyline": null, "winProbability": null, "winner": null }
      ],
      "totalRounds": null,
      "goesTheDistance": null
    }
  ]
}
```

The second fight is the unpriced-prelim case, which is common — six of thirteen
fighters on a measured card had no odds row at all. It renders in card order
with `--` in every price slot, not omitted.

## Persistence

Changed pregame observations append to `odds_snapshots` with
`provider = 'sharpapi'` and `bookmaker` of `draftkings` or `fanduel` — **one row
per book**, which that table already supports via its `bookmaker` column.

`ufc_odds` is different, and must not be read as "one row per book." It mirrors
`nfl_odds`, whose primary key is **`(game_date, matchup_key)` only** — no
bookmaker. Writing two rows per fight there would have FanDuel silently
overwrite DraftKings on upsert, and `load_persisted_odds` would collapse them
regardless, since it keys its map on `(date, matchup_key)`.

So **`ufc_odds` stores both books bundled inside one `odds_data` JSON row**,
keyed by book:

```json
{ "draftkings": { ...market fields... },
  "fanduel":    { ...market fields... } }
```

This keeps `ufc_odds` structurally identical to every other sport's latest-value
table and lets UFC reuse `load_persisted_odds` unchanged — no `bookmaker`
column, no three-part primary key, no UFC-specific loader.

The split is deliberate: the **latest-value cache bundles books** because its
key cannot express them; the **history table separates them** because its key
can.

### Assemble the whole bundle before writing anything

`save_persisted_odds` (`utils.py:269`) passes `odds_data` **whole** to
`upsert` — it replaces the column, and does not deep-merge books or markets.
UFC collects across three market requests × two books, so a naive
write-as-you-go collector would have each market's upsert **erase the previous
market's data and the other book along with it.**

The collector must therefore:

1. **Start from the persisted bundle.** Load `ufc_odds` via
   `load_persisted_odds` before issuing any request. Note that this **raises**
   after three failed attempts rather than returning a partial map, so a failed
   seed must abort the sweep — assembling a bundle without the persisted base
   would overwrite good data with whatever a single market happened to return.
2. **Fetch all three markets**, accumulating into an in-memory structure keyed
   by **ESPN fight id → book → market fields**, layering fresh values over the
   persisted ones.
3. **Write once per fight.** One `ufc_odds` upsert carrying the fully assembled
   two-book bundle. Never one upsert per market response.
4. **Snapshot from the assembled bundle**, not per market response — one
   `odds_snapshots` row per fight per book, each holding that book's complete
   market set.

Writing snapshots per market response would also defeat the consecutive-value
dedup in `save_odds_snapshots`, since each partial bundle would hash
differently from the last and every market request would append a row.

### Retention: three distinct cases

The shipped client sends no conditional requests, so **a 304 cannot occur** and
`fetch_sharp_odds` raises if one ever arrives. Every successful market request
returns complete current rows.

That removes the partial-refresh hazard an earlier draft of this spec spent a
section defending against, and with it the fourth retention case. Three remain,
and they are still genuinely different from each other:

| Case | Meaning | Rule |
|---|---|---|
| **`no_match` on a market request** | the whole query matched no rows | Keep persisted, but mark **every** fight's market from that request stale. Not a board-wide withdrawal. |
| **Fight absent from an otherwise populated response** | that fight's market specifically is gone | Mark **that** market stale. The books likely pulled it. |
| **Fight started, so it left the pregame feed** | expected, not a withdrawal | Freeze the last pregame value as final. Never mark stale. |

Distinguish the last two by the fight's start time against the capture time.
That check is already needed for the pregame-only rule, so it costs nothing.

Seeding the bundle from persisted values (step 1 above) is still required — not
for 304s any more, but because `no_match` and an absent fight both leave a market
with no fresh row, and the persisted value has to survive.

A raised `SharpOddsError` is a **failed sweep, not an empty board.** The client
refuses to return a partial market, so the collector must treat the exception as
"write nothing for this capture" rather than assembling a bundle from whichever
markets happened to succeed. Persisted values carry forward untouched.

### Stale prices are "last observed", never current

Any market retained through `no_match` or an unexplained absence carries:

```json
{ "lastObservedAt": "2026-09-19T14:02:11Z", "isStale": true }
```

Without this, a **withdrawn moneyline keeps qualifying its book for display**
under the selection rule above — the page would show a confident price for a
market nobody is offering, and nothing on screen would say so. That is worse
than showing no line, because it looks like information.

The display rules follow:

- a stale market renders with a staleness indicator, not as a live price;
- **a stale moneyline does not qualify a book** in the selection rule. If the
  only complete moneyline pair is stale, the fight falls through to
  `bookmaker: null` and renders unpriced.

A fight frozen because it started is **not** stale — that is the correct final
pregame price, and it is exactly what the history table exists to preserve.

### `matchup_key` is ESPN-derived, never SharpAPI's `event_id`

**SharpAPI does not deduplicate a fight across books.** Verified on
Dvalishvili vs Yan:

| book | `event_id` | `event_uuid` | `event_start_time` |
|---|---|---|---|
| draftkings | `ufc_merabdvalishvili_petryan_2026-10-25_b0` | `c55b9cf0ba3eeb6f` | `2026-10-25T03:30Z` |
| fanduel | `ufc_merabdvalishvili_petryan_2026-10-24_b3` | `3d42aeaaaf8f3a7f` | `2026-10-24T19:45Z` |

One fight, two ids, two uuids, two external ids, and **two different dates**.
The `_b0` / `_b3` suffix tracks the book, not the bout.

Using `event_id` as `matchup_key` would split a single fight's line history
across two permanent keys, and the differing start times would fragment
`game_date` alongside it — so `save_odds_snapshots`, which dedups on
`(sport, provider, bookmaker, game_date, matchup_key)`, could never match a
fight against itself. `odds_snapshots` is append-only with a truncate guard, so
this is not repairable after the fact.

Instead:

- **`matchup_key`** is the **ESPN competition id** — the same value exposed as
  `UFCFightData.fightId`, e.g. `401897736`. Verified unique: a 13-fight card
  returns 13 distinct competition ids.

  A fighter-surname slug was considered and rejected. `dvalishvili_v_yan` reads
  better, but MMA has deep surname collisions (Silva, Nunes, Oliveira, Santos),
  and two fights sharing a surname pair would collide on a **primary key** —
  something athlete ids inside `odds_data` cannot prevent, since the database
  never sees them. NCAAF can use readable abbreviations because ESPN guarantees
  they are unique; fights have no readable unique option, so correctness wins.
- **`game_date`** is the **ESPN card date**, not either book's
  `event_start_time`, so both books' rows land on the same day.
- **Fighter names and ESPN athlete ids** go inside the `odds_data` payload, so
  an opaque key stays debuggable.

This keeps ESPN as the single source of identity for the whole page, which it
already is for card structure, fight order, and weight class. SharpAPI supplies
prices and nothing else.

The provider's own ids are still worth keeping for debugging: store
`event_uuid` per book inside `odds_data`.

## Endpoints

```text
GET  /api/ufc/next-card
POST /api/ufc/cron/fetch-odds
```

The cron endpoint requires `Authorization: Bearer $CRON_SECRET` and returns 401
without it, or 503 when the server has no `CRON_SECRET` configured.

## Cron cadence

Its own workflow, twice daily, same reasoning as NCAAF: keep zero-SGO-cost
sports out of `fetch-odds.yml` so the entity-budget surface stays small.

Cards are roughly weekly, but **there is no such thing as a dark day here.** The
page targets the *next* card rather than today's, and SharpAPI returns upcoming
fights regardless of date — a `league=ufc` pull on a Tuesday in October returns
the following Saturday's lines.

That makes a daily run useful rather than wasteful: it accumulates the full
line-movement path across the week before a card, which is the window where
movement is worth having. There is no monthly cap to spend against.

A genuinely empty response is either a real gap between cards or a transient
store state — see `meta.store.reason` above. Do not conflate them.

## Deferred

- **Slate Prep integration.** `formatGame` in `prep/page.tsx` assumes away/home
  with a spread, and a fight has neither. A non-team prep format is real work,
  worth doing once the page exists and the fight rows have proven what they
  should say. Copying from the UFC page by hand is a weekly, low-effort
  stopgap.
- `method_of_victory` and `round_betting` — both paginate, and neither has a
  display design yet. Pagination itself is no longer a blocker: the shipped
  client handles it, so adding a market is a fourth call plus a render.
- fighter records, reach/stance/camp context, images
- multiple upcoming cards, and any date picker
- live/in-fight updates
- `/odds/delta?since=` as a collection model. It returns only changed rows plus
  a `removed` list and is a better fit for odds history than snapshot-and-hash —
  but adopting it for one sport would split how history is gathered across the
  app.
- `/health`'s `newest_odds_age_s` for freshness monitoring
