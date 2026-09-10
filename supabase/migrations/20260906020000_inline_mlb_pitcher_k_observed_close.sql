-- `eligible` is referenced three times, so Postgres materialized it and turned
-- the CTE into an optimization fence: a player predicate on the view landed as
-- a filter over all ~64k eligible rows instead of reaching the base table, and
-- the materialization spilled ~21MB to temp on every request. Inlining lets the
-- predicate reach mlb_pitcher_k_odds_player_history_idx, and lets the planner
-- discard the alternate branch outright when a caller asks for main lines only.
create or replace view public.mlb_pitcher_k_observed_close
with (security_invoker = true)
as
with eligible as not materialized (
    select odds.*
    from public.mlb_pitcher_k_odds odds
    where odds.starts_at is not null
      and odds.captured_at < odds.starts_at
      and odds.market_phase in ('pregame', 'unknown')
),
main_rows as (
    select distinct on (
        event_id,
        player_id,
        bookmaker_id,
        side
    )
        eligible.*
    from eligible
    where not is_alternate
    order by
        event_id,
        player_id,
        bookmaker_id,
        side,
        captured_at desc,
        id desc
),
latest_alt_collections as (
    select distinct on (event_id, player_id, bookmaker_id)
        event_id,
        player_id,
        bookmaker_id,
        collection_id
    from eligible
    where is_alternate
    order by
        event_id,
        player_id,
        bookmaker_id,
        captured_at desc,
        id desc
),
alt_rows as (
    select eligible.*
    from eligible
    join latest_alt_collections latest
      on latest.event_id = eligible.event_id
     and latest.player_id = eligible.player_id
     and latest.bookmaker_id = eligible.bookmaker_id
     and latest.collection_id = eligible.collection_id
    where eligible.is_alternate
)
select * from main_rows
union all
select * from alt_rows;

comment on view public.mlb_pitcher_k_observed_close is
    'Latest independently observed pregame price per main side, plus the latest alternate ladder.';
