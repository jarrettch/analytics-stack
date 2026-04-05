# Cleanup & Issues to Address

_Created: 2026-04-02_

---

## Data correctness (fix first)

- [x] **1. CRITICAL — Enable Row-Level Security on Supabase tables**
  RLS enabled + service_role policy added on all four public tables (`nba_odds`, `chunks`, `sync_metadata`, `issues`). Completed 2026-04-02. **Note:** Any new table in `public` schema needs the same treatment — Supabase defaults to wide open.

- [ ] **2. `save_persisted_odds` timezone bug**
  `mlb.py:181` — `date.today()` uses system time, not ET. Should be `datetime.now(ET).date()`. Check `nba.py` for the same pattern. Can cause odds to persist under the wrong date near midnight.

- [ ] **3. Spring training filter still active**
  `mlb.py:228` accepts both `"S"` and `"R"` game types. Drop `"S"` now that regular season has started — spring training exhibitions shouldn't appear alongside real games.

## Architecture / before NHL

- [ ] **4. Duplicated code between `nba.py` and `mlb.py`**
  Cache helpers, Supabase persistence, odds math are copy-pasted. Extract into a shared `routers/utils.py` — especially important before adding NHL since you'd otherwise be copying a third time.

- [ ] **5. Preferred bookmaker set to Circa but unavailable on SGO free tier**
  Both `nba.py` and `mlb.py` `_sgo_book_val` prefer Circa, but Circa isn't available on the SGO free plan — so it always falls through to the consensus fallback. Switch to BetOnline (sharper lines, available on free tier) as the spec originally intended.

- [ ] **6. New `httpx.AsyncClient` per request**
  Both routers create a new `AsyncClient` per call. Use a module-level client or reuse within a request cycle to reduce connection overhead.

## Frontend

- [x] **7. MLB sparkline x-axis granularity**
  Uses inning number so all at-bats within an inning collapse to the same x position. The fix is to keep the 9-inning frame (fixed width) but distribute at-bats proportionally within each inning bucket using `atBatIndex`. Hydration data already has this field — just needs the x calculation updated in `WinProbSparkline`.

- [ ] **8. Polling architecture (stale closure fix)**
  `page.tsx:57` polls every 15s regardless of live games. The fix isn't simply adding a `hasLiveGames` check back — that's what caused the original stale closure bug. The right approach: track live game state in a ref that gets updated after each fetch, and have the interval callback read from the ref to decide whether to fire. This avoids stale closures while still skipping unnecessary polls.

- [ ] **9. Card re-render optimization**
  Once polling is restructured (#8), add `React.memo` on `MatchupCard` and sport-specific stat components. Currently every poll triggers a full re-render of all cards even when data hasn't changed (the `JSON.stringify` check in `page.tsx` helps at the data level, but child components still re-render on each `setMatchups` call with the same reference shape).

## Enhancements

- [ ] **Sparkline color context**
  ESPN-style: tint the fill above/below the 50% midline (green/red or team colors) so you can instantly see who's winning. Could use team primary colors via a color map or ESPN's CDN. Low priority but improves readability in the compact card layout.

## Cleanup

- [ ] **Confirm `.env` is gitignored**
  `apps/api/.env` contains API keys and Supabase credentials. Verified never committed to GitHub. Just ensure `.gitignore` continues to cover it.

- [ ] **Dead code: `NbaDashboard.tsx`**
  `packages/ui/src/NbaDashboard.tsx` (~400 lines) is the old NBA-only card, fully superseded by `MatchupCard.tsx`. Still exported from `packages/ui/src/index.ts`. Remove the file and its export.

- [ ] **Stub routers: `ev.py` and `dodgers.py`**
  Both return hardcoded placeholder data. Remove or gate behind a feature flag to reduce API surface noise.

- [ ] **Stale `Untitled` file in `apps/api/`**
  Early draft of `main.py`, not imported anywhere. Delete it.

- [ ] **MLB dashboard spec status table is stale**
  `mlb_dashboard.md` status table still shows most items as "Not started" — all are implemented. Update it.
