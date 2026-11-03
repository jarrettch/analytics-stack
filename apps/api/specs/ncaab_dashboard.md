# NCAAB Dashboard

_Created: 2026-11-02 (scope; not yet implemented)_
_Revised: 2026-11-02 after review: nullable projections, a slate-date rule for post-midnight tips, ranked-toggle selection rules, the pre-10am collection limit, and dashboard ranked-only by default. Second review the same day: persisted rows load for the scoreboard's own dates, and each collection runs as its own guarded step_
_Revised: 2026-11-02 to add tournament round labels and seeds (Phase 4), with the women's and bracket findings under Deferred_

Men's Division I college basketball, built the way NCAAF was
(`ncaaf_dashboard.md`). ESPN supplies the slate and DraftKings odds in one
scoreboard call, so NCAAB costs nothing against the SGO entity budget, and
everything that spec says about selecting the odds entry by `provider.id ==
"100"`, close-over-open, and pregame-only capture applies here unchanged.

The season tipped off 2026-11-02. ESPN drops a game's odds at tip, so every
day without a collector loses that slate's lines for good. That sets the
build order: collection ships first and alone (Phase 1), display after.

## What the feed showed on opening night

Measured 2026-11-02 at ~7:50pm ET against
`mens-college-basketball/scoreboard?dates=20261102&groups=50&limit=400`:

| | count |
|---|---|
| events on the D-I board | 182 |
| live or final, no `odds` block | 103 |
| pregame with a DraftKings entry | 50 |
| pregame with no odds | 29, all against a non-D-I opponent |
| unique abbreviations | 364 of 364 competitors |
| `provider` values seen | only `("100", "Draft Kings")` |

None of the 87 live games carried odds. That confirms the NCAAF behavior holds
for basketball: a line not captured before tip is gone.

Every unpriced pregame game had a non-D-I opponent (empty `conferenceId`).
The has-a-line filter removes those exhibition-style games with no extra
logic.

Spot checks: Nov 3 had 29 events with 10 priced; Nov 6 had 72 events and none
priced yet, presumably because books hadn't posted three days out.

## Data sources

| Need | Source | Notes |
|---|---|---|
| Slate / live scores | ESPN site scoreboard | `mens-college-basketball/scoreboard?dates=YYYYMMDD&groups=50&limit=400`. `groups=50` is Division I. `limit=400` because opening night alone had 182 events and NCAAF's `limit=200` would truncate the busiest days without saying so. |
| Odds | same scoreboard response | `select_draftkings_odds` and `normalize_espn_odds` in `espn_odds.py`, unchanged. |
| Record | scoreboard competitor | `records[]` where `name == "overall"`. |
| PPG / Opp PPG / streak / conference | ESPN standings | `apis/v2/sports/basketball/mens-college-basketball/standings?season=2027`. 32 conference groups. Stats include `avgpointsfor` and `avgpointsagainst` directly, so there's no dividing by games played. Key stats on `type`, as NCAAF does, because splits repeat names. |
| Rank | scoreboard competitor | `curatedRank.current`, AP poll. 99 means unranked; normalize 0 and 99 to `null`, same as `ncaaf._rank`. |
| Conference id | scoreboard competitor | `team.conferenceId`. Basketball ids are their own namespace; they don't match football's for the same conference. |
| Logos | ESPN CDN | `teamlogos/ncaa/500/{team.id}.png`, the same path NCAAF uses. |

### The season year is the end year

ESPN labels 2026-27 as season `2027`. Read the year off
`events[0].season.year`, as `ncaaf._assemble_slate` does. The fallback there
is `slate_date.year`, which in November would fetch last season's standings.
For NCAAB the fallback should be `slate_date.year + 1` when the month is
August or later. Better still, skip standings for that request rather than
guess.

## Team identity

ESPN abbreviations are unique across the slate (364 of 364), so
`matchup_key` is `AWAY@HOME` from `nfl._event_matchup_key`, with no team map.
Numeric team ids go into the `odds_data` payload, as NCAAF does, so identity
stays recoverable if an abbreviation changes.

## Game status

Do **not** reuse `nfl._status`. It labels periods `Q{n}` and treats only
period > 4 as overtime. Men's college basketball plays two 20-minute halves,
so a reused helper would show the second half as "Q2" and overtime as "Q3".

`ncaab._status` maps:

| ESPN `state` / `period` | `status` |
|---|---|
| `in`, 1 | `1H {clock}` |
| `in`, 2 | `2H {clock}` |
| `in`, 3+ | `OT {clock}` (`2OT` and up for period 4+) |
| `in`, halftime | ESPN's `shortDetail` ("Halftime") as-is |
| `post`, 2 | `Final` |
| `post`, 3+ | `F/OT` |

`gameStatus` keeps the shared contract: 1 preview, 2 live, 3 final.

Women's basketball plays quarters. That's one reason it's deferred rather than
sharing this router.

## Team context

`NBATeamData` doesn't fit. It requires offensive rating, defensive rating and
pace, and ESPN publishes none of those for college. NCAAB gets its own type:

```ts
export interface NCAABTeamData extends BaseTeamData {
  score: number
  pointsFor: number | null      // standings avgpointsfor
  pointsAgainst: number | null  // standings avgpointsagainst
  rank: number | null           // curatedRank.current, 99 -> null
  conferenceId: string          // ESPN team.conferenceId
}

export interface NCAABMatchupData extends BaseMatchupData {
  sport: "ncaab"
  gameStatus: number
  period: number
  gameClock: string | null
  away: NCAABTeamData
  home: NCAABTeamData
  seasonType: string
  statsSeason: number
  pregameSpread?: number | null
  spreadOddsHome?: number | null
  spreadOddsAway?: number | null
  overOdds?: number | null
  underOdds?: number | null
  homeMoneyline?: number | null
  awayMoneyline?: number | null
}
```

There is no `week` or `weekLabel`. ESPN's college basketball events carry no
week number (`week` was absent on every event sampled).

Early-season standings read 0-0, so PPG is `null` for each team's first game
and renders `--`. No league-average fill-ins. No KenPom, BPI or NET claims.
The model label is `MARKET (DK)`.

### Projected score

`nfl._project_score` falls back to a 44-point total when the market has none,
so NCAAB can't reuse it. NCAAB projects from the market only: with both a
total and a spread, home is total/2 − spread/2 and away is the rest. Without
both, the projection is `null`. There's no PPG fallback. A PPG blend would be
a model, and the card is labeled `MARKET (DK)`.

**Nullable contract.** The shared types don't allow a missing projection
today: `BaseTeamData.predictedScore` is `number` and
`BaseMatchupData.projectedTotal` is `number | undefined`. Phase 2 widens both
in `sports-types.ts`:

```ts
predictedScore: number | null   // BaseTeamData
projectedTotal?: number | null  // BaseMatchupData
```

Widening the base types is simpler than an `Omit<>` override for one sport.
The only consumer is `MatchupCard.tsx:1026-1041`. Other sports keep emitting
numbers, so nothing changes for them. That block renders `--` for a `null`
score and only applies the leader highlight when both scores are numbers.
Today `away.predictedScore > home.predictedScore` would compare against
`null` and quietly highlight the wrong side.

Phase 1 emits `null` from the API, so the backend tests cover it then. Phase 2
covers the rendering. Opening week is exactly when it happens: standings read
0-0 and some lines post with a spread but no total.

## Slate behavior

Same rules as NCAAF, for the same reasons:

- `GET /api/ncaab/today` anchors on `slate_date_et()`. Late West Coast tips
  (10pm ET and later, plus Hawaii events in November) stay on the board past
  midnight ET.
- Each game belongs to the slate of the ESPN scoreboard date that returned
  it, not to its tip's ET calendar date. See "Post-midnight tips" below.
- Only games with a line are returned, where a line means fresh ESPN odds or
  a persisted value. Load persisted odds, merge, then filter. Otherwise every
  game drops off the board at tip.
- Load persisted odds through a strict wrapper and return 503 when Supabase
  is unreachable or unconfigured.
- Status, live scores and cover/total grading reuse the NCAAF shape.

Scale differs from football. A Saturday in January or February can carry
120-150+ priced games, against ~40-70 for NCAAF. The payload is larger, which
is why both the dashboard and prep lean on the ranked filter.

### Post-midnight tips

ESPN files a game under the local evening it's played, not its ET date.
Verified 2026-11-02 against last season: East Texas A&M at Hawai'i (event
`401824896`) tipped `2025-11-10T06:00Z`, 1:00am ET on Nov 10. It appears on the
`dates=20251109` scoreboard and not on `20251110`.

Three dates are in play for that game, and each part of the system uses a
different one:

| Use | Date | Result |
|---|---|---|
| ESPN scoreboard bucket | Nov 9 | the game is on Nov 9's board |
| `game_date` from `_event_date_et` (persistence key, payload `date`) | Nov 10 | the honest ET tip date |
| prep's filter, `game.date === easternDateKey()` | Nov 9 slate until 6am | **drops the game** |

Collection is fine. The Nov 9 runs fetch the Nov 9 bucket, see the game
pregame, and persist it under `game_date` Nov 10. `/today` is fine too: it
fetches the slate date's bucket, and `load_persisted_odds()` reads calendar
yesterday through tomorrow, which includes Nov 10. The dashboard does no date
filtering of its own.

Prep is where it breaks. Its date filter would hide the game all evening, the
one window where it's worth prepping.

**Rule:** the payload carries `slateDate`, the scoreboard date the router
requested (`slate_date_et()` for `/today`). `date` stays the ET tip date and
`game_date` keys don't change. Prep filters on `slateDate` when the payload
has one and falls back to `date` for sports that don't.

NCAAF has the same bug, because it uses the same helpers and the same prep
filter. Hawai'i home football kicks off late too. Emitting `slateDate` from
`ncaaf.py` is one line, so Phase 3 includes it.

Tests use the real Hawai'i event shape: a scoreboard requested for Nov 9 that
holds a `06:00Z` Nov 10 tip. Backend: the game is in `/today` for slate Nov
9 with `date` Nov 10, `slateDate` Nov 9, and its persisted odds found under
Nov 10. Prep: the game stays listed when the slate key is Nov 9, at both
11pm and 1am ET.

## Persistence

- Latest values upsert to `ncaab_odds`, mirroring `ncaaf_odds`:
  `(game_date, matchup_key)` primary key, no bookmaker column.
- Changed pregame observations append to `odds_snapshots` with
  `sport = 'ncaab'`, `provider = 'espn'`, `bookmaker = 'draftkings'`.
- Opening prices ride in the same `odds_data` row as `open*` fields, never as
  separate snapshots. See NCAAF's "Close versus open".

### Load prior rows for the scoreboard's own dates

`fetch_espn_odds` (`espn_odds.py:244`) calls `load_persisted_odds()` with no
date. That reads **calendar** yesterday, today and tomorrow ET, whatever
`target_date` is. With `offset=0` this happens to cover everything. With
`offset=1` it doesn't. Tomorrow's scoreboard can hold a post-midnight Hawai'i
tip whose ET date is the day after tomorrow (see "Post-midnight tips"), and
that row is outside the window.

The miss corrupts saved data, it doesn't just skip it.
`normalize_espn_odds` falls back to the persisted value when a leg has no
`close` and no `open`. Without the prior row, that leg becomes `null`.
`save_persisted_odds` then upserts the whole `odds_data` bundle and replaces
the saved line with one missing that market. The second capture of the game
erases what the first one saved.

**Rule:** `fetch_espn_odds` fetches the scoreboard first, collects the ET
dates of its events (`_event_date`), and loads persisted rows for exactly
those dates. This follows `nfl._load_persisted_odds_for_dates` (`nfl.py:436`).
Each per-date load still raises after its retries, so a failed read fails the
collection instead of normalizing against an empty map. An empty scoreboard
loads nothing.

This is a change to shared code, so NCAAF picks it up too. NCAAF doesn't
collect `offset=1` today and isn't exposed to the bug, but nothing about the
fix is basketball-specific. The cron response's `persisted_loaded` and
`total_matchups` now count rows for the scoreboard's dates rather than a
three-day calendar window.

Regression test: a persisted row for a game dated two days after "now" holds
a full line, including a total. The `offset=1` scoreboard has that game with
the total leg's `close` and `open` both missing. The collector must load that
date, keep the persisted total in the normalized bundle, and upsert the
bundle with the total intact. A second assertion checks that the loader was
called with the events' dates, not the calendar window.

## Migration

One migration:

- adds `'ncaab'` to the `odds_snapshots_sport_check` constraint, keeping
  every existing value;
- creates `ncaab_odds` with RLS and the service-role policy, copied from
  `ncaaf_odds`.

## Endpoints

```text
GET  /api/ncaab/today
POST /api/ncaab/cron/fetch-odds?offset=0|1
```

The cron endpoint uses `require_cron_auth`, as every collector does.

## Collection: shared with NCAAF

NCAAB collects in `fetch-ncaaf-odds.yml`, as a second step after NCAAF. It
does not get its own workflow.

**Cost.** NCAAF already dispatches twice a day (10:00 and 17:00 ET). Morning
runs take 45-49s because they wake Render; evening runs take ~10s. The two NCAAB
`curl` calls after the server is warm add seconds each. Worst case, the morning
run crosses 60s and bills 2 minutes, about +30 min/month. A separate workflow
would add ~60 min/month. See the Actions budget notes in
`nba_player_props.md` → Cron hours.

**Timing.** 10:00 ET catches the full board before weekend noon tips. 17:00
ET catches lines posted during the day before the 6-9pm weeknight tips. A tip
between 10:00 and 17:00 ET gets one same-day attempt, the morning run.

A tip before 10:00 ET gets **zero** same-day attempts. Both runs collect the
calendar date, and the morning run is already too late. Overseas and
early neutral-site November events land here. So do odd entries: opening
night had Northern Illinois at Grambling listed at 3:00am ET (`08:00Z`),
final before any run could see it.

To give those games a chance, the NCAAB step collects tomorrow as well:
`offset=0`, then `offset=1`, on both runs. The server is already warm, so the
second call adds seconds and no dispatches. Its value depends on books
posting the night before. On Nov 2 at ~7:50pm ET, Nov 3 had 10 of 29 events
priced, and every unpriced one had a non-D-I opponent. On Nov 6, four days
out, none were. Coverage for pre-10am tips is therefore whatever was posted
by 17:00 the day before. This is a documented limit, not a guarantee. Don't
add a dispatch for it without a measured miss rate.

Calling `offset=1` doesn't change any slate or persistence date. It fetches
tomorrow's scoreboard bucket, and rows key on each tip's ET date as always.

**NCAAF's season ending doesn't break anything.** On a day with no FBS games
the NCAAF endpoint gets an empty scoreboard, writes nothing, and reports
`success: true` with `fresh_from_api: 0`. `fetch_espn_odds` sets
`latest_persistence_ok` from write failures, and zero writes means zero
failures. The workflow's snapshot check only fires when `candidates > 0`. The
same holds for NCAAB in the off-season. The NCAAF step can stay year-round;
dropping it in January is optional cleanup.

**Every collection is attempted, whatever failed before it.** The job has
four steps:

1. wake Render (the existing health-check loop)
2. collect NCAAF, `offset=0`
3. collect NCAAB, `offset=0`
4. collect NCAAB, `offset=1`

Steps 2-4 each run `if: ${{ !cancelled() }}`. A failure in any step, including
the wake loop, doesn't skip the ones after it. Render sometimes comes up
late, and each collect step has its own `curl --retry`. Today's NCAAB failing
must not cost tomorrow's early tips their only capture.

A failed step still fails the job, so one failed collection out of three is a
red run and an email. That's the same alerting as now, without the
early-exit skip.

The three collect steps share one script,
`scripts/collect_espn_odds.sh <sport> <offset>`, rather than three copies of
the current inline `curl` and `jq` block. It keeps the block's exact checks:
`--fail` with retries, `.success == true`, and the
`candidates > 0 && !snapshot_persistence_ok` persistence check. Inside one
step, an early `exit` is still correct. Independence comes from the step
boundaries, not from the script.

Not a matrix. Matrix legs are separate jobs, and each would bill its own
rounded-up minute and wake Render on its own.

Re-dispatching a failed morning run re-runs all three collections, which is
harmless. The latest-value upsert is idempotent, and snapshots deduplicate.

**The filename stays `fetch-ncaaf-odds.yml` for Phase 1.** Renaming it means
a pg_cron migration with the ordered rollout `schedule_combined_player_props`
needed, plus `check-collectors.yml` and `test_collector_schedule.py` edits.
That costs a day of lines for a cosmetic change. Update the workflow's `name:`
to "Fetch College Odds" and the header comment now; a rename is backlog.

## Dashboard wiring (Phase 2)

- `ncaab` joins `Sport`, `MatchupData`, `SPORT_LABELS` ("NCAAB"),
  `SPORT_MODELS` ("MARKET (DK)"), and `LIVE_SPORTS`.
- A November day can show NBA, NFL, NHL, NCAAF and NCAAB: five tabs. The
  scroll strip handles that; NCAAF's "revisit at seven" still holds.

### `MatchupCard` branches

| Location | Current gate | Change |
|---|---|---|
| `TeamLogo` (`MatchupCard.tsx:99`) | `sport === "ncaaf"` → `ncaa` | add `ncaab`, or logos 404 against `teamlogos/ncaab/` |
| `TeamRow` marker (`:114`) | `ncaaf` reads `rank` | add `ncaab` |
| `TeamStats` switch (`:429`) | no `ncaab` case | new `NCAABTeamStats`: PPG, Opp PPG, streak, conference |
| Score section (`:971`) | six-sport chain | add `ncaab` |
| Live period label (`:981`) | `Q{period}` for NBA/WNBA | use the server's `status` string for `ncaab` |

The score section is the one that fails quietly. Without it a live game shows
projected scores styled as the actual score, the same trap the NCAAF spec
called out.

Leave the detail-link gate alone; there's no NCAAB detail page.

### Filters

The conference and ranked-only toggles in `app/page.tsx` are NCAAF-only today
(`isNcaaf`, `passesNcaafFilters`, `ncaafConference`, `ncaafRankedOnly`).
Generalize them to both college sports rather than copying them:

- `isCollege(matchup)` narrows to `ncaaf | ncaab`.
- Conference and ranked state is keyed by sport. Football and basketball
  conference ids differ, so one shared value would select the wrong
  conference after a tab switch. The existing "only apply a conference the
  select can show" guard would hide the mistake rather than prevent it.
- NCAAB's ranked-only toggle defaults **on**, matching prep. A Saturday can
  put 150 cards on the board. NCAAF's default stays off.

## Slate prep (Phase 3)

`ncaab` joins `SUPPORTED_SPORTS` and `emptyLoadStates()`. The compiler flags
the second one.

**Prep defaults to ranked games** (decided 2026-11-02). On add, NCAAB lists
only games where at least one team has a `rank`, and only those get
auto-selected. A toggle reveals every priced game. Showing them unselected in
a 150-row list would bury the ranked games.

When no ranked team plays, the list shows "No ranked teams today" with the
same toggle, rather than an empty group.

**A hidden game is never selected.** `toSlateText` builds output from
`selectedGames` without checking visibility, so a selected game hidden by the
toggle would still be copied. The rules:

- Turning ranked-only **on** deselects every unranked NCAAB game.
- Turning it **off** reveals unranked games unselected. Revealing isn't
  choosing.
- "Select all" / "Deselect all" and the `allSelected` state act on visible
  games only.
- `toSlateText` emits selected **and** visible games. This repeats the first
  rule on purpose, so a future code path that hides games can't leak them into
  the copy.

The toggle is per sport, so turning it on for NCAAB doesn't touch NCAAF.

`PrepGame` gains `awayRank` / `homeRank`, and `formatGame` prefixes ranked
teams (`#5 Duke`) for `ncaab`. NCAAF could take the same prefix for free;
that's a separate one-line change, not part of this work.

`groupHeading` falls through to "NCAAB". `normalizeGame` needs no live-status
branch because the server already formats halves.

## Tournaments: round labels and seeds (Phase 4)

_Added 2026-11-02 from ESPN's March 2025 and March 2026 scoreboards._

This has to ship before conference tournaments start, around 2027-03-03. The
NCAA tournament's First Four follows about two weeks later. It needs Phase 3
merged first, because the ranked rule below changes Phase 3's prep filter.

### What ESPN gives us

Every tournament game has `competitions[0].type.abbreviation == "TRNMNT"`.
Regular-season games have `"STD"`, and early March has both on the same day.

`competitions[0].tournamentId` names the event:

| id | event | `season.type` |
|---|---|---|
| 22 | NCAA tournament (2025 and 2026) | 3 |
| 21 | NIT | 3 |
| 161 | College Basketball Crown | 3 |
| 1-41 | conference tournaments: 2 A-10, 3 ACC, 9 Big Ten, 27 SEC, and so on | 2 |

Conference tournaments count as regular season (`season.type == 2`), so
`season.type` alone can't find them. Use `type.abbreviation`.

`competitions[0].notes[0].headline` holds the round:

```text
T. Rowe Price ACC Tournament - Quarterfinal
Phillips 66 Big 12 Tournament - Semifinal
NIT - Championship
NCAA Men's Basketball Championship - West Region - 1st Round   (2026)
Men's Basketball Championship - South Region - First Four      (2025)
NCAA Men's Basketball Championship - Final Four
College Basketball Crown Championship Game                     (no separator)
```

The tournament name in front is unreliable. Sponsors come and go ("Barbasol
Horizon League Championship", "Ivy League Tournament pres. by TIAA"), and the
NCAA's own prefix changed between 2025 and 2026. **Identify the NCAA
tournament by `tournamentId == 22` only. Never key on the name.** Read the
round from the last ` - ` segment, and the region from the segment before it
when that segment ends in "Region".

Round suffixes seen across both seasons: `1st Round`, `2nd Round`,
`3rd Round`, `Quarterfinal`, `Semifinal`, `Semifinals`, `Final`,
`Championship`, `First Four`, `Sweet 16`, `Elite 8`, `Final Four`,
`National Championship`.

### Seeds: ESPN reuses the rank field

The scoreboard has no `seed` field, and neither does the summary header.
Instead, **in NCAA tournament games `curatedRank.current` is the tournament
seed, not the AP rank.** Examples:

| game | `curatedRank` |
|---|---|
| Arizona vs LIU, West 1st Round | 1, 16 |
| Lehigh vs Prairie View, South First Four | 16, 16 |
| NC State vs Texas, West First Four | 11, 11 |
| Duke vs FSU, ACC Quarterfinal | 1, 99 (AP rank, unchanged) |
| NIT and Crown finals | 99, 99 |

Shipped Phases 1-3 read that field as AP rank, so without this phase March
breaks quietly:

- the card puts LIU's seed `16` where an AP rank goes;
- prep copies it as `#16 LIU`, which reads as the AP's 16th-ranked team;
- ranked-only lets through every NCAA tournament game, because every team
  has a "rank".

Conference tournament seeds aren't on the scoreboard. Those games get round
labels and keep AP ranks, with no seeds.

### API rules

On `NCAABMatchupData`:

- `roundLabel: string | null`. `null` for every non-tournament game.
- `neutralSite: boolean`, from `competitions[0].neutralSite`.

On `NCAABTeamData`:

- `seed: number | null`. Only for `tournamentId == 22`: `seed` is
  `curatedRank.current` (0 and 99 become `null`) and `rank` is `null`, since
  ESPN gives no AP rank in those games. Everywhere else `seed` is `null` and
  `rank` works as it does today.

`roundLabel` format, uppercase:

| game | label |
|---|---|
| NCAA, First Four through Elite 8 | `{REGION} · {ROUND}`: `WEST · FIRST FOUR`, `EAST · R64`, `SOUTH · R32`, `MIDWEST · SWEET 16`, `WEST · ELITE 8` |
| NCAA, Final Four | `FINAL FOUR` |
| NCAA, National Championship | `TITLE GAME` |
| conference tournament | `{CONF} {ROUND}`: `ACC QF`, `BIG TEN SF`, `SEC FINAL` |
| NIT | `NIT {ROUND}` |
| Crown | `CROWN {ROUND}`, and `CROWN FINAL` for the headline with no separator |

Round abbreviations: `1st Round` R1, `2nd Round` R2, `3rd Round` R3,
`Quarterfinal` QF, `Semifinal`/`Semifinals` SF, `Final`/`Championship` FINAL.
The NCAA tournament uses R64 and R32 rather than R1 and R2, because that's
what bettors call those rounds.

`{CONF}` is the conference short name we already get from standings
(`ACC`, `Big Ten`, `Mountain West`). Use it only when both teams have the same
conference. Otherwise use `CONF`.

**An unrecognized round is never dropped.** If the suffix doesn't match the
table, the label is the raw suffix in uppercase. A missing headline on a
`TRNMNT` game gives `TOURNAMENT`. New sponsors and renamed rounds should
degrade to an ugly label, never a blank one.

### Ranked-only in March

Ranked-only passes a game when either team has a `rank` **or a `seed`**.
During the NCAA tournament that shows every NCAA game and hides the NIT and
Crown, unless an AP-ranked team plays in one. That rule goes in the shared
`collegeFilters` (dashboard) and `slatePrep` (prep) modules. The toggle keeps
its "Ranked" label.

NCAAF doesn't change. CFP games keep reading `curatedRank` as rank, as
`MatchupCard`'s comment explains, and NCAAF teams have no `seed` field.

### Display

- **Card:** show `roundLabel` in the same place NCAAF shows its week label.
  The team marker shows a seed as `(1)` and an AP rank as a plain `1`. A team
  never has both.
- **Prep copy:** seeds as `(1) Arizona`, AP ranks as `#5 Duke`. Neutral-site
  games use `vs` instead of `@`, since every tournament game is neutral-site
  and ESPN's home/away there is arbitrary. The round label goes before the
  matchup in brackets:
  `[EAST · R64] (1) Duke vs (16) Siena — Spread ...`.
- `matchup_key` stays `AWAY@HOME` from ESPN. `neutralSite` only changes the
  display, never the persistence key.

### Tests

Build the fixtures from real March 2026 scoreboards (`dates=20260312`,
`20260317`, `20260319`, `20260326`, `20260404`, `20260406`), plus one March
2025 First Four event for the old `Men's Basketball Championship` prefix.
Cover:

- ACC quarterfinal: `ACC QF`, AP rank kept, `seed` null;
- First Four: both teams seeded, `rank` null, `SOUTH · FIRST FOUR`;
- Round of 64, Sweet 16, Final Four, title game;
- NIT final and the Crown title game with no separator;
- an unknown suffix and a missing headline;
- a regular-season game on a March date: no label;
- ranked-only passing a 16-vs-16 First Four game and hiding an unranked NIT
  game, in both the dashboard filter and prep;
- the prep copy line with brackets, parentheses and `vs`.

## Phases

1. **Collection.** Migration, `routers/ncaab.py` with `/cron/fetch-odds`
   (and `/today`, which is nearly free once the router exists), registration
   in `main.py`, the date-scoped persisted load in `fetch_espn_odds`, the
   workflow's guarded collect steps and shared script, tests.
   Tests use a trimmed fixture from a real scoreboard capture, covering a
   priced game, a live game with no odds, an unpriced non-D-I game, a ranked
   team, an overtime final, a spread-only game with no standings (both
   projections `null`), and a post-midnight-ET tip on the previous day's
   scoreboard. Plus the persisted-window regression test above. Ship this alone, then confirm the first scheduled run writes
   rows.
2. **Dashboard.** Types, nullable projections in `MatchupCard`, the other
   `MatchupCard` branches, generalized college filters with NCAAB defaulting
   to ranked-only. Component test: a `null`-projection card renders `--` with
   no leader highlight.
3. **Slate prep.** Ranked default with the visibility rules, rank prefix,
   `slateDate` filtering for NCAAB and NCAAF.
4. **Tournaments.** `roundLabel`, `seed`, `neutralSite`; ranked-or-seeded in
   both filters; card and prep display. One PR touching API and UI, after
   Phase 3 merges and before 2027-03-03.

## Deferred

- Women's college basketball. ESPN carries no lines for it: 0 of 82 pregame
  games priced on Nov 2-4, 2026. SharpAPI lists `ncaaw` as a league, but its
  free tier returned no DraftKings or FanDuel rows on 2026-11-02
  (`reason: no_match`), while `ncaab` returned 44 games. Re-check closer to
  conference play. It also plays quarters and would need its own status
  mapping.
- An interactive tournament bracket (D3). Separate project; needs its own
  check of whether ESPN exposes bracket data.
- Conference tournament seeds. They aren't on the scoreboard.
- Game detail page and box scores.
- A second sportsbook via SharpAPI and cross-book de-vigging.
- KenPom/BPI/NET-style ratings.
- Renaming `fetch-ncaaf-odds.yml` to a sport-neutral name.
