# Slate Prep — Spec

_Revised: 2026-09-26 — NCAAF added; bulk select/deselect controls_

A `/prep` page in `visualize-ev` whose primary object is an **editable slate**:

```text
Fetch today's games
→ select, remove, and combine games
→ copy formatted slate
→ optionally wrap that same slate in v2 or v3
```

The page replaces hand-assembled daily matchup lists. It is a slate composer,
not a prompt generator: prompt wrapping is secondary and must never cause a
refetch or regenerate the selected slate. LLM execution remains explicitly
deferred.

---

## Goal

Let the researcher compose one paste-ready, multi-sport slate from today's games
in Eastern Time plus the current NFL week, trim it to the work at hand, then
copy that exact edited selection. The default action is `Copy formatted slate`,
because triage, combining sports, and deciding batches happen before prompt
selection.

## Scope: core v1

- MLB, WNBA, NCAAF, NBA, and NHL use today's Eastern Time slate. NFL uses the
  current ESPN season/week slate. There is no date picker or arbitrary date
  input.
- MLB, WNBA, NFL, NCAAF, NBA, and NHL only. Reuse `/api/{sport}/today` for
  daily sports and `/api/nfl/week` for NFL. Do not add new data sources or a separate
  export fetch path.
- The user can add any combination of those sports into one slate.
- Render games grouped under clear sport headings. This is required for NFL so
  preseason games remain unmistakably labeled.
- Every game can be unchecked and rechecked before copying, individually or in
  bulk: each sport group has a Select all / Deselect all toggle, and a page-wide
  Deselect all clears every group. Deselecting never removes a sport; its games
  stay listed as excluded so any of them can be rechecked.
- Final games are excluded; scheduled and in-progress games remain available.
- One in-memory edited selection is the source for every export. Switching
  export actions does not reload sport data, reformat a fresh raw slate, or
  restore removed games.

### Explicitly deferred

- Tennis and UFC. UFC has its own page (`/ufc`); its fight rows have no
  away/home or spread, so it needs a separate prep format (see `ufc_page.md`).
- NCAAF conference and ranked filters on this page. They exist on the dashboard
  only.
- Arbitrary dates and date selection.
- Automatic batching or a “split into N” control.
- Running v2/v3 prompts or any other model execution in the stack.
- Line movement/history, consolidation, grading, and picks tracking.

## Data and formatting

The composer fetches each selected sport's existing dashboard payload on demand,
then normalizes the result into a client-side game model that retains the
display-relevant fields (sport, game ID, teams, scheduled time/status, probable
pitchers where available, and current line snapshot). NFL uses `/week`; every
other sport uses `/today`.

Use the existing data assembly as-is; `/prep` is not a second odds pipeline.
The formatted slate contains selected matchups plus the current line snapshot:

- **MLB:** `Away (Full Pitcher Name) vs Home (Full Pitcher Name) — RL <away>/<home> (<away price>/<home price>), ML <away>/<home>, O/U <x.5> (o<over>/u<under>)`.
  Use `(TBD)` when a probable pitcher is unavailable.
- **NBA, WNBA, NFL, NHL:** `Away @ Home — Spread <away>/<home> (<away price>/<home price>), ML <away>/<home>, O/U <x.5> (o<over>/u<under>)`.
- Spread/runline and moneyline pairs are always away/home order. All odds are
  American odds with explicit signs. Total prices are labelled `o`/`u` because
  their order is otherwise not implied by the line.
- A market is emitted only when its line is available. Paired prices are omitted
  together when either side is unavailable.
- Omit unavailable line fields rather than inventing a value. The snapshot is
  current context, not movement data and not a replacement for later market
  research.

Export order follows the slate as displayed: sport groups in the user's chosen
order, then each route's game order within its group. Each non-empty group begins
with an unambiguous heading such as `MLB` or `NFL (Preseason)` when the supplied
game metadata identifies preseason.

## Surface and state

`/prep` is a dedicated page in `visualize-ev` with:

- a multi-select sport control for MLB, WNBA, NFL, NCAAF, NBA, and NHL;
- loading, empty, and per-sport error states that do not discard already loaded
  selections from other sports;
- grouped game rows with selection checkboxes, including excluded games so they
  can be restored individually;
- a per-group Select all / Deselect all toggle, shown once that group has
  loaded games, and a page-wide Deselect all beside the selected count;
- a monospace preview of the exact formatted slate to be copied; and
- a primary `Copy formatted slate` action.

Adding a sport fetches its configured route once for the current composition.
Unchecking a game edits only local composer state. Removing and re-adding a
sport may fetch its current route payload again; it must not disturb selections
in the other sport groups.

The page owns selection and formatting state. A shared formatter may be extracted
for future automation, but v1 does not require `slate-export` endpoints and must
not use a server endpoint that independently rebuilds the export from raw data.

## Optional prompt wrappers and repo boundary

The sibling `sports` repo remains canonical for `sports_betting_prompt_v2.md`,
`sports_betting_prompt_v3.md`, consolidation, grading, and `picks.jsonl`.
v2 is retained whenever prompt export is retained: it is an active workflow
variant, not a deprecated version.

If product ownership explicitly accepts making the v2/v3 prompt bodies available
to the web app, add two secondary actions:

- `Copy with v2 prompt`
- `Copy with v3 prompt`

Each action wraps the already-rendered, edited slate in its selected template and
copies the result. Neither can fetch games, regenerate the raw slate, or change
the current selection.

This is an ownership decision, not an implementation detail: embedding template
bodies in `/prep` duplicates prompt content from `sports` and exposes it through
the web application. Until that decision is made, ship the multi-sport composer
with `Copy formatted slate` only. A later alternative is to provide prompt
templates through private/admin or local configuration rather than bundling them
into the public app.

## Build order

1. Build the multi-sport composer over the daily routes and NFL weekly route,
   with sport groups, removable games, preview, and `Copy formatted slate`.
2. Verify that all exports derive from the same local edited selection; retain
   the slate through partial fetch failures and sport additions/removals.
3. If prompt exposure is approved, add v2 and v3 wrapper actions over that same
   rendered slate. Otherwise leave them out of the initial release.
4. _(Later)_ Prompt-template configuration, date selection, batching, tennis/UFC,
   and line-movement work each need their own scoped decision.

## Acceptance criteria

- A user can compose and copy a single slate containing games from two or more
  supported sports without any duplicate or unlabeled groups.
- An NFL preseason game appears under an explicitly NFL/preseason-labeled group.
- Unchecking a game removes it from the preview and from every copy action;
  rechecking it restores it without altering other selections.
- A group's Select all / Deselect all changes only that group's games. The
  page-wide Deselect all empties the preview but keeps every added sport and its
  games listed for rechecking.
- `Copy formatted slate` is visually and behaviorally the primary/default action.
- If enabled, v2 and v3 copies differ only by their wrapper/template; their slate
  body is byte-for-byte the current formatted slate.
- No v1 path fetches tennis/UFC, accepts an arbitrary date, batches
  automatically, or executes a model. NFL week selection comes from the
  backend's current-week logic.
