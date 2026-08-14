# NFL Game Detail

_Created: 2026-08-13_

Layers on `game_detail_common.md` and uses ESPN's per-event summary payload.

## Tabs

1. **Win Prob** — ESPN's published win-probability series plus play context.
2. **Matchup** — PF/G, PA/G, and available game team stats.
3. **Boxscore** — team totals plus passing, rushing, and receiving tables.
4. **Plays** — drive play-by-play, flattened in sequence order.

## State behavior

- Preview: header and empty linescore render; box score and plays may be empty.
- Live: partial linescore, box score, drives, and win probability render.
- Final: complete source-backed detail renders when ESPN publishes it.
- A missing tab payload must not fail the whole endpoint.

## Source mappings

- Header/teams/status: `summary.header.competitions[0]`
- Venue: `summary.gameInfo.venue`
- Linescore: competitor `linescores[]`
- Team stats: `summary.boxscore.teams[].statistics`
- Player stats: `summary.boxscore.players[].statistics`
- Plays: flatten `summary.drives.previous[].plays` plus the current drive
- Win probability: `summary.winprobability`; convert ESPN home probability to
  away/home percentages and distribute points across game progress

## Explicit non-goals

The prototype's QB Elo adjustment, ATS trends, offense-vs-defense ranks, and
similar modeled panels are not part of v1 because no trustworthy computation
or licensed feed currently backs them.
