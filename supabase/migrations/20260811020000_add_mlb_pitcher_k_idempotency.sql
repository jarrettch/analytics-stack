-- A GitHub workflow rerun reuses its collection_id. Exact observations already
-- accepted from an earlier partial attempt are ignored instead of appended twice.
alter table public.mlb_pitcher_k_odds
    alter column event_id set not null;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conrelid = 'public.mlb_pitcher_k_odds'::regclass
          and conname = 'mlb_pitcher_k_odds_collection_price_key'
    ) then
        alter table public.mlb_pitcher_k_odds
            add constraint mlb_pitcher_k_odds_collection_price_key unique (
                collection_id,
                event_id,
                odd_id,
                bookmaker_id,
                is_alternate,
                line,
                american_odds
            );
    end if;
end;
$$;

comment on column public.mlb_pitcher_k_odds.close_line is
    'Provider close when SGO returns one; normally null in oddsAvailable cron responses. Use the latest pregame snapshot as observed close.';

comment on column public.mlb_pitcher_k_odds.close_american_odds is
    'Provider close price when SGO returns one; normally null in oddsAvailable cron responses. Use the latest pregame snapshot as observed close.';
