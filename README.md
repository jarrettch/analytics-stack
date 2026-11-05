# Analytics Stack

A full-stack sports analytics monorepo. Its flagship app, **Visualize EV**, brings live scores, game schedules, and sportsbook odds into one dashboard so you can follow a slate and see how lines moved before a game.

**Explore Visualize EV** · [Live dashboard](https://www.visualize-ev.gg/) · [Slate prep](https://www.visualize-ev.gg/prep) · [MLB pitcher strikeout odds](https://www.visualize-ev.gg/mlb/pitcher-strikeout-odds)

## What’s in the app

- **Multi-sport dashboard** for MLB, NBA, WNBA, NFL, NHL, and college football and basketball. Game detail pages show play-by-play and win probability where supported.
- **Slate prep** groups upcoming matchups by date and sport, with start times, game status, and available spreads, totals, and moneylines.
- **MLB pitcher strikeout board** compares prices across sportsbooks and shows how archived lines settled against actual strikeout totals.

## How the data gets there

Next.js and React power the frontend. The Python/FastAPI backend combines scoreboard feeds and sportsbook data, matches events across providers, and serves current game data alongside odds snapshots stored in Supabase/PostgreSQL. Supabase `pg_cron` dispatches GitHub Actions workflows that call authenticated collection endpoints on Render.

| Area            | What to look for                                                                                                                               | Source                                                                                                                |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Live dashboard  | Polling for live game state and rendering different sports through shared, typed matchup components                                            | [Dashboard](apps/visualize-ev/app/page.tsx) · [Matchup card](packages/ui/src/MatchupCard.tsx)                         |
| Slate dates     | Handling late-night college games whose ESPN scoreboard date and Eastern Time calendar date differ                                             | [Slate prep](apps/visualize-ev/app/prep/page.tsx) · [College basketball API](apps/api/routers/ncaab.py)               |
| Odds collection | Timing NBA and NFL prop collection around tipoff or kickoff, archiving snapshots, and tracking usable closing prices by game, book, and market | [Collection workflow](.github/workflows/fetch-player-props.yml) · [Coverage report](scripts/report_props_coverage.py) |

The workspace also includes a Dodgers roster and player browser, shared UI and configuration packages, and database migrations and SQL tests. [Dodgermetrics](apps/dodgermetrics) · [Shared packages](packages) · [Supabase](supabase)

## Run locally

Use **Node.js 20.9+**, **pnpm 9**, and **Python 3.11**.

Install dependencies and start FastAPI from the repository root.

```sh
pnpm install --frozen-lockfile
cd apps/api
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn main:app --reload --port 8000
```

In a second terminal at the repository root, start Visualize EV.

```sh
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm --filter visualize-ev dev
```

[Local dashboard](http://localhost:3002) · [Interactive API docs](http://localhost:8000/docs)

The API can start without credentials. To enable sportsbook feeds and persisted odds history, copy `.env.example` to `apps/api/.env` and fill in the relevant provider and database settings. [API environment example](apps/api/.env.example)

Run the Python and shared UI tests from the repository root. The Python suite uses a loopback HTTP server for collector integration tests.

```sh
PYTHONPATH=apps/api apps/api/.venv/bin/python -m pytest apps/api/tests -q
pnpm --filter @repo/ui test
```
