# Cleanup & Issues to Address

_Created: 2026-04-02_

---

## Data correctness (fix first)

- [x] **1. CRITICAL — Enable Row-Level Security on Supabase tables**
  RLS enabled + service_role policy added on all four public tables (`nba_odds`, `chunks`, `sync_metadata`, `issues`). Completed 2026-04-02. **Note:** Any new table in `public` schema needs the same treatment — Supabase defaults to wide open.

- [x] **2. `save_persisted_odds` timezone bug**
  `mlb.py:181` — `date.today()` uses system time, not ET. Should be `datetime.now(ET).date()`. Check `nba.py` for the same pattern. Can cause odds to persist under the wrong date near midnight. Fixed 2026-04-16 in both routers.

- [x] **3. Spring training filter still active**
  `mlb.py:228` accepts both `"S"` and `"R"` game types. Drop `"S"` now that regular season has started — spring training exhibitions shouldn't appear alongside real games. Fixed 2026-04-16.

## Architecture / before NHL

- [ ] **4. Duplicated code between `nba.py` and `mlb.py`**
  Cache helpers, Supabase persistence, odds math are copy-pasted. Extract into a shared `routers/utils.py` — especially important before adding NHL since you'd otherwise be copying a third time.

- [ ] **5. Preferred bookmaker set to Circa but unavailable on SGO free tier**
  Both `nba.py` and `mlb.py` `_sgo_book_val` prefer Circa, but Circa isn't available on the SGO free plan — so it always falls through to the consensus fallback. Switch to BetOnline (sharper lines, available on free tier) as the spec originally intended.

- [ ] **6. New `httpx.AsyncClient` per request**
  Both routers create a new `AsyncClient` per call. Use a module-level client or reuse within a request cycle to reduce connection overhead.

## Users / Auth / Bet Tracking (next major unlock)

This is the unlock that turns visualize-ev from a one-shot tool into something users come back to. Treat the whole block as one unit — shipping just auth (with no bet log) adds maintenance burden with zero user value.

- [ ] **Decide: shared vs separate Supabase project for visualize-ev and IDMR**
  Leaning separate. Different audiences (bettors vs Bible study), independent quotas/billing, can kill or sell either independently. Shared "save setup work" gain is small and one-time. Confirm before standing anything up.

- [ ] **Scope `profiles` table (minimal first)**
  Start with `id` (FK to `auth.users`), `email`, `display_name`, `created_at`. Add `odds_format_preference`, `favorite_teams`, `default_unit_size`, etc. only when a feature actually needs them. Don't speculatively design columns.

- [ ] **Scope bet tracking tables (this is where the design time goes)**
  Rough shape to flesh out:
  - `bets`: id, user_id, sport, event_id, market, selection, odds_at_placement, stake, placed_at, status (pending/won/lost/push/void), settled_at, payout
  - `bet_legs` (for parlays): bet_id, leg_index, selection, odds, result
  - Capture **CLV at time of bet**: snapshot the closing line so historical CLV is queryable without re-fetching
  - Decide: manual entry only at first, or attempt auto-import from screenshots/CSV later?

- [ ] **RLS policies on all user-owned tables**
  Every new table in `public` needs `enable row level security` + a policy restricting to `auth.uid() = user_id`. Same gotcha as item #1 — Supabase defaults to wide open.

- [ ] **Auth provider decision**
  Magic link is lowest friction. Google OAuth is the obvious second. Skip password auth unless there's a reason.

- [ ] **Anonymous-user behavior for existing tools**
  Decide before building auth: does the CLV calculator still work logged-out? (It should.) Are any existing tools gated? Lock this in now so you're not retrofitting later.

- [ ] **Bet history view (must ship with auth, not after)**
  Minimum: list of bets with filters (sport, status, date range), running P&L, CLV summary. This is the "why come back" surface — auth without it is plumbing.

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
