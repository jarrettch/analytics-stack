-- The table was initially created manually before these collector fields landed.
-- Keep this migration idempotent so it is safe both for that database and for a
-- fresh database that already received the expanded CREATE TABLE migration.
alter table public.mlb_pitcher_k_odds
    add column if not exists is_alternate boolean not null default false,
    add column if not exists market_phase text not null default 'unknown',
    add column if not exists event_started boolean,
    add column if not exists event_ended boolean,
    add column if not exists event_finalized boolean,
    add column if not exists event_cancelled boolean,
    add column if not exists open_line numeric,
    add column if not exists open_american_odds integer,
    add column if not exists close_line numeric,
    add column if not exists close_american_odds integer;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.mlb_pitcher_k_odds'::regclass
          and conname = 'mlb_pitcher_k_odds_market_phase_check'
    ) then
        alter table public.mlb_pitcher_k_odds
            add constraint mlb_pitcher_k_odds_market_phase_check
            check (market_phase in ('pregame', 'live', 'final', 'cancelled', 'unknown'));
    end if;
end;
$$;

alter table public.mlb_pitcher_k_odds enable row level security;

comment on column public.mlb_pitcher_k_odds.market_phase is
    'Derived from SGO event status flags; use pregame rows for CLV modeling.';
