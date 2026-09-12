# Oh-K Corral — Roll Settlement History Up By Start

_Created: 2026-09-11_

The settled-history view on the pitcher strikeout board reports one row per
bookmaker per side. Six books means twelve rows per start, and both the table
and the summary treat those as independent observations. The table shows a
single game, and a 3-2 record renders as 18-12.

Nothing is wrong with the stored data or the SQL views. `mlb_pitcher_k_observed_close`
already collapses to exactly one observation per (event, pitcher, book, side) —
it is not logging one row per collection pass. This is a presentation and
aggregation problem in the API response and the page.

---

## Evidence

Jacob deGrom (`JACOB_DEGROM_1_MLB`), main lines only, as of 2026-09-11:

```
2026-08-13  rows=12  books=6  lines=[6.5]        Ks=3    6 win / 6 loss
2026-08-20  rows=12  books=6  lines=[5.5, 6.5]   Ks=10   6 win / 6 loss
2026-08-25  rows=12  books=6  lines=[6.5]        Ks=2    6 win / 6 loss
2026-08-31  rows=12  books=6  lines=[6.5]        Ks=8    6 win / 6 loss
2026-09-05  rows=12  books=6  lines=[5.5]        Ks=7    6 win / 6 loss
2026-09-10  rows=12  books=6  lines=[6.5]        Ks=—    12 pending
```

Six starts, 72 rows. Graded by start, the over is **3-2**. The UI shows
**18-12 · Hit 60.0%**.

---

## Two defects

### 1. The recent-settlements table can only ever show one game

`apps/visualize-ev/app/mlb/pitcher-strikeout-odds/page.tsx:460`

```ts
const recent = history.settlements.slice(0, 8);
```

`settlements` is book/side-grained, so eight rows is fewer than the twelve one
start produces. For any pitcher priced at six books this table displays a single
date, repeated. It reads like duplicate logging; it is actually a slice across
the wrong axis.

### 2. The summary counts each book as a separate wager

`apps/api/routers/mlb_pitcher_k_board.py` → `_side_summary`

`wins`, `losses`, `pushes` and `bets` are counted over book/side rows, so every
start is counted once per book. The **percentages survive this** — averaging six
near-identical books does not move the mean, and 3/5 is the same 60% — but the
implied sample size is inflated sixfold. For a product whose claim is "did this
pitcher beat the line," an n of 30 where the truth is 5 is the number that has
to be corrected.

---

## Required changes

### API: return starts, keep books nested

`build_settlement_history` in `apps/api/routers/mlb_pitcher_k_board.py` should
add a `starts` array alongside the existing flat `settlements` list. Group by
`mlb_game_pk`, falling back to `sgo_event_id` when the game is unmapped so
unresolved rows still group.

```jsonc
{
  "starts": [
    {
      "date": "2026-08-20",
      "gamePk": 823901,
      "eventId": "…",
      "awayTeam": "TEX",
      "homeTeam": "SEA",
      "consensusLine": 6.5,       // modal line across books; see rule below
      "lineRange": [5.5, 6.5],    // null when every book agrees
      "strikeouts": 10,
      "settlementStatus": "graded",
      "grade": { "over": "win", "under": "loss" },
      "voidReason": null,
      "books": [ /* the existing per-book/side rows for this start */ ]
    }
  ]
}
```

Keep `settlements` as-is. It is the per-book detail the expander renders, and
removing it would be a breaking change for no gain.

### Per-start grading rule

Grade the start at the **modal line** across books for that side. On a tie
between two lines, take the lower. Record the spread in `lineRange` so the UI
can mark the start as split-lined, and leave the per-book rows authoritative for
anything price-sensitive.

Worked example from the data above: 2026-08-20 posted both 5.5 and 6.5. Ten
strikeouts clears both, so the start grades `over: win` either way. A six-K
night against that same spread would split, which is exactly why `lineRange`
needs to reach the UI rather than being silently collapsed.

A start is `graded` only when its book rows are graded. If any row is `pending`,
`policy_pending`, `data_pending`, or `unresolved_identity`, the start carries
that status and no grade. Prefer the most-blocking status when rows disagree,
in this order: `unresolved_identity` → `pending` → `data_pending` →
`policy_pending`.

### Summary: separate the two questions

Replace the current per-side summary with two clearly labelled blocks.

**Record and hit rate — per start.** `wins`/`losses`/`pushes` count starts, not
book rows. This is the headline number. deGrom's over becomes `3-2`, hit 60.0%,
n=5.

**ROI — per book close.** Genuinely needs per-book prices, since that is where
the price differs, so keep computing it over book/side rows. Label it
explicitly as per-book-close so it can never be read as five wagers. Keep the
existing denominator convention (pushes count as staked) and the comment that
documents it.

Add a `bestPriceRoi` alongside it: for each start and side, grade at the single
best available American price across books. Nobody bets all six books, and best
price is the closest honest analogue to what a person would actually do.

### Frontend

`page.tsx`

- Render `starts`, not `settlements`. `slice(0, 8)` then means eight starts.
- One row per start: date, matchup, line (show the range when `lineRange` is
  set), Ks, result.
- Per-book rows move behind a per-row expander, reusing the existing row markup.
- Summary block shows the per-start record prominently with its `n`, and the
  per-book ROI underneath, labelled.
- The existing status footer ("0 void · 12 pending · across observed
  books/sides") stays book-grained and keeps its wording — it is already
  correct and correctly qualified.

---

## Tests

`apps/api/tests/test_mlb_pitcher_k_board.py`

- Six books, one start, all agreeing: one entry in `starts`, record is 1-0 not
  6-0, `lineRange` null.
- Books split across two lines with a result between them: start grades at the
  modal line, `lineRange` is `[5.5, 6.5]`.
- Modal tie between two lines: lower line wins the tie.
- A start with one pending book row: the whole start is `pending`, contributes
  to neither record nor ROI.
- `bestPriceRoi` picks the highest payout per start/side, not the mean.
- Existing `settlements` payload is unchanged, so the expander keeps working.

---

## Out of scope

- No schema or view changes. `mlb_pitcher_k_observed_close`,
  `mlb_pitcher_k_settlements` and the status-count view are all correct as
  written.
- Do not change the alternate-line summaries; they have the same book-grained
  shape but no start-level meaning worth inventing yet.
- Do not touch the settlement cron, identity resolution, or the backfill script.

---

## Context worth knowing

Settlements before 2026-09-07 came from the one-time backfill, which resolved
pitcher identity against MLB's *final* probable pitchers. A pitcher who was
scratched after his prop posted cannot be matched retroactively, so that window
contains **zero voids** — scratches land in `unresolved_identity` instead.
Dates from 2026-09-07 onward resolve identity while the market is live and will
grade scratches as proper voids. Any per-start record computed across the
August window is therefore missing its no-action cases. Worth a note wherever
the history states its date range, but not a blocker for this change.
