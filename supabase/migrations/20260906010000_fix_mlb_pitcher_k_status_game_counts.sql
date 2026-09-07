-- Unresolved settlements often have no MLB gamePk yet. Count their stable SGO
-- event IDs so the backlog monitor reports affected games instead of zero.
create or replace view public.mlb_pitcher_k_settlement_status_counts
with (security_invoker = true)
as
select
    game_date,
    settlement_status,
    count(*) as observation_count,
    count(distinct sgo_event_id) as game_count
from public.mlb_pitcher_k_settlements
where settlement_status not in ('graded', 'void')
group by game_date, settlement_status;

comment on view public.mlb_pitcher_k_settlement_status_counts is
    'Daily nonterminal settlement counts by SGO event for cron monitoring and policy backlog visibility.';
