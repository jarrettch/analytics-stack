-- Add NCAAB without removing any existing odds-history sport.
alter table public.odds_snapshots
    drop constraint if exists odds_snapshots_sport_check;

alter table public.odds_snapshots
    add constraint odds_snapshots_sport_check
    check (sport in ('nba', 'wnba', 'nfl', 'mlb', 'nhl', 'ncaaf', 'ufc', 'ncaab'));

-- Mirror ncaaf_odds: one latest bundle per ET game date and matchup.
create table if not exists public.ncaab_odds (
    game_date date not null,
    matchup_key text not null,
    odds_data jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (game_date, matchup_key)
);

alter table public.ncaab_odds enable row level security;

drop policy if exists "service role manages ncaab odds" on public.ncaab_odds;
create policy "service role manages ncaab odds"
    on public.ncaab_odds
    for all
    to service_role
    using (true)
    with check (true);

