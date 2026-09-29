-- MLB reports a game cancelled without play as abstract state `Final`, but its
-- boxscore contains no pitchers. Once the event itself is resolved, every
-- observed market can be voided without resolving a pitcher identity.
create or replace view public.mlb_pitcher_k_settlements
with (security_invoker = true)
as
with joined as (
    select
        odds.id as odds_observation_id,
        odds.game_date,
        odds.event_id as sgo_event_id,
        odds.player_id as sgo_player_id,
        odds.player_name as sgo_player_name,
        odds.bookmaker_id,
        odds.side,
        odds.is_alternate,
        odds.line,
        odds.american_odds,
        odds.captured_at,
        event_map.mlb_game_pk,
        event_map.away_team,
        event_map.home_team,
        event_map.resolution_status as event_resolution_status,
        event_map.mlb_abstract_state,
        event_map.mlb_detailed_state,
        event_map.results_fetched_at,
        pitcher_map.mlb_player_id,
        pitcher_map.resolution_status as pitcher_resolution_status,
        coalesce(
            results.pitcher_name,
            pitcher_map.mlb_player_name,
            odds.player_name,
            odds.player_id
        ) as pitcher_name,
        results.strikeouts,
        results.did_start,
        results.appeared,
        results.innings_pitched,
        results.outs_recorded,
        results.batters_faced,
        results.pitches_thrown,
        results.fetched_at as result_fetched_at,
        case
            when event_map.mlb_game_pk is null
              or event_map.resolution_status <> 'resolved'
                then 'unresolved_identity'
            when event_map.mlb_detailed_state ilike 'cancelled%'
              or event_map.mlb_detailed_state ilike 'canceled%'
                then 'void'
            when pitcher_map.mlb_player_id is null
              or pitcher_map.resolution_status <> 'resolved'
                then 'unresolved_identity'
            when event_map.mlb_abstract_state is distinct from 'Final'
                then 'pending'
            when event_map.results_fetched_at is null
                then 'pending'
            when event_map.mlb_detailed_state is distinct from 'Final'
                then 'policy_pending'
            when results.mlb_player_id is null
                then 'void'
            when not results.did_start
                then 'void'
            when results.strikeouts is null
                then 'data_pending'
            else 'graded'
        end as settlement_status,
        case
            when event_map.mlb_detailed_state ilike 'cancelled%'
              or event_map.mlb_detailed_state ilike 'canceled%'
                then 'game_cancelled'
            when results.mlb_player_id is null
                 and event_map.mlb_abstract_state = 'Final'
                 and event_map.mlb_detailed_state = 'Final'
                 and event_map.results_fetched_at is not null
                then 'did_not_appear'
            when results.mlb_player_id is not null
                 and not results.did_start
                then 'did_not_start'
            else null
        end as void_reason
    from public.mlb_pitcher_k_observed_close odds
    left join public.mlb_sgo_event_map event_map
      on event_map.sgo_event_id = odds.event_id
    left join public.mlb_sgo_pitcher_map pitcher_map
      on pitcher_map.sgo_event_id = odds.event_id
     and pitcher_map.sgo_player_id = odds.player_id
    left join public.mlb_pitcher_k_results results
      on results.mlb_game_pk = event_map.mlb_game_pk
     and results.mlb_player_id = pitcher_map.mlb_player_id
),
graded as (
    select
        joined.*,
        case
            when settlement_status <> 'graded' then null
            when strikeouts = line then 'push'
            when side = 'over' and strikeouts > line then 'win'
            when side = 'under' and strikeouts < line then 'win'
            else 'loss'
        end as grade
    from joined
)
select
    graded.*,
    case
        when grade = 'push' then 0::numeric
        when grade = 'loss' then -1::numeric
        when grade = 'win' and american_odds > 0
            then american_odds::numeric / 100
        when grade = 'win' and american_odds < 0
            then 100::numeric / abs(american_odds)
        else null
    end as profit_units
from graded;

comment on view public.mlb_pitcher_k_settlements is
    'Derived pitcher-K grades, voids, and one-unit profit from observed closes and official MLB facts.';
