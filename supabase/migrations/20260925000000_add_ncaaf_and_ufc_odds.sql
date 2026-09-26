-- NCAAF (ESPN/DraftKings) and UFC (SharpAPI) join odds history together, so
-- the append-only table's sport CHECK changes once rather than twice.
-- The constraint stays: it is what keeps a typo in a new router from writing
-- orphaned rows that can never be deleted.
alter table public.odds_snapshots
    drop constraint if exists odds_snapshots_sport_check;

alter table public.odds_snapshots
    add constraint odds_snapshots_sport_check
    check (sport in ('nba', 'wnba', 'nfl', 'mlb', 'nhl', 'ncaaf', 'ufc'));

-- Both latest-value tables mirror nfl_odds exactly, including the
-- (game_date, matchup_key) primary key with no bookmaker column. UFC carries
-- two books, so ufc_odds bundles them inside one odds_data row per fight
-- (see specs/ufc_page.md -> Persistence); two rows would overwrite each other.
create table if not exists public.ncaaf_odds (
    game_date date not null,
    matchup_key text not null,
    odds_data jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (game_date, matchup_key)
);

alter table public.ncaaf_odds enable row level security;

drop policy if exists "service role manages ncaaf odds" on public.ncaaf_odds;
create policy "service role manages ncaaf odds"
    on public.ncaaf_odds
    for all
    to service_role
    using (true)
    with check (true);

create table if not exists public.ufc_odds (
    game_date date not null,
    matchup_key text not null,
    odds_data jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (game_date, matchup_key)
);

alter table public.ufc_odds enable row level security;

drop policy if exists "service role manages ufc odds" on public.ufc_odds;
create policy "service role manages ufc odds"
    on public.ufc_odds
    for all
    to service_role
    using (true)
    with check (true);
