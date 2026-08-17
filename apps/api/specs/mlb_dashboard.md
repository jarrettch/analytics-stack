# MLB Dashboard — Implementation Spec

_Last updated: 2026-05-07_

> **Status:** Shipped. This doc is now a historical design reference; consult the code (`routers/mlb.py`, `routers/utils.py`, `apps/visualize-ev/app/page.tsx`) for current behavior.

---

## Status

| Layer | Status |
|---|---|
| `MLBTeamData` / `MLBMatchupData` types | Done |
| `MLBTeamStats` card component | Done |
| `MLBRatings` comparison bars | Done |
| `apps/api/routers/mlb.py` | Done |
| `apps/api/main.py` wiring | Done |
| `apps/visualize-ev/app/page.tsx` MLB fetch | Done |
| `.github/workflows/fetch-odds.yml` MLB cron | Done |
| Live score / inning display | Done (post-v1) |
| Win-probability sparkline | Done (post-v1) |
| Shared helpers extracted to `routers/utils.py` | Done (2026-05-07) |

---

## Decisions

- **Odds source:** SportsGameOdds (SGO) — same integration as NBA. The Odds API is out of monthly quota for March; will re-evaluate in April.
- **v1 scope:** Static dashboard only. No live score display or live polling for MLB (follow-up task after regular season launch).
- **Game filter:** Spring training (`gameType == "S"`) until regular season starts (late March 2026), then switch to `gameType == "R"`. This is a one-line change.
- **EV Calculator:** Skip for v1 — it requires adding several odds fields to `MLBMatchupData`. Can enable in a follow-up once regular season data is flowing.

---

## Data Sources

| Data | Source | Auth |
|---|---|---|
| Schedule / scores / probable pitchers | MLB Stats API (free) | None |
| Standings | MLB Stats API | None |
| Team batting + pitching stats | MLB Stats API | None |
| Spread (run line) / total / moneyline | SportsGameOdds (`leagueID: "MLB"`) | `SPORTS_GAME_ODDS_KEY` |
| Odds persistence | Supabase `nba_odds` table (reuse as-is) | `SUPABASE_*` |

MLB Stats API base: `https://statsapi.mlb.com/api/v1/`

---

## Type Changes (`sports-types.ts`)

`MLBMatchupData` is currently sparse compared to `NBAMatchupData`. For v1 static dashboard, add the minimum fields needed to populate the spread/total footer and win probability bar:

```typescript
export interface MLBMatchupData extends BaseMatchupData {
  sport: "mlb"
  gameNumber?: number          // 1 or 2 for doubleheaders
  gameStatus: number           // 1=scheduled, 2=live, 3=final
  pregameSpread?: number | null     // home perspective (almost always +1.5)
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
  away: MLBTeamData
  home: MLBTeamData
}
```

**Not adding for v1** (live score follow-up): `currentInning`, `inningHalf`, `score` on `MLBTeamData`.

`MLBTeamData` needs no changes for v1 — all fields are already defined.

---

## Backend: `apps/api/routers/mlb.py`

### Cache System

Same pattern as `nba.py`:

```python
CACHE_TTL_LIVE    = 20     # not used for v1 static, kept for future
CACHE_TTL_DEFAULT = 60
CACHE_TTL_ODDS    = 1800   # 30 min — SGO rate limit

caches = {
    "mlb_schedule":  {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
    "mlb_standings": {"data": None, "last_fetch": 0, "ttl": 300},
    "mlb_stats":     {"data": None, "last_fetch": 0, "ttl": 600},
    "mlb_odds":      {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_ODDS},
    "mlb_assembled": {"data": None, "last_fetch": 0, "ttl": CACHE_TTL_DEFAULT},
}
```

Use separate cache keys from NBA so they don't collide.

---

### `get_schedule(date_str: str) -> list`

```
GET https://statsapi.mlb.com/api/v1/schedule
    ?sportId=1
    &date={YYYY-MM-DD}
    &hydrate=probablePitcher,linescore,team
```

Filter: `game["gameType"] == "S"` for spring training (**flip to `"R"` for regular season**).

Key fields per game:

| Field | Path |
|---|---|
| Game PK | `game["gamePk"]` (int → str) |
| UTC start | `game["gameDate"]` |
| Local date | `game["officialDate"]` |
| Abstract status | `game["status"]["abstractGameState"]` → `"Preview"` / `"Live"` / `"Final"` |
| Home team ID | `game["teams"]["home"]["team"]["id"]` |
| Away team ID | `game["teams"]["away"]["team"]["id"]` |
| Home abbreviation | `game["teams"]["home"]["team"]["abbreviation"]` |
| Away abbreviation | `game["teams"]["away"]["team"]["abbreviation"]` |
| Home probable pitcher | `game["teams"]["home"].get("probablePitcher", {}).get("fullName")` |
| Away probable pitcher | `game["teams"]["away"].get("probablePitcher", {}).get("fullName")` |
| Venue | `game["venue"]["name"]` |
| Double-header | `game["doubleHeader"]` → `"Y"` if twin bill |
| Game number | `game["gameNumber"]` → 1 or 2 |

**Status mapping:**
```python
SATUS_MAP = {"Preview": 1, "Live": 2, "Final": 3}
game_status = SATUS_MAP.get(abstract_state, 1)
```

**Status text for display:**
- Scheduled → ET time string (convert `gameDate` UTC → ET, format as `"7:05 PM ET"`)
- Live → `"Top 7"` / `"Bot 4"` / `"Mid 6"` / `"End 8"` (from linescore — v1 can simplify to `"Live"`)
- Final → `"Final"` (check `linescore.currentInning > linescore.scheduledInnings` for extras → `"F/11"`)

---

### `get_standings_map() -> dict`

```
GET https://statsapi.mlb.com/api/v1/standings
    ?leagueId=103,104
    &season={current_year}
    &standingsType=regularSeason
```

- `leagueId=103` = AL, `104` = NL
- Returns division objects, each with `teamRecords`

Build `standings_map: dict[int, dict]` keyed by `team["id"]`:

```python
{
    team_id: {
        "wins": int,
        "losses": int,
        "winPct": float,           # parse string ".580"
        "divisionRank": int,       # parse string "1"
        "streak": str,             # e.g. "W4", "L2"
        "division": str,           # e.g. "NL East"
        "conference": str,         # "AL" or "NL" (abbreviate from league.name)
    }
}
```

Note: Spring training standings are essentially 0-0 for everyone — that's fine for testing.

---

### `get_stats_map() -> dict`

Two calls, same season param:

```
GET https://statsapi.mlb.com/api/v1/teams/stats
    ?season={current_year}
    &sportId=1
    &stats=season
    &group=hitting
```

```
GET https://statsapi.mlb.com/api/v1/teams/stats
    ?season={current_year}
    &sportId=1
    &stats=season
    &group=pitching
```

**Season timing edge case:** Current year stats will be empty or sparse early in spring training. Fall back to `season={current_year - 1}` if the current year returns fewer than 5 team records. Flag in logs.

Build `stats_map: dict[int, dict]` keyed by `team["id"]`:

```python
{
    team_id: {
        "battingAvg":  float,   # hitting: stat["avg"]
        "ops":         float,   # hitting: stat["ops"]
        "runsScored":  float,   # hitting: stat["runs"] / stat["gamesPlayed"]  (RS/G)
        "era":         float,   # pitching: stat["era"]
        "runsAllowed": float,   # pitching: stat["runs"] / stat["gamesPlayed"] (RA/G)
    }
}
```

---

### SGO Odds Integration

Same structure as `nba.py`'s `get_odds_map()`.

**Request (confirmed against live SGO MLB response):**
```python
params = {
    "leagueID": "MLB",
    "oddsAvailable": "true",
    "oddID": ",".join([
        # Identical oddIDs to NBA — "points-" prefix, not "runs-"
        "points-home-game-ml-home",
        "points-away-game-ml-away",
        "points-home-game-sp-home",   # run line (always ±1.5)
        "points-away-game-sp-away",
        "points-all-game-ou-over",
        "points-all-game-ou-under",
    ]),
    "limit": 50,
}
```

**Team ID format (confirmed):** `TEAM_NAME_MLB` with full names — e.g. `MINNESOTA_TWINS_MLB`, `ATLANTA_BRAVES_MLB`, `OAKLAND_ATHLETICS_MLB`, `LOS_ANGELES_DODGERS_MLB`, `LOS_ANGELES_ANGELS_MLB`. No short variants (`LA_`) observed — all 30 teams use full names.

**Team tricode map:** Build `SGO_MLB_TEAM_MAP` using the same pattern as `get_tricode_from_sgo_team_id()` in `nba.py`. All teams confirmed to use full name format with `_MLB` suffix.

**Athletics tricode:** `OAKLAND_ATHLETICS_MLB` → `OAK` (confirmed from live response — no relocation tricode change in SGO as of March 2026).

**Persistence:** Reuse the Supabase `nba_odds` table. MLB matchup keys (`"NYM@ATL"`) won't collide with NBA keys. The `load_persisted_odds` / `save_persisted_odds` pattern copies directly — or import from `nba.py` and call with the same interface.

**Cron endpoint:** `POST /api/mlb/cron/fetch-odds` — identical to NBA cron, calls `get_odds_map()` and invalidates the `mlb_odds` cache. Requires `Authorization: Bearer $CRON_SECRET`; `offset` is bounded to `0..1` and `collection_key` to 128 chars.

---

### Response Assembly: `GET /api/mlb/today`

For each game in today's schedule:

1. Look up standings by team ID → record, winPct, divisionRank, division, conference, streak
2. Look up stats by team ID → ERA, avg, OPS, RS/G, RA/G
3. Look up odds by matchup key `"{AWAY}@{HOME}"` → run line, total, moneylines
4. Calculate win probability from moneylines (import `calculate_win_probability_from_odds` from `nba.py` or duplicate — same math applies)
5. Calculate projected scores:
   ```python
   # Naive RS model: blend team offensive output vs opponent pitching
   naive_away = (away_rs_pg + home_ra_pg) / 2
   naive_home = (home_rs_pg + away_ra_pg) / 2
   naive_total = naive_away + naive_home

   # Blend with market if O/U available
   if over_under:
       projected_total = round(0.7 * over_under + 0.3 * naive_total, 1)
       ratio = naive_away / naive_total if naive_total > 0 else 0.5
       proj_away = round(projected_total * ratio)
       proj_home = round(projected_total * (1 - ratio))
   else:
       projected_total = round(naive_total, 1)
       proj_away = round(naive_away)
       proj_home = round(naive_home)
   ```
6. Spread result / total result: same logic as NBA — only calculate if `gameStatus == 3` (Final). Requires scores, which the schedule endpoint provides.

**Response shape per game:**
```python
{
    "sport": "mlb",
    "gameId": str(game_pk),
    "gameStatus": game_status,           # 1/2/3
    "status": status_text,              # "7:05 PM ET" / "Live" / "Final"
    "date": official_date,              # "2026-03-22"
    "time": local_time_et,              # "19:05"
    "venue": venue_name,
    "broadcast": "Local",
    "model": "PECOTA v2.8",
    "gameNumber": game_number_or_none,
    "spreadLine": spread_text,          # "NYM +1.5" or "N/A"
    "spreadResult": spread_result,      # True/False/None
    "pregameSpread": home_run_line,     # home perspective (+1.5 typical)
    "spreadOddsHome": ...,
    "spreadOddsAway": ...,
    "overUnder": over_under,
    "overOdds": ...,
    "underOdds": ...,
    "homeMoneyline": ...,
    "awayMoneyline": ...,
    "totalResult": total_result,        # "over"/"under"/"push"/None
    "projectedTotal": projected_total,
    "away": {
        "name": ..., "abbreviation": ...,
        "record": f"{wins}-{losses}",
        "winPct": float,
        "seed": division_rank,          # using div rank as seed equivalent
        "division": division_name,
        "conference": "AL" or "NL",
        "streak": streak_code,          # "W4", "L2"
        "predictedScore": proj_away,
        "winProbability": away_prob,
        "runsScored": rs_pg,
        "runsAllowed": ra_pg,
        "era": era,
        "battingAvg": avg,
        "ops": ops,
        "probablePitcher": pitcher_name_or_none,
        "pitcherRecord": None,          # not in stats API
    },
    "home": { ... }
}
```

---

## Frontend: `apps/visualize-ev/app/page.tsx`

Add MLB alongside NBA in the fetch logic:

```typescript
if (activeSport === "nba" || activeSport === "mlb") {
    const endpoint = activeSport === "nba" ? "nba/today" : "mlb/today"
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/${endpoint}`)
    ...
}
```

**Polling:** No live polling for MLB v1 (games update slowly). Set up polling interval only for `activeSport === "nba"` — keep current behavior. MLB loads once on tab switch.

Remove the "coming soon" fallback for MLB once the endpoint is live.

---

## Backend Wiring: `apps/api/main.py`

```python
from routers import ev, dodgers, nba, mlb

app.include_router(mlb.router)
```

---

## Cron: `.github/workflows/fetch-odds.yml`

Add a second step to the existing workflow that calls `/api/mlb/cron/fetch-odds` on the same schedule (11am ET + 5pm ET). MLB games typically start afternoon and evening, so the same timing works. MLB season runs April–October so this only matters in-season.

---

## MLB Team ID → Tricode Map

```python
MLB_TEAM_ID_TO_TRI = {
    108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS", 112: "CHC",
    113: "CIN", 114: "CLE", 115: "COL", 116: "DET", 117: "HOU",
    118: "KC",  119: "LAD", 120: "WSH", 121: "NYM", 133: "ATH",  # Athletics (verify tricode post-relocation)
    134: "PIT", 135: "SD",  136: "SEA", 137: "SF",  138: "SL",
    139: "TB",  140: "TEX", 141: "TOR", 142: "MIN", 143: "PHI",
    144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY", 158: "MIL",
}
```

Note: The schedule endpoint also returns `team["abbreviation"]` directly, so this map is only needed to join stats/standings data. Verify `ATH` (Athletics) — the team relocated to Sacramento for 2025 and their tricode may have changed from `OAK`.

---

## Spring Training → Regular Season Transition

One-line change in `get_schedule()`:

```python
# Spring training (current):
if game["gameType"] != "S":
    continue

# Regular season (flip when ready, ~late March 2026):
if game["gameType"] != "R":
    continue
```

Regular season starts late March 2026. Spring training can be used for end-to-end testing until then.

---

## v1 Explicit Non-Goals (Follow-up Tasks)

- **Live score display** — requires adding `score` to `MLBTeamData` and `currentInning`/`inningHalf` to `MLBMatchupData`, plus live polling in the frontend
- **EV Calculator for MLB** — requires adding odds fields to `MLBMatchupData` (already planned in type spec above, just need MatchupCard to render it for `sport === "mlb"`)
- **Pitcher record** — not available from MLB Stats API team stats; would require a separate roster/stats call per pitcher
- **Doubleheader handling** — `gameNumber` field is in the type; display TBD (could suffix the game ID with `/1` `/2` in the card header)
