create table if not exists public.nfl_player_prop_odds (
    id bigint generated always as identity primary key,

    -- collection
    collection_id      text        not null,
    captured_at        timestamptz not null,

    -- canonical identity
    game_date          date        not null,
    matchup_key        text        not null,
    starts_at          timestamptz,

    -- provider identity (debugging only, never a key)
    provider           text        not null default 'sharpapi',
    provider_event_id  text        not null,
    provider_event_uuid text,
    market_id          text,
    selection_id       text        not null,

    -- market
    bookmaker_id       text        not null
        check (bookmaker_id in ('draftkings', 'fanduel')),
    market_type        text        not null,
    stat_category      text,
    market_shape       text        not null
        check (market_shape in ('line', 'scorer')),

    -- selection
    selection_name     text        not null,
    selection_raw      text        not null,
    provider_player_name text,
    side               text
        check (side is null or side in ('over', 'under')),
    line               numeric,

    -- price
    american_odds      integer     not null,
    decimal_odds       numeric,
    implied_probability numeric,

    -- provider state
    is_main_line       boolean     not null,
    is_alternate_line  boolean     not null default false,
    is_active          boolean     not null,
    is_stale_pregame_price boolean not null,
    provider_timestamp timestamptz not null,

    created_at         timestamptz not null default now(),

    -- shape invariants
    constraint nfl_player_prop_odds_line_shape check (
        (market_shape = 'line'   and line is not null and side is not null)
        or
        (market_shape = 'scorer' and line is null     and side is null)
    ),

    -- dedup: provider identity, never `line` (see below)
    constraint nfl_player_prop_odds_collection_price_key unique (
        collection_id,
        bookmaker_id,
        selection_id,
        american_odds
    )
);

create index if not exists nfl_player_prop_odds_game_market_idx
    on public.nfl_player_prop_odds (game_date, matchup_key, market_type, captured_at);

create index if not exists nfl_player_prop_odds_selection_idx
    on public.nfl_player_prop_odds (selection_name, market_type, captured_at desc);

create index if not exists nfl_player_prop_odds_close_idx
    on public.nfl_player_prop_odds (game_date, matchup_key, provider_timestamp desc)
    where is_stale_pregame_price = false and is_active = true;

alter table public.nfl_player_prop_odds enable row level security;

drop policy if exists "service role manages nfl player prop odds"
    on public.nfl_player_prop_odds;
create policy "service role manages nfl player prop odds"
    on public.nfl_player_prop_odds
    for all to service_role using (true) with check (true);

create or replace function public.reject_nfl_player_prop_odds_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'nfl_player_prop_odds is append-only';
end;
$$;

drop trigger if exists nfl_player_prop_odds_append_only
    on public.nfl_player_prop_odds;
create trigger nfl_player_prop_odds_append_only
before update or delete on public.nfl_player_prop_odds
for each row execute function public.reject_nfl_player_prop_odds_mutation();

drop trigger if exists nfl_player_prop_odds_reject_truncate
    on public.nfl_player_prop_odds;
create trigger nfl_player_prop_odds_reject_truncate
before truncate on public.nfl_player_prop_odds
for each statement execute function public.reject_nfl_player_prop_odds_mutation();

-- A scalar result avoids PostgREST's response row limit undercounting large
-- collections. The whole insert, including duplicate handling, is atomic.
create or replace function public.append_nfl_player_prop_odds(observations jsonb)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
    inserted integer;
begin
    if jsonb_typeof(observations) is distinct from 'array' then
        raise exception 'observations must be an array';
    end if;
    if (select count(distinct item->>'collection_id')
        from jsonb_array_elements(observations) as item) > 1 then
        raise exception 'one collection per insert is required';
    end if;

    insert into public.nfl_player_prop_odds (
        collection_id, captured_at, game_date, matchup_key, starts_at,
        provider, provider_event_id, provider_event_uuid, market_id, selection_id,
        bookmaker_id, market_type, stat_category, market_shape, selection_name,
        selection_raw, provider_player_name, side, line, american_odds,
        decimal_odds, implied_probability, is_main_line, is_alternate_line,
        is_active, is_stale_pregame_price, provider_timestamp
    )
    select
        collection_id, captured_at, game_date, matchup_key, starts_at,
        provider, provider_event_id, provider_event_uuid, market_id, selection_id,
        bookmaker_id, market_type, stat_category, market_shape, selection_name,
        selection_raw, provider_player_name, side, line, american_odds,
        decimal_odds, implied_probability, is_main_line, is_alternate_line,
        is_active, is_stale_pregame_price, provider_timestamp
    from jsonb_populate_recordset(null::public.nfl_player_prop_odds, observations)
    on conflict on constraint nfl_player_prop_odds_collection_price_key do nothing;

    get diagnostics inserted = row_count;
    return inserted;
end;
$$;

revoke all on function public.append_nfl_player_prop_odds(jsonb) from public;
grant execute on function public.append_nfl_player_prop_odds(jsonb) to service_role;

comment on table public.nfl_player_prop_odds is
    'Append-only NFL props observations. Each successful attempt traverses all five markets and both books. Rejected selected-game or unidentified rows fail the attempt; invalid known non-selected games are skipped, so incidental league-wide coverage may be partial. collection_id groups a workflow run and its retries, not a single instant: a retry can add changed prices for the same selection. Resolve observations by provider_timestamp and captured_at; do not assume one price per selection per collection.';
comment on column public.nfl_player_prop_odds.provider_timestamp is
    'Observed close: latest provider_timestamp before starts_at where is_active and not is_stale_pregame_price. captured_at is collection time, not price time.';
comment on column public.nfl_player_prop_odds.implied_probability is
    'Raw bookmaker probability including vig. Scorer rows have no complementary No price and cannot be de-vigged.';
