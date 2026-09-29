-- Pitcher-K observations can come from more than one odds provider. Keep raw
-- provider identifiers for auditability, but resolve settlement and history
-- through official MLB game and player identities.

alter table public.mlb_pitcher_k_odds
    add column if not exists provider text not null default 'sportsgameodds';

alter table public.mlb_pitcher_k_odds
    drop constraint if exists mlb_pitcher_k_odds_provider_nonempty;
alter table public.mlb_pitcher_k_odds
    add constraint mlb_pitcher_k_odds_provider_nonempty
    check (btrim(provider) <> '');

alter table public.mlb_pitcher_k_odds
    drop constraint if exists mlb_pitcher_k_odds_collection_price_key;
alter table public.mlb_pitcher_k_odds
    add constraint mlb_pitcher_k_odds_collection_price_key unique (
        collection_id,
        provider,
        event_id,
        odd_id,
        bookmaker_id,
        is_alternate,
        line,
        american_odds
    );

comment on table public.mlb_pitcher_k_odds is
    'Append-only provider-tagged MLB pitcher strikeout price observations; one row per bookmaker and side.';

alter table public.mlb_sgo_pitcher_map
    drop constraint if exists mlb_sgo_pitcher_map_sgo_event_id_fkey;
alter table public.mlb_sgo_pitcher_map
    drop constraint if exists mlb_sgo_pitcher_map_pkey;
alter table public.mlb_sgo_event_map
    drop constraint if exists mlb_sgo_event_map_pkey;

alter table public.mlb_sgo_event_map
    rename to mlb_pitcher_k_event_map;
alter table public.mlb_sgo_pitcher_map
    rename to mlb_pitcher_k_player_map;

alter table public.mlb_pitcher_k_event_map
    rename column sgo_event_id to provider_event_id;
alter table public.mlb_pitcher_k_event_map
    rename column sgo_starts_at to provider_starts_at;
alter table public.mlb_pitcher_k_player_map
    rename column sgo_event_id to provider_event_id;
alter table public.mlb_pitcher_k_player_map
    rename column sgo_player_id to provider_player_id;
alter table public.mlb_pitcher_k_player_map
    rename column sgo_player_name to provider_player_name;

alter table public.mlb_pitcher_k_event_map
    add column provider text not null default 'sportsgameodds';
alter table public.mlb_pitcher_k_player_map
    add column provider text not null default 'sportsgameodds';

alter table public.mlb_pitcher_k_event_map
    add constraint mlb_pitcher_k_event_map_pkey
    primary key (provider, provider_event_id);
alter table public.mlb_pitcher_k_player_map
    add constraint mlb_pitcher_k_player_map_pkey
    primary key (provider, provider_event_id, provider_player_id);
alter table public.mlb_pitcher_k_player_map
    add constraint mlb_pitcher_k_player_map_event_fkey
    foreign key (provider, provider_event_id)
    references public.mlb_pitcher_k_event_map(provider, provider_event_id);

alter index if exists public.mlb_sgo_event_map_game_idx
    rename to mlb_pitcher_k_event_map_game_idx;
alter index if exists public.mlb_sgo_event_map_date_idx
    rename to mlb_pitcher_k_event_map_date_idx;
alter index if exists public.mlb_sgo_pitcher_map_player_idx
    rename to mlb_pitcher_k_player_map_player_idx;

comment on table public.mlb_pitcher_k_event_map is
    'Mutable provider event to official MLB game identity map; frozen after its final boxscore is fetched.';
comment on table public.mlb_pitcher_k_player_map is
    'Event-scoped provider pitcher to official MLB person identity map; unresolved rows retry while pregame.';

drop view public.mlb_pitcher_k_settlement_status_counts;
drop view public.mlb_pitcher_k_starter_coverage;
drop view public.mlb_pitcher_k_settlements;

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
        provider,
        event_id,
        player_id,
        bookmaker_id,
        side
    )
        eligible.*
    from eligible
    where not is_alternate
    order by
        provider,
        event_id,
        player_id,
        bookmaker_id,
        side,
        captured_at desc,
        id desc
),
latest_alt_collections as (
    select distinct on (provider, event_id, player_id, bookmaker_id)
        provider,
        event_id,
        player_id,
        bookmaker_id,
        collection_id
    from eligible
    where is_alternate
    order by
        provider,
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
      on latest.provider = eligible.provider
     and latest.event_id = eligible.event_id
     and latest.player_id = eligible.player_id
     and latest.bookmaker_id = eligible.bookmaker_id
     and latest.collection_id = eligible.collection_id
    where eligible.is_alternate
)
select * from main_rows
union all
select * from alt_rows;

create or replace view public.mlb_pitcher_k_board_rows
with (security_invoker = true)
as
select
    odds.*,
    event_map.mlb_game_pk,
    player_map.mlb_player_id
from public.mlb_pitcher_k_odds odds
left join public.mlb_pitcher_k_event_map event_map
  on event_map.provider = odds.provider
 and event_map.provider_event_id = odds.event_id
left join public.mlb_pitcher_k_player_map player_map
  on player_map.provider = odds.provider
 and player_map.provider_event_id = odds.event_id
 and player_map.provider_player_id = odds.player_id;

create or replace view public.mlb_pitcher_k_settlements
with (security_invoker = true)
as
with joined as (
    select
        odds.id as odds_observation_id,
        odds.game_date,
        odds.provider,
        odds.event_id as provider_event_id,
        odds.player_id as provider_player_id,
        odds.player_name as provider_player_name,
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
        player_map.mlb_player_id,
        player_map.resolution_status as player_resolution_status,
        coalesce(
            results.pitcher_name,
            player_map.mlb_player_name,
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
            when player_map.mlb_player_id is null
              or player_map.resolution_status <> 'resolved'
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
    left join public.mlb_pitcher_k_event_map event_map
      on event_map.provider = odds.provider
     and event_map.provider_event_id = odds.event_id
    left join public.mlb_pitcher_k_player_map player_map
      on player_map.provider = odds.provider
     and player_map.provider_event_id = odds.event_id
     and player_map.provider_player_id = odds.player_id
    left join public.mlb_pitcher_k_results results
      on results.mlb_game_pk = event_map.mlb_game_pk
     and results.mlb_player_id = player_map.mlb_player_id
),
canonical_close as (
    -- A slate can switch providers after an earlier successful collection.
    -- Keep the newest close for the same official game, pitcher, book, side,
    -- and alternate line so the handoff does not count the same wager twice.
    select distinct on (
        coalesce(
            'mlb:' || mlb_game_pk::text,
            'provider:' || provider || ':' || provider_event_id
        ),
        coalesce(
            'mlb:' || mlb_player_id::text,
            'provider:' || provider || ':' || provider_player_id
        ),
        bookmaker_id,
        side,
        is_alternate,
        case when is_alternate then line else null end
    )
        joined.*
    from joined
    order by
        coalesce(
            'mlb:' || mlb_game_pk::text,
            'provider:' || provider || ':' || provider_event_id
        ),
        coalesce(
            'mlb:' || mlb_player_id::text,
            'provider:' || provider || ':' || provider_player_id
        ),
        bookmaker_id,
        side,
        is_alternate,
        case when is_alternate then line else null end,
        captured_at desc,
        odds_observation_id desc
),
graded as (
    select
        canonical_close.*,
        case
            when settlement_status <> 'graded' then null
            when strikeouts = line then 'push'
            when side = 'over' and strikeouts > line then 'win'
            when side = 'under' and strikeouts < line then 'win'
            else 'loss'
        end as grade
    from canonical_close
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

create or replace view public.mlb_pitcher_k_starter_coverage
with (security_invoker = true)
as
select
    results.game_date,
    results.mlb_game_pk,
    results.mlb_player_id,
    results.pitcher_name,
    bool_or(odds.id is not null) as has_market,
    count(distinct odds.bookmaker_id) filter (where odds.id is not null) as bookmaker_count
from public.mlb_pitcher_k_results results
left join public.mlb_pitcher_k_event_map event_map
  on event_map.mlb_game_pk = results.mlb_game_pk
 and event_map.resolution_status = 'resolved'
left join public.mlb_pitcher_k_player_map player_map
  on player_map.provider = event_map.provider
 and player_map.provider_event_id = event_map.provider_event_id
 and player_map.mlb_player_id = results.mlb_player_id
 and player_map.resolution_status = 'resolved'
left join public.mlb_pitcher_k_odds odds
  on odds.provider = player_map.provider
 and odds.event_id = player_map.provider_event_id
 and odds.player_id = player_map.provider_player_id
where results.did_start
group by
    results.game_date,
    results.mlb_game_pk,
    results.mlb_player_id,
    results.pitcher_name;

create or replace view public.mlb_pitcher_k_settlement_status_counts
with (security_invoker = true)
as
select
    game_date,
    settlement_status,
    count(*) as observation_count,
    count(distinct coalesce(
        'mlb:' || mlb_game_pk::text,
        'provider:' || provider || ':' || provider_event_id
    )) as game_count
from public.mlb_pitcher_k_settlements
where settlement_status not in ('graded', 'void')
group by game_date, settlement_status;

-- Read-only compatibility views keep old ad-hoc SGO queries working while the
-- application moves to the provider-neutral table names.
create view public.mlb_sgo_event_map
with (security_invoker = true)
as
select
    provider_event_id as sgo_event_id,
    mlb_game_pk,
    game_date,
    away_team,
    home_team,
    provider_starts_at as sgo_starts_at,
    mlb_starts_at,
    resolution_status,
    resolution_method,
    mlb_abstract_state,
    mlb_detailed_state,
    results_fetched_at,
    last_attempted_at,
    resolved_at,
    frozen_at,
    created_at,
    updated_at
from public.mlb_pitcher_k_event_map
where provider = 'sportsgameodds';

create view public.mlb_sgo_pitcher_map
with (security_invoker = true)
as
select
    provider_event_id as sgo_event_id,
    provider_player_id as sgo_player_id,
    mlb_player_id,
    provider_player_name as sgo_player_name,
    mlb_player_name,
    resolution_status,
    resolution_method,
    last_attempted_at,
    resolved_at,
    frozen_at,
    created_at,
    updated_at
from public.mlb_pitcher_k_player_map
where provider = 'sportsgameodds';

comment on view public.mlb_pitcher_k_board_rows is
    'Provider-tagged pitcher-K observations enriched with official MLB game and player identities.';
comment on view public.mlb_pitcher_k_observed_close is
    'Latest independently observed pregame price per provider, event, pitcher, book, and side.';
comment on view public.mlb_pitcher_k_settlements is
    'Provider-neutral pitcher-K grades, voids, and one-unit profit from observed closes and official MLB facts.';
comment on view public.mlb_pitcher_k_starter_coverage is
    'Actual MLB starters left joined to provider-tagged pitcher-K markets for collection coverage checks.';
comment on view public.mlb_pitcher_k_settlement_status_counts is
    'Daily nonterminal settlement counts by provider event for cron monitoring and policy backlog visibility.';
