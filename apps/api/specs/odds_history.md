# Odds Movement History

_Created: 2026-08-13_

The per-sport `*_odds` tables remain mutable latest-value caches for dashboard
reads. `odds_snapshots` is the append-only time series used for movement and
future CLV work.

## Observation contract

One row represents the normalized main-game market bundle for one matchup,
provider, and selected bookmaker at one collection time:

- sport, provider, bookmaker
- provider event ID
- game date, matchup key, scheduled start
- captured timestamp
- normalized ML/spread/total JSON
- deterministic hash of the normalized JSON

The table rejects UPDATE, DELETE, and TRUNCATE operations through triggers.

## Deduplication

Insert only when the normalized odds bundle differs from the latest saved row
for that sport/provider/bookmaker/matchup. A price that moves away and later
returns to an earlier value is a new observation because it differs from the
immediately preceding snapshot.

This is consecutive deduplication, not global hash uniqueness.

## Market phase

Main-game movement history is pregame-only. Started events are not appended to
the movement table; this prevents live prices from being mistaken for closing
lines. The latest pregame snapshot becomes the observed close unless a provider
supplies an explicit close later.

## Source caveat

SportsGameOdds prefers BetOnline per market and falls back to its consensus
field when that book is unavailable. Snapshot `bookmaker` records
`betonline`, `consensus`, or `mixed:betonline+consensus` so downstream analysis
does not silently compare unlike sources. The Odds API records the selected
book key directly.

## Retention and cadence

No retention limit initially: the data is compact and cannot be reconstructed.
Use the existing scheduled collections before increasing cadence. Any future
15-minute polling must be budgeted against provider entity/request limits and
should be restricted to games approaching start time.

Corrections, development cleanup, or a future retention job are explicit
administrative operations: the table owner must temporarily disable the user
triggers, perform the scoped mutation, and re-enable them. Application and
service-role writes remain insert-only.
