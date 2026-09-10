# NFL Dashboard

_Created: 2026-08-13_

NFL v1 follows the existing multi-sport dashboard contract. It intentionally
ships source-backed schedule, scores, team context, and odds before attempting
proprietary ratings or prediction models.

## Data sources

| Need | Source | Notes |
|---|---|---|
| Slate / live scores | ESPN site scoreboard JSON | Query by `dates=YYYYMMDD`; keyless but undocumented, so isolate behind router helpers and retain stale cache. |
| Records / PF / PA / streak | ESPN standings JSON | During preseason, use the prior regular season as context. The game card's current record still comes from the preseason scoreboard. |
| Odds | SportsGameOdds `leagueID=NFL` | Same normalized ML/spread/total contract as NBA/MLB/NHL. Persist latest rows in `nfl_odds` and changed observations in `odds_snapshots`. |
| Logos | ESPN CDN | Dashboard abbreviation is the slug except Washington (`WSH`) and Las Vegas (`LV`), which ESPN supports directly. |

## Slate behavior

- `GET /api/nfl/week` returns every game in the current NFL week. The route
  anchors on today's ET slate, looks ahead up to seven days when necessary,
  and then requests the anchor event's full ESPN season/week slate.
- `GET /api/nfl/today` remains the calendar-day feed used by Slate Prep. If
  today is dark, it looks ahead up to seven days and returns the next populated
  date. Game-detail lookup uses the weekly feed so every displayed card opens.
- Preseason games are included and labeled `HOF` or `PRE W<n>`.
- Regular-season and postseason games use `WK<n>` / the upstream week number.
- Status normalizes to `gameStatus`: 1 preview, 2 live, 3 final.
- Final spread results distinguish a cover, loss, and exact-margin push. The
  card renders a push as a neutral `P`, matching total pushes.
- Live status displays `Q<n> mm:ss`; overtime displays `OT`.

## Team context

The card must not claim DVOA or another proprietary model. v1 uses:

- current event record from the scoreboard;
- PF/G and PA/G from the context season;
- standings streak and conference;
- nullable yards/game, yards allowed/game, and turnover differential until a
  reliable league-wide feed is deliberately added.

Missing metrics render `--`, never fabricated league-average values.

During preseason, `statsSeason` is the prior year and `seasonType` is
`preseason`, allowing the UI and downstream consumers to distinguish old team
context from the live preseason record.

## Projection label

Projected scores are market-derived when a total/spread exists. A PF/PA blend
is only a fallback. The model label is `MARKET + TEAM STATS`, not `DVOA`.

## Endpoints

```text
GET  /api/nfl/today
GET  /api/nfl/week
GET  /api/nfl/game/{game_id}
POST /api/nfl/cron/fetch-odds?offset=0|1
```

The cron endpoint requires `Authorization: Bearer $CRON_SECRET` and returns 401
without it, or 503 when the server has no `CRON_SECRET` configured.

## Cron cadence

Use the shared daily jobs plus one NFL-specific Sunday capture:

- tomorrow's odds around 2am ET;
- current-day odds around noon ET;
- Sunday's early-window odds around 12:30pm ET;
- current-day odds around 6pm ET.

The Sunday job uses GitHub's native `America/New_York` schedule, so DST does not
require duplicate UTC slots or a runtime wall-clock guard. A delayed workflow
still attempts its collection; the collector's pregame-only rule rejects any
event that has already started. The 12:30 trigger leaves time for the Render
cold-start wake-up before the common 1pm window without introducing 15-minute
polling.
No capture is described as a provider close: the latest successfully stored
pregame observation is the observed close. Additional window-specific polling
remains deferred until its SGO entity cost is measured.

The Sunday workflow calls the cron endpoint with
`verify_early_window=true`; ordinary cron calls do not fetch ESPN solely for
this check. Verification compares ESPN's scheduled 1:00–2:59pm ET matchup keys
with eligible pregame SGO candidate matchup keys, so it does not depend on SGO's
nullable `startsAt` field. It fails when ESPN is unavailable, when an early
slate has fewer matching pregame candidates than scheduled games, or when
snapshot persistence fails. A successful scoreboard with zero early games is
allowed (offseason and some postseason/preseason Sundays are legitimate). A
zero insert count alone is not an error: unchanged lines are intentionally
deduplicated.

## Deferred

- DVOA/EPA/play-success models and ranks
- injuries and depth-chart interpretation
- starting-QB adjustment model
- weather panel (the ESPN slate contains basic weather, but it needs a shared
  weather contract before surfacing)
- public odds-movement UI; v1 first accumulates the history
