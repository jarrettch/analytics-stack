# MLB Dashboard — Implementation Spec

## Overview

The frontend card, TypeScript types, ratings comparison, and odds integration
pattern are already in place from the NBA build. The work is almost entirely
a new `apps/api/routers/mlb.py` + wiring it into `main.py` and `page.tsx`.

---

## Data Sources

| Data | Source | Auth |
|---|---|---|
| Schedule / scores / probable pitchers | MLB Stats API (free) | None |
| Standings | MLB Stats API | None |
| Team batting + pitching stats | MLB Stats API | None |
| Spread / total / moneyline | The Odds API (`baseball_mlb`) | `ODDS_API_KEY` |

MLB Stats API base: `https://statsapi.mlb.com/api/v1/`

---

## 1. Schedule Endpoint

```
GET /schedule?sportId=1&date={YYYY-MM-DD}&hydrate=probablePitcher,linescore,team
```

Key fields per game:

| Field | Path |
|---|---|
| Game ID | `game.gamePk` (int) |
| UTC start | `game.gameDate` |
| Local date | `game.officialDate` |
| Abstract status | `game.status.abstractGameState` → `"Preview"` / `"Live"` / `"Final"` |
| Home team | `game.teams.home.team.{id, name, abbreviation}` |
| Away team | `game.teams.away.team.{id, name, abbreviation}` |
| Home score | `game.teams.home.score` |
| Away score | `game.teams.away.score` |
| Home probable pitcher | `game.teams.home.probablePitcher.fullName` |
| Away probable pitcher | `game.teams.away.probablePitcher.fullName` |
| Current inning | `game.linescore.currentInning` |
| Inning state | `game.linescore.inningState` → `"Top"` / `"Middle"` / `"Bottom"` / `"End"` |

**Status mapping:**
```python
status_map = {
    "Preview": 1,   # scheduled
    "Live":    2,   # in progress
    "Final":   3,   # final
}
game_status = status_map.get(abstract_state, 1)
```

**Status text for display:**
- Scheduled → time string (e.g. `"7:05 PM ET"`)
- Live → `"Top 7"` / `"Bot 4"` / `"Mid 6"` / `"End 8"`
- Final → `"Final"` (or `"F/10"` for extras — check `linescore.scheduledInnings` vs actual)

**Double-headers:** `game.doubleHeader` — `"Y"` if part of a twin bill, `game.gameNumber` = 1 or 2.

---

## 2. Standings Endpoint

```
GET /standings?leagueId=103,104&season={YEAR}&standingsType=regularSeason
```

- `leagueId=103` = AL, `104` = NL
- Returns 6 division objects, each with ~5 `teamRecords`

Key fields per team record:

| Field | Path |
|---|---|
| Team ID | `teamRecord.team.id` |
| Wins | `teamRecord.wins` |
| Losses | `teamRecord.losses` |
| Win pct | `teamRecord.winningPercentage` (string, e.g. `".580"`) |
| Division rank | `teamRecord.divisionRank` (string) |
| Streak | `teamRecord.streak.streakCode` (e.g. `"W4"`) |
| Division | `division.name` (on the parent record object) |
| Conference | `league.name` → `"American League"` / `"National League"` |

Build a `standings_map: dict[int, dict]` keyed by `team.id`.

---

## 3. Team Stats Endpoints

Both return 30 splits (one per team), keyed by `split.team.id`.

### Hitting
```
GET /teams/stats?season={YEAR}&sportId=1&stats=season&group=hitting
```

| Dashboard field | API field | Notes |
|---|---|---|
| `battingAvg` | `stat.avg` (string) | Parse to float |
| `ops` | `stat.ops` (string) | Parse to float |
| `runsScored` | `stat.runs / stat.gamesPlayed` | RS/G |

### Pitching
```
GET /teams/stats?season={YEAR}&sportId=1&stats=season&group=pitching
```

| Dashboard field | API field | Notes |
|---|---|---|
| `era` | `stat.era` (string) | Parse to float |
| `runsAllowed` | `stat.runs / stat.gamesPlayed` | RA/G |

Build `hitting_map` and `pitching_map` keyed by `team.id`. Merge into a single
`advanced_map[team_id]` dict.

---

## 4. Odds (The Odds API)

Same pattern as NBA — sport key is `baseball_mlb`.

```
GET /v4/sports/baseball_mlb/odds/?apiKey=...&regions=us&markets=spreads,totals,h2h&oddsFormat=american
```

Preferred book: **BetOnline** (`betonlineag`), fallback to first available.

Matchup key format: `"{AWAY_TRI}@{HOME_TRI}"` — same as NBA.

MLB uses run lines (spread is almost always ±1.5) rather than point spreads,
but the data structure is identical. The `overUnder` is the run total (e.g. 8.5).

Persistence: reuse the existing `nba_odds` Supabase table with no changes — the
matchup key uniquely identifies the game. The `load_persisted_odds` / `save_persisted_odds`
functions in `nba.py` are generic and can be imported directly, or copied into `mlb.py`.

---

## 5. Response Assembly

Target shape per game: `MLBMatchupData` (already defined in `sports-types.ts`).

```python
{
    "sport": "mlb",
    "gameId": str(game_pk),
    "status": status_text,           # "7:05 PM ET", "Top 7", "Final"
    "gameStatus": game_status,       # 1/2/3
    "date": official_date,
    "time": local_time_et,
    "venue": venue_name,
    "broadcast": "Local",
    "model": "PECOTA v2.8",
    "spreadLine": spread_line,       # e.g. "NYM +1.5" or "N/A"
    "spreadResult": spread_result,   # True/False/None
    "pregameSpread": home_run_line,  # home perspective (almost always +1.5)
    "spreadOddsHome": ...,
    "spreadOddsAway": ...,
    "overUnder": over_under,
    "overOdds": ...,
    "underOdds": ...,
    "homeMoneyline": ...,
    "awayMoneyline": ...,
    "totalResult": total_result,     # "over"/"under"/"push"/None
    "projectedTotal": projected_total,
    "away": {
        "name": team_name,
        "abbreviation": tri,
        "record": f"{wins}-{losses}",
        "winPct": float(win_pct),
        "seed": int(div_rank),
        "division": division_name,
        "conference": "AL" or "NL",
        "streak": streak_code,       # "W4", "L2"
        "score": away_score,
        "predictedScore": proj_away,
        "winProbability": away_win_prob,
        "runsScored": rs_per_game,
        "runsAllowed": ra_per_game,
        "era": era,
        "battingAvg": avg,
        "ops": ops,
        "probablePitcher": pitcher_name,   # None if not announced
        "pitcherRecord": None,             # not available from stats API
    },
    "home": { ... }  # same shape
}
```

**Projected total / run scoring model (simple):**
```python
# Pythagorean-style blended projection
away_proj = (away_ops_factor + home_era_factor) / 2  # naive
home_proj = (home_ops_factor + away_era_factor) / 2
# Blend 70% market O/U, 30% naive if O/U available
```
Keep it simple for v1 — the market O/U is accurate enough that a 70/30 blend
is fine. If no O/U, fall back to naive.

**Win probability:** Use moneyline de-vig from `calculate_win_probability_from_odds`
(same function as NBA — can import or copy). MLB doesn't have a meaningful
in-game model for v1, so no live win prob section.

---

## 6. Team Tricode Lookup

The stats and standings endpoints don't return abbreviations. Two options:

**Option A (recommended):** Build a hardcoded `team_id_to_tri` dict (30 teams, stable).
```python
TEAM_ID_TO_TRI = {
    133: "OAK", 134: "PIT", 135: "SD", 136: "SEA", 137: "SF",
    138: "STL", 139: "TB", 140: "TEX", 141: "TOR", 142: "MIN",
    143: "PHI", 144: "ATL", 145: "CWS", 146: "MIA", 147: "NYY",
    158: "MIL", 108: "LAA", 109: "ARI", 110: "BAL", 111: "BOS",
    112: "CHC", 113: "CIN", 114: "CLE", 115: "COL", 116: "DET",
    117: "HOU", 118: "KC",  119: "LAD", 120: "WSH", 121: "NYM",
}
```

**Option B:** Fetch `/api/v1/teams?sportId=1` once at startup and cache.

The schedule endpoint returns `team.abbreviation` directly (with `hydrate=team`),
so tricodes are available there — stats/standings just need the lookup for matching.

---

## 7. Caching Strategy

Reuse the same TTL pattern from `nba.py`:

| Cache key | TTL | Notes |
|---|---|---|
| `mlb_schedule` | 20s (live) / 60s (pre/post) | Scoreboard always needs to be fresh |
| `mlb_standings` | 300s | Changes once a day |
| `mlb_stats` | 600s | Season stats, slow-moving |
| `mlb_odds` | 1800s | Rate-limited API |
| `mlb_assembled` | 20s (live) / 60s | Final assembled response |

---

## 8. Backend Wiring

### `apps/api/main.py`
```python
from routers import mlb
app.include_router(mlb.router)
```

### `apps/api/routers/mlb.py`
```python
router = APIRouter(prefix="/api/mlb", tags=["MLB"])

@router.get("/today")
async def get_todays_games(): ...
```

### `apps/visualize-ev/app/page.tsx`
Add MLB fetch alongside the existing NBA fetch:
```ts
if (activeSport === "mlb") {
    const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL}/api/mlb/today`)
    // same pattern as NBA
}
```

---

## 9. What's Already Done (No Changes Needed)

- `MLBMatchupData`, `MLBTeamData` types in `sports-types.ts`
- `MLBTeamStats` stats row component (record, RS/G, RA/G, ERA, OPS, SP)
- `MLBRatings` comparison bars (AVG, ERA, OPS)
- Spread/O/U footer, win probability bar, EV Calculator — all render for any sport
- `SPORT_LABELS`, `SPORT_MODELS` constants
- Odds persistence (Supabase `nba_odds` table reusable as-is)

---

## 10. Season Timing Note

MLB regular season starts late March / early April. Before Opening Day:
- Schedule endpoint returns spring training games (`game.seriesDescription === "Spring Training"`) — filter these out with `game.gameType === "R"` for regular season only
- Standings will show all teams at 0-0
- Team stats endpoint returns empty for `season=CURRENT_YEAR` — fall back to prior season stats for display until enough games have been played (e.g. < 10 games)
