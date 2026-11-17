-- PrizePicks tennis board runs: odds snapshots pulled on demand, the refreshes
-- that pull them, and the priced runs. See apps/api/specs/tennis_fs_upload.md.
-- Only the API's service role reads or writes these tables.

create table if not exists public.tennis_odds_snapshots (
    id          bigint generated always as identity primary key,
    pulled_at   timestamptz not null,
    -- The flat SharpAPI rows for all five markets. A snapshot is only used whole.
    rows        jsonb       not null,
    row_count   integer     not null,
    event_count integer     not null
);

create index if not exists tennis_odds_snapshots_pulled_at_idx
    on public.tennis_odds_snapshots (pulled_at desc);

create table if not exists public.tennis_odds_refreshes (
    id          bigint generated always as identity primary key,
    status      text        not null default 'running'
        check (status in ('running', 'done', 'failed')),
    started_at  timestamptz not null,
    finished_at timestamptz,
    snapshot_id bigint references public.tennis_odds_snapshots (id),
    error       text
);

-- One running refresh at a time. A second upload joins it instead.
create unique index if not exists tennis_odds_refreshes_one_running_idx
    on public.tennis_odds_refreshes (status)
    where status = 'running';

create table if not exists public.tennis_runs (
    id             uuid        primary key default gen_random_uuid(),
    created_at     timestamptz not null,
    status         text        not null check (status in ('pending', 'ready')),
    board_saved_at timestamptz not null,
    -- Null while pending.
    snapshot_id    bigint references public.tennis_odds_snapshots (id),
    -- The refresh this run is waiting on, if any. The only column that moves.
    refresh_id     bigint references public.tennis_odds_refreshes (id),
    -- The run this one reprices, if any. A stored run's results never change.
    revises        uuid references public.tennis_runs (id),
    board          jsonb       not null,
    results        jsonb       not null,
    model_version  text        not null,
    model_config   jsonb       not null
);

create index if not exists tennis_runs_refresh_idx
    on public.tennis_runs (refresh_id) where refresh_id is not null;
create index if not exists tennis_runs_revises_idx
    on public.tennis_runs (revises, created_at desc) where revises is not null;
create index if not exists tennis_runs_snapshot_idx
    on public.tennis_runs (snapshot_id) where snapshot_id is not null;

alter table public.tennis_odds_snapshots enable row level security;
alter table public.tennis_odds_refreshes enable row level security;
alter table public.tennis_runs enable row level security;

revoke all on public.tennis_odds_snapshots, public.tennis_odds_refreshes, public.tennis_runs
    from public, anon, authenticated;

drop policy if exists "service role manages tennis odds snapshots" on public.tennis_odds_snapshots;
create policy "service role manages tennis odds snapshots"
    on public.tennis_odds_snapshots for all to service_role using (true) with check (true);

drop policy if exists "service role manages tennis odds refreshes" on public.tennis_odds_refreshes;
create policy "service role manages tennis odds refreshes"
    on public.tennis_odds_refreshes for all to service_role using (true) with check (true);

drop policy if exists "service role manages tennis runs" on public.tennis_runs;
create policy "service role manages tennis runs"
    on public.tennis_runs for all to service_role using (true) with check (true);

comment on table public.tennis_runs is
    'One priced PrizePicks tennis board. Immutable apart from refresh_id: a reprice is a new row whose revises points here. Grade the newest revision of each board.';
comment on table public.tennis_odds_snapshots is
    'One complete SharpAPI tennis pull (ATP and WTA, five markets). Never mix rows from two snapshots in one run.';

-- Snapshots a run references stay, so any run can be repriced exactly under a
-- later model version. The rest go after a week. 09:40 UTC is before any
-- upload hours and clear of the collector schedules.
select cron.schedule(
    'tennis-odds-snapshot-cleanup',
    '40 9 * * *',
    $job$
    delete from public.tennis_odds_snapshots s
    where s.pulled_at < now() - interval '7 days'
      and not exists (select from public.tennis_runs r where r.snapshot_id = s.id)
      and not exists (select from public.tennis_odds_refreshes f where f.snapshot_id = s.id and f.status = 'running')
    $job$
);
