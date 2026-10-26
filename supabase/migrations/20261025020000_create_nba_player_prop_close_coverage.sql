-- Daily close coverage for check-collectors.yml, per game, book and market.
-- A row counts as usable under the close rules on
-- nba_player_prop_odds.provider_timestamp: active, not stale, combo_check
-- null or passed, and priced in the 90 minutes before the tip. Unchecked and
-- failed combo rows are counted separately and never as usable. Only cells
-- with some row in the window come back; the API fills in the empty cells
-- from ESPN's schedule. See apps/api/specs/nba_player_props.md.
create or replace function public.nba_player_prop_close_coverage(target date)
returns table (
    matchup_key    text,
    bookmaker_id   text,
    market_type    text,
    usable_rows    integer,
    usable_players integer,
    unchecked_rows integer,
    failed_rows    integer
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        o.matchup_key,
        o.bookmaker_id,
        o.market_type,
        (count(*) filter (where o.combo_check is null or o.combo_check = 'passed'))::integer,
        (count(distinct o.selection_name) filter (where o.combo_check is null or o.combo_check = 'passed'))::integer,
        (count(*) filter (where o.combo_check = 'unchecked'))::integer,
        (count(*) filter (where o.combo_check = 'failed'))::integer
    from public.nba_player_prop_odds as o
    where o.game_date = target
      and o.is_active
      and not o.is_stale_pregame_price
      and o.provider_timestamp >= o.starts_at - interval '90 minutes'
      and o.provider_timestamp < o.starts_at
    group by o.matchup_key, o.bookmaker_id, o.market_type
$$;

-- Same outer wall as the append function: Supabase's default privileges
-- would otherwise let anon and authenticated call it directly.
revoke all on function public.nba_player_prop_close_coverage(date) from public, anon, authenticated;
grant execute on function public.nba_player_prop_close_coverage(date) to service_role;
