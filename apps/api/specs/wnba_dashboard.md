# WNBA Dashboard

_Created: 2026-05-28 (documents the shipped build)_

Mirrors the NBA dashboard structurally — same `/today` + `/cron/fetch-odds`
shape, same card layout, same `nba_api` plumbing. The one architectural
departure is odds: **WNBA isn't on our SportsGameOdds tier, so it uses The Odds
API instead of SGO.** This doc captures the decisions that differ from NBA so we
don't re-discover them.

---

## Data sources (all free, no auth except Odds API key)

| Need | Source | Notes |
|---|---|---|
| Scoreboard | `cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_10.json` | The `_10` suffix is WNBA on the **live** endpoint (it's NBA on the static `scheduleLeagueV2_10.json` — don't confuse them). Identical payload shape to NBA's `_00.json`; `WNBAScoreBoard` just overrides `ScoreBoard.endpoint_url`. Same `www.nba.com` Referer header requirement as NBA. |
| Standings | `LeagueStandingsV3(league_id="10", season=<year>)` | `TeamName` is the nickname ("Aces"), no `TeamAbbreviation` field — map nickname → tricode. |
| Advanced stats | `LeagueDashTeamStats(measure_type_detailed_defense="Advanced", league_id_nullable="10", season=<year>)` | `OFF_RATING`/`DEF_RATING`/`PACE` present, `TEAM_NAME` is the full name. |
| Odds | The Odds API `sports/basketball_wnba/odds` | `markets=h2h,spreads,totals`, `regions=us`, `oddsFormat=american`. Per-bookmaker outcome arrays (not SGO's flat oddID keys). No `fairOdds` — win prob is de-vigged from the book ML pair. Free tier: 500 req/mo. |

**Season string:** WNBA seasons are single calendar years (May–Oct), so the
season is just the year (`"2026"`), **not** the NBA `"2025-26"` format.
`wnba_season()` returns `str(datetime.now(ET).year)`; revisit only if we ever
need historical/offseason lookups.

## Odds provider — `utils.fetch_odds_api_odds`

Parallel to `fetch_sgo_odds`, same signature shape and **same normalized output
dict**, so everything downstream (persistence, assembly, frontend) is unchanged:

```python
return await fetch_odds_api_odds(
    sport_key="basketball_wnba",
    table_name="wnba_odds",
    tricode_for=get_tricode_from_name,   # full team name -> tricode
    target_date=target_date,
)
```

- **Book preference** (`ODDS_API_BOOKS`): `pinnacle, draftkings, fanduel, betmgm, betrivers, bovada, mybookieag`, first available per event. BetOnline/Pinnacle are **not** carried for WNBA on the free tier, so the chain leads with DraftKings/FanDuel.
- **Window:** `commenceTimeFrom`/`commenceTimeTo` scope to a single ET day (UTC bounds), mirroring the SGO path so we only pull that day's slate.
- **Win prob:** The Odds API has no `fairOdds`, so `calculate_win_probability_from_odds` de-vigs the book ML pair (no extra credit cost).
- Requires `ODDS_API_KEY` in the environment.

## Team identifiers

15 teams (2026 added Toronto Tempo + Portland Fire). **`nba_api`'s static
`get_wnba_teams()` is stale** — only 13 teams, and lists Phoenix as `PHO`. The
canonical tricodes the live scoreboard uses were harvested from
`cdn.wnba.com/static/json/staticData/scheduleLeagueV2.json`. All maps live in
`wnba.py`'s `WNBA_TEAMS` table `tricode: (team_id, full_name, nickname, arena, espn_slug)`:

- `get_tricode_from_name(name)` — full name → tricode, with a unique-nickname last-token fallback ("LA Sparks" → Sparks → `LAS`). Used by the odds provider.
- ESPN CDN logo slugs differ from nba.com tricodes: `GSV→gs, LAS→la, LVA→lv, NYL→ny, PDX→por, WAS→wsh` (rest match lowercased). The API payload emits `espnSlug` per team so the frontend stays sport-agnostic — same pattern as the `AZ→ari` fix.

## Projection — game-length units fix

`PACE` from the advanced-stats endpoint is normalized **per 48 minutes** (NBA
convention), but WNBA plays **40-minute** games. Without scaling, the naive
ORtg×pace projection runs ~20% hot (~200 totals for an ~165-point league). The
projection rescales by `WNBA_REG_MINUTES / 48`. The projection model is otherwise
the NBA logic verbatim (70% market margin / 30% naive); a proper WNBA calibration
pass remains a follow-up.

## Persistence contract

Same shape as NBA, different table:

- Matchup key: `"AWAY@HOME"` using WNBA tricodes (e.g. `"LVA@SEA"`).
- Supabase table: `wnba_odds`, DDL identical to `nba_odds` — composite unique on `(game_date, matchup_key)`, columns `game_date`/`matchup_key`/`odds_data` 1:1.
- RLS enabled + service-role policy (cleanup.md #1 — every new public table needs it).

## Card data shape

WNBA reuses the **NBA** card shape — `WNBATeamData = NBATeamData` (off/def
rating, pace, last10, score) and `WNBAMatchupData` mirrors `NBAMatchupData`
(quarter clock, same odds fields). `MatchupCard` renders WNBA through the NBA
branches for team stats, live/final score, quarter clock, and ratings.

**Deliberately excluded from v1** (both assume 48-minute NBA math):
- the NBA live win-prob heuristic (`LiveWinProbSection`)
- the EV calculator panel

## Endpoints

- `/api/wnba/today` — assembled game payload (same envelope as `/api/nba/today`)
- `POST /api/wnba/cron/fetch-odds?offset=0|1` — daily/next-day odds prefetch. Requires `Authorization: Bearer $CRON_SECRET`.
- `/api/wnba/debug/standings`, `/api/wnba/debug/scoreboard`

Polling cadence matches NBA: `assembled` TTL is `CACHE_TTL_LIVE` (20s) when any
`gameStatus == 2`, else `CACHE_TTL_DEFAULT` (60s).

## Status

- [x] `utils.fetch_odds_api_odds` provider
- [x] `routers/wnba.py` — scoreboard / standings / advanced stats / odds, assembled `/today`
- [x] Frontend: `wnba` in the sport switcher, `WNBAMatchupData`, MatchupCard wiring, ESPN slugs
- [x] WNBA steps added to `fetch-odds.yml` + `fetch-tomorrow-odds.yml`
- [x] `wnba_odds` Supabase table + RLS service-role policy
- [x] `ODDS_API_KEY` set on Render
- [ ] WNBA-specific projection calibration (currently NBA model + 40-min pace rescale)
