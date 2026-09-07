-- Provider identity is kept separate from official MLB results. Mappings may
-- improve while a game is pending, while result rows remain correction-safe.
create table if not exists public.mlb_sgo_event_map (
    sgo_event_id text primary key,
    mlb_game_pk bigint,
    game_date date not null,
    away_team text,
    home_team text,
    sgo_starts_at timestamptz,
    mlb_starts_at timestamptz,
    resolution_status text not null
        check (resolution_status in ('resolved', 'ambiguous', 'unresolved')),
    resolution_method text,
    mlb_abstract_state text,
    mlb_detailed_state text,
    results_fetched_at timestamptz,
    last_attempted_at timestamptz not null,
    resolved_at timestamptz,
    frozen_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists mlb_sgo_event_map_game_idx
    on public.mlb_sgo_event_map (mlb_game_pk);

create index if not exists mlb_sgo_event_map_date_idx
    on public.mlb_sgo_event_map (game_date, resolution_status);

create table if not exists public.mlb_sgo_pitcher_map (
    sgo_event_id text not null references public.mlb_sgo_event_map(sgo_event_id),
    sgo_player_id text not null,
    mlb_player_id bigint,
    sgo_player_name text,
    mlb_player_name text,
    resolution_status text not null
        check (resolution_status in ('resolved', 'ambiguous', 'unresolved')),
    resolution_method text,
    last_attempted_at timestamptz not null,
    resolved_at timestamptz,
    frozen_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (sgo_event_id, sgo_player_id)
);

create index if not exists mlb_sgo_pitcher_map_player_idx
    on public.mlb_sgo_pitcher_map (mlb_player_id);

create table if not exists public.mlb_pitcher_k_results (
    mlb_game_pk bigint not null,
    mlb_player_id bigint not null,
    game_date date not null,
    pitcher_name text not null,
    strikeouts integer check (strikeouts >= 0),
    did_start boolean not null,
    appeared boolean not null default true,
    innings_pitched text,
    outs_recorded integer,
    batters_faced integer,
    pitches_thrown integer,
    mlb_abstract_state text not null,
    mlb_detailed_state text not null,
    fetched_at timestamptz not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (mlb_game_pk, mlb_player_id)
);

create index if not exists mlb_pitcher_k_results_player_idx
    on public.mlb_pitcher_k_results (mlb_player_id, game_date desc);

alter table public.mlb_sgo_event_map enable row level security;
alter table public.mlb_sgo_pitcher_map enable row level security;
alter table public.mlb_pitcher_k_results enable row level security;

create index if not exists mlb_pitcher_k_odds_player_history_idx
    on public.mlb_pitcher_k_odds (player_id, game_date desc, captured_at desc);

comment on table public.mlb_sgo_event_map is
    'Mutable SGO event to MLB game identity map; frozen after its final boxscore is fetched.';

comment on table public.mlb_sgo_pitcher_map is
    'Event-scoped SGO pitcher to MLB person identity map; unresolved rows retry while pregame.';

comment on table public.mlb_pitcher_k_results is
    'Correction-safe official MLB pitching facts for every pitcher who appeared.';

-- The observed close is the latest independently quoted side before first pitch.
-- Unknown SGO phase rows still qualify when their capture timestamp proves they
-- were pregame. Alternate ladders use only the latest captured collection.
create or replace view public.mlb_pitcher_k_observed_close
with (security_invoker = true)
as
with eligible as (
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
              or pitcher_map.mlb_player_id is null
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
left join public.mlb_sgo_event_map event_map
  on event_map.mlb_game_pk = results.mlb_game_pk
 and event_map.resolution_status = 'resolved'
left join public.mlb_sgo_pitcher_map pitcher_map
  on pitcher_map.sgo_event_id = event_map.sgo_event_id
 and pitcher_map.mlb_player_id = results.mlb_player_id
 and pitcher_map.resolution_status = 'resolved'
left join public.mlb_pitcher_k_odds odds
  on odds.event_id = pitcher_map.sgo_event_id
 and odds.player_id = pitcher_map.sgo_player_id
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
    count(distinct mlb_game_pk) as game_count
from public.mlb_pitcher_k_settlements
where settlement_status not in ('graded', 'void')
group by game_date, settlement_status;

comment on view public.mlb_pitcher_k_observed_close is
    'Latest independently observed pregame price per main side, plus the latest alternate ladder.';

comment on view public.mlb_pitcher_k_settlements is
    'Derived pitcher-K grades and one-unit profit from observed closes and official MLB facts.';

comment on view public.mlb_pitcher_k_starter_coverage is
    'Actual MLB starters left joined to stored pitcher-K markets for collection coverage checks.';

comment on view public.mlb_pitcher_k_settlement_status_counts is
    'Daily nonterminal settlement counts for cron monitoring and policy backlog visibility.';
