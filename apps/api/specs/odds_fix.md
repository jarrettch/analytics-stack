# SportsGameOdds Entity Budget Fix

_Created: 2026-04-08_

## The problem

Hit ~99% of the SGO `amateur` tier monthly entity cap (2483 / 2500) just **7 days into the cycle**. Sustainable budget is ~83 entities/day; observed burn is ~354/day, roughly **4× over**.

Per-second / per-minute / per-hour / per-day request limits are all fine. The bottleneck is specifically the **monthly entity quota**. SGO bills one entity per event in the response.

## Actual root cause (after audit)

The cache-TTL theory was wrong. Audit findings:

- **Only the cron hits SGO.** `/api/nba/today` and `/api/mlb/today` always read from `load_persisted_odds` (Supabase), never from SGO directly. So request volume is irrelevant.
- **Cron schedule is fine.** `.github/workflows/fetch-odds.yml` fires twice daily (11am + 5pm ET), hitting both NBA and MLB cron endpoints. That's 4 SGO calls/day total — well within any reasonable budget.
- **The bug is in the SGO query itself.** `nba.py:382-391` and `mlb.py:422-431` build the request with:
  - No `startsAfter` / `startsBefore` filter
  - `limit: 50`

  So every cron call asks SGO for "the next 50 upcoming events with odds available," not "today's games." That pulls multiple days of forward schedule every fetch. SGO bills the entire returned payload — each event = 1 entity.

  Math: ~50 entities/call × 4 calls/day ≈ 200/day ceiling, actual ~354/day suggests overlap with other days plus the new key being grabbed, etc.

The SGO `/v2/events` endpoint **does** support date filtering — confirmed via the docs: `startsAfter` and `startsBefore`, both ISO 8601 datetime strings. They're just not in the local `sports_odds_api_context.md` reference table.

## Fix (small and surgical)

- [x] **1. Audit complete — only the cron path hits SGO**
  Confirmed `/today` for both sports reads only from `load_persisted_odds`. The cache-bust at `nba.py:514` is in the cron handler, not the request path. No per-request burn exists.

- [x] **2. Scope the SGO query to today's ET window** _(shipped 2026-04-08)_
  Added `startsAfter` / `startsBefore` (today 00:00 ET → tomorrow 00:00 ET, converted to UTC) and dropped `limit` from 50 to 20 in both `nba.py` and `mlb.py` `get_odds_map()`.

  **Verified in Render logs:** NBA cron `fresh_from_api` dropped from 32 → 7. MLB dropped from 35 → 15. Persistence now only writes to today's date. Total per cron run: 22 entities. Two runs/day = 44/day. Monthly projection: ~1320 entities (53% of 2500 cap). ~85% reduction from the broken state.

- [x] **3. Update `sports_odds_api_context.md` reference table** _(shipped 2026-04-08)_
  Added `startsAfter` / `startsBefore` to the Common Query Parameters table plus a warning about the entity budget.

## Stretch (only if #2 isn't enough)

These were the original ideas. Most aren't needed once #2 ships, but keep them on the shelf:

- [ ] **Tiered TTL on the persisted cache** based on time-to-first-pitch (less relevant since the cron is the only fetcher; the cron schedule itself is the cadence control)
- [ ] **Skip cron run on no-game days** — dark NBA days, off-day MLB. Could add a `workflow_dispatch` check or just let SGO return zero events (still 1 request but 0 entities)
- [ ] **Single shared scheduler** for both sports — only worth it if the entity budget pressure stays high after #2

## Stopgap for the rest of this billing cycle

- Second free SGO key obtained and in `.env`. Bridges until cycle resets.
- **Before relying on The Odds API as backup**, check that quota too — free tier is 500 credits/month (a call costs markets × regions, so a request is not one credit) and `nba.py:33` comment suggests it's been getting hit in parallel.
- Confirm SGO billing cycle reset date from dashboard (calendar month vs signup anniversary).

## Validation after fix #2 ships

- [ ] Watch the `/cron/fetch-odds` response — `fresh_from_api` should match today's actual game count, not 50
- [ ] Track daily entity usage from the SGO account endpoint for 2-3 days
- [ ] Add a periodic log line that prints SGO entity usage so this never sneaks up again
- [ ] Optional kill-switch: if monthly entities cross 80% with >5 days left, stop fetching SGO and serve persisted only
