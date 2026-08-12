-- Immutable observations of the MLB pitcher-strikeout market. There is
-- intentionally no uniqueness constraint: repeated prices from
-- separate cron runs are separate points in the line-history time series.
create table if not exists public.mlb_pitcher_k_odds (
    id bigint generated always as identity primary key,
    collection_id uuid not null,
    captured_at timestamptz not null,
    game_date date not null,
    event_id text not null,
    starts_at timestamptz,
    home_team_id text,
    away_team_id text,
    player_id text not null,
    player_name text,
    player_team_id text,
    odd_id text not null,
    market_name text,
    side text not null check (side in ('over', 'under')),
    bookmaker_id text not null,
    is_alternate boolean not null default false,
    market_phase text not null default 'unknown'
        check (market_phase in ('pregame', 'live', 'final', 'cancelled', 'unknown')),
    event_started boolean,
    event_ended boolean,
    event_finalized boolean,
    event_cancelled boolean,
    line numeric not null,
    american_odds integer not null,
    open_line numeric,
    open_american_odds integer,
    close_line numeric,
    close_american_odds integer,
    bookmaker_updated_at timestamptz,
    created_at timestamptz not null default now(),
    constraint mlb_pitcher_k_odds_collection_price_key unique (
        collection_id,
        event_id,
        odd_id,
        bookmaker_id,
        is_alternate,
        line,
        american_odds
    )
);

create index if not exists mlb_pitcher_k_odds_player_game_idx
    on public.mlb_pitcher_k_odds (game_date, player_id, captured_at);

create index if not exists mlb_pitcher_k_odds_event_collection_idx
    on public.mlb_pitcher_k_odds (event_id, collection_id);

comment on table public.mlb_pitcher_k_odds is
    'Append-only SGO pitcher strikeout price observations; one row per bookmaker and side.';

comment on column public.mlb_pitcher_k_odds.close_line is
    'Provider close when SGO returns one; normally null in oddsAvailable cron responses. Use the latest pregame snapshot as observed close.';

comment on column public.mlb_pitcher_k_odds.close_american_odds is
    'Provider close price when SGO returns one; normally null in oddsAvailable cron responses. Use the latest pregame snapshot as observed close.';

alter table public.mlb_pitcher_k_odds enable row level security;

create or replace function public.reject_mlb_pitcher_k_odds_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception 'mlb_pitcher_k_odds is append-only';
end;
$$;

drop trigger if exists mlb_pitcher_k_odds_append_only
    on public.mlb_pitcher_k_odds;

create trigger mlb_pitcher_k_odds_append_only
before update or delete on public.mlb_pitcher_k_odds
for each row execute function public.reject_mlb_pitcher_k_odds_mutation();
