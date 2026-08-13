-- Latest-value cache used by the NFL dashboard. This mirrors the manually
-- provisioned nba_odds/mlb_odds/nhl_odds tables.
create table if not exists public.nfl_odds (
    game_date date not null,
    matchup_key text not null,
    odds_data jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (game_date, matchup_key)
);

alter table public.nfl_odds enable row level security;

drop policy if exists "service role manages nfl odds" on public.nfl_odds;
create policy "service role manages nfl odds"
    on public.nfl_odds
    for all
    to service_role
    using (true)
    with check (true);

-- Changed, pregame observations of the normalized main-game market bundle.
-- There is deliberately no unique constraint on snapshot_hash: a market can
-- move away and legitimately return to an earlier price later.
create table if not exists public.odds_snapshots (
    id bigint generated always as identity primary key,
    sport text not null,
    provider text not null,
    bookmaker text not null,
    provider_event_id text,
    game_date date not null,
    matchup_key text not null,
    starts_at timestamptz,
    captured_at timestamptz not null,
    odds_data jsonb not null,
    snapshot_hash text not null,
    created_at timestamptz not null default now(),
    constraint odds_snapshots_sport_check
        check (sport in ('nba', 'wnba', 'nfl', 'mlb', 'nhl'))
);

create index if not exists odds_snapshots_lookup_idx
    on public.odds_snapshots (
        sport,
        provider,
        bookmaker,
        game_date,
        matchup_key,
        captured_at desc
    );

create index if not exists odds_snapshots_event_time_idx
    on public.odds_snapshots (provider_event_id, captured_at desc);

comment on table public.odds_snapshots is
    'Append-only changed pregame snapshots of normalized moneyline, spread, and total markets.';

comment on column public.odds_snapshots.snapshot_hash is
    'SHA-256 of canonical odds_data JSON; used for consecutive-value deduplication in the collector.';

alter table public.odds_snapshots enable row level security;

drop policy if exists "service role manages odds snapshots" on public.odds_snapshots;
create policy "service role manages odds snapshots"
    on public.odds_snapshots
    for all
    to service_role
    using (true)
    with check (true);

create or replace function public.reject_odds_snapshot_mutation()
returns trigger
language plpgsql
as $$
begin
    raise exception 'odds_snapshots is append-only';
end;
$$;

drop trigger if exists odds_snapshots_append_only
    on public.odds_snapshots;

create trigger odds_snapshots_append_only
before update or delete on public.odds_snapshots
for each row execute function public.reject_odds_snapshot_mutation();

drop trigger if exists odds_snapshots_reject_truncate
    on public.odds_snapshots;

create trigger odds_snapshots_reject_truncate
before truncate on public.odds_snapshots
for each statement execute function public.reject_odds_snapshot_mutation();
