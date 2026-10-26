-- NBA props archive: intraday sweeps plus the pregame close window. Mirrors
-- nfl_player_prop_odds without the scorer shape; every v1 market is
-- line-shaped. See apps/api/specs/nba_player_props.md.
create table if not exists public.nba_player_prop_odds (
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
    market_type        text        not null
        check (market_type in (
            'player_points', 'player_rebounds', 'player_assists',
            'player_made_threes', 'player_points_+_rebounds_+_assists',
            'player_points_+_rebounds', 'player_points_+_assists',
            'player_rebounds_+_assists'
        )),
    stat_category      text,
    -- Combos only: whether the line matched the same book's component lines
    -- in the same sweep. Failed rows are kept for audit, never as prices.
    combo_check        text,

    -- selection
    selection_name     text        not null,
    selection_raw      text        not null,
    provider_player_name text,
    side               text        not null
        check (side in ('over', 'under')),
    line               numeric     not null,

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

    constraint nba_player_prop_odds_combo_check check (
        case
            when market_type in (
                'player_points_+_rebounds_+_assists', 'player_points_+_rebounds',
                'player_points_+_assists', 'player_rebounds_+_assists'
            ) then combo_check is not null
                and combo_check in ('passed', 'failed', 'unchecked')
            else combo_check is null
        end
    ),

    -- Same dedup as NFL: selection_id already encodes market, line and side.
    constraint nba_player_prop_odds_collection_price_key unique (
        collection_id,
        bookmaker_id,
        selection_id,
        american_odds
    )
);

create index if not exists nba_player_prop_odds_game_market_idx
    on public.nba_player_prop_odds (game_date, matchup_key, market_type, captured_at);

create index if not exists nba_player_prop_odds_selection_idx
    on public.nba_player_prop_odds (selection_name, market_type, captured_at desc);

create index if not exists nba_player_prop_odds_close_idx
    on public.nba_player_prop_odds (game_date, matchup_key, provider_timestamp desc)
    where is_stale_pregame_price = false and is_active = true;

alter table public.nba_player_prop_odds enable row level security;

drop policy if exists "service role manages nba player prop odds"
    on public.nba_player_prop_odds;
create policy "service role manages nba player prop odds"
    on public.nba_player_prop_odds
    for all to service_role using (true) with check (true);

create or replace function public.reject_nba_player_prop_odds_mutation()
returns trigger language plpgsql as $$
begin
    raise exception 'nba_player_prop_odds is append-only';
end;
$$;

drop trigger if exists nba_player_prop_odds_append_only
    on public.nba_player_prop_odds;
create trigger nba_player_prop_odds_append_only
before update or delete on public.nba_player_prop_odds
for each row execute function public.reject_nba_player_prop_odds_mutation();

drop trigger if exists nba_player_prop_odds_reject_truncate
    on public.nba_player_prop_odds;
create trigger nba_player_prop_odds_reject_truncate
before truncate on public.nba_player_prop_odds
for each statement execute function public.reject_nba_player_prop_odds_mutation();

-- A scalar result avoids PostgREST's response row limit undercounting large
-- collections. The whole insert, including duplicate handling, is atomic.
create or replace function public.append_nba_player_prop_odds(observations jsonb)
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

    insert into public.nba_player_prop_odds (
        collection_id, captured_at, game_date, matchup_key, starts_at,
        provider, provider_event_id, provider_event_uuid, market_id, selection_id,
        bookmaker_id, market_type, stat_category, combo_check, selection_name,
        selection_raw, provider_player_name, side, line, american_odds,
        decimal_odds, implied_probability, is_main_line, is_alternate_line,
        is_active, is_stale_pregame_price, provider_timestamp
    )
    select
        collection_id, captured_at, game_date, matchup_key, starts_at,
        provider, provider_event_id, provider_event_uuid, market_id, selection_id,
        bookmaker_id, market_type, stat_category, combo_check, selection_name,
        selection_raw, provider_player_name, side, line, american_odds,
        decimal_odds, implied_probability, is_main_line, is_alternate_line,
        is_active, is_stale_pregame_price, provider_timestamp
    from jsonb_populate_recordset(null::public.nba_player_prop_odds, observations)
    on conflict on constraint nba_player_prop_odds_collection_price_key do nothing;

    get diagnostics inserted = row_count;
    return inserted;
end;
$$;

-- Supabase's default privileges grant execute on new public functions to
-- anon and authenticated directly, so revoking from public alone would leave
-- them callable. RLS would still reject their inserts; this is the outer wall.
revoke all on function public.append_nba_player_prop_odds(jsonb) from public, anon, authenticated;
grant execute on function public.append_nba_player_prop_odds(jsonb) to service_role;

comment on table public.nba_player_prop_odds is
    'Append-only NBA props observations from intraday sweeps and the pregame close window. A sweep is not a complete snapshot of both books: either book can be missing from the feed near tip. collection_id groups a workflow run and its retries, not a single instant. Resolve observations by provider_timestamp and captured_at; do not assume one price per selection per collection.';
comment on column public.nba_player_prop_odds.provider_timestamp is
    'Observed close, per book: latest provider_timestamp before starts_at where is_active, not is_stale_pregame_price, and combo_check is null or passed. captured_at is collection time, not price time.';
comment on column public.nba_player_prop_odds.combo_check is
    'Combo markets only: passed when the line was within 3 of the same book''s component lines in the same sweep, failed when it was not (likely mislabeled; never use as a price), unchecked when a component was missing or ambiguous. Null for single-stat markets.';
comment on column public.nba_player_prop_odds.implied_probability is
    'Raw bookmaker probability including vig. Some selections are one-sided; de-vig only paired over/under rows.';
