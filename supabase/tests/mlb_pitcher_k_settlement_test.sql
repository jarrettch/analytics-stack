begin;

create extension if not exists pgtap with schema extensions;
set local search_path = public, extensions;

select plan(6);

insert into public.mlb_sgo_event_map (
    sgo_event_id,
    mlb_game_pk,
    game_date,
    resolution_status,
    mlb_abstract_state,
    mlb_detailed_state,
    results_fetched_at,
    last_attempted_at
) values
    ('TEST-GRADED', 990001, '2026-08-17', 'resolved', 'Final', 'Final',
     '2026-08-18T05:00:00Z', '2026-08-18T05:00:00Z'),
    ('TEST-SCRATCH', 990002, '2026-08-17', 'resolved', 'Final', 'Final',
     '2026-08-18T05:00:00Z', '2026-08-18T05:00:00Z'),
    ('TEST-UNFETCHED', 990003, '2026-08-17', 'resolved', 'Final', 'Final',
     null, '2026-08-18T05:00:00Z'),
    ('TEST-DATA', 990004, '2026-08-17', 'resolved', 'Final', 'Final',
     '2026-08-18T05:00:00Z', '2026-08-18T05:00:00Z');

insert into public.mlb_sgo_pitcher_map (
    sgo_event_id,
    sgo_player_id,
    mlb_player_id,
    resolution_status,
    last_attempted_at
) values
    ('TEST-GRADED', 'TEST-P1', 990101, 'resolved', '2026-08-17T18:00:00Z'),
    ('TEST-SCRATCH', 'TEST-P2', 990102, 'resolved', '2026-08-17T18:00:00Z'),
    ('TEST-UNFETCHED', 'TEST-P3', 990103, 'resolved', '2026-08-17T18:00:00Z'),
    ('TEST-DATA', 'TEST-P4', 990104, 'resolved', '2026-08-17T18:00:00Z');

insert into public.mlb_pitcher_k_results (
    mlb_game_pk,
    mlb_player_id,
    game_date,
    pitcher_name,
    strikeouts,
    did_start,
    appeared,
    mlb_abstract_state,
    mlb_detailed_state,
    fetched_at
) values
    (990001, 990101, '2026-08-17', 'Graded Pitcher', 6, true, true,
     'Final', 'Final', '2026-08-18T05:00:00Z'),
    (990004, 990104, '2026-08-17', 'Missing Stat Pitcher', null, true, true,
     'Final', 'Final', '2026-08-18T05:00:00Z');

insert into public.mlb_pitcher_k_odds (
    collection_id,
    captured_at,
    game_date,
    event_id,
    starts_at,
    player_id,
    odd_id,
    side,
    bookmaker_id,
    is_alternate,
    market_phase,
    line,
    american_odds
) values
    ('00000000-0000-0000-0000-000000000001', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-GRADED', '2026-08-17T23:00:00Z', 'TEST-P1',
     'test-over-55', 'over', 'book1', false, 'pregame', 5.5, 150),
    ('00000000-0000-0000-0000-000000000001', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-GRADED', '2026-08-17T23:00:00Z', 'TEST-P1',
     'test-under-55', 'under', 'book1', false, 'pregame', 5.5, -110),
    ('00000000-0000-0000-0000-000000000001', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-GRADED', '2026-08-17T23:00:00Z', 'TEST-P1',
     'test-over-60', 'over', 'book2', false, 'pregame', 6, -110),
    ('00000000-0000-0000-0000-000000000001', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-GRADED', '2026-08-17T23:00:00Z', 'TEST-P1',
     'test-under-60', 'under', 'book2', false, 'pregame', 6, 120),
    ('00000000-0000-0000-0000-000000000002', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-SCRATCH', '2026-08-17T23:00:00Z', 'TEST-P2',
     'test-scratch', 'over', 'book1', false, 'pregame', 5.5, -110),
    ('00000000-0000-0000-0000-000000000003', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-UNFETCHED', '2026-08-17T23:00:00Z', 'TEST-P3',
     'test-unfetched', 'over', 'book1', false, 'pregame', 5.5, -110),
    ('00000000-0000-0000-0000-000000000004', '2026-08-17T18:00:00Z',
     '2026-08-17', 'TEST-DATA', '2026-08-17T23:00:00Z', 'TEST-P4',
     'test-data', 'under', 'book1', false, 'pregame', 5.5, -110);

select results_eq(
    $$
        select bookmaker_id, side, grade, round(profit_units, 3)
        from public.mlb_pitcher_k_settlements
        where sgo_event_id = 'TEST-GRADED'
        order by bookmaker_id, side
    $$,
    $$ values
        ('book1'::text, 'over'::text, 'win'::text, 1.500::numeric),
        ('book1'::text, 'under'::text, 'loss'::text, -1.000::numeric),
        ('book2'::text, 'over'::text, 'push'::text, 0.000::numeric),
        ('book2'::text, 'under'::text, 'push'::text, 0.000::numeric)
    $$,
    'grades both sides and converts American odds to one-unit profit'
);

select is(
    (select settlement_status from public.mlb_pitcher_k_settlements
     where sgo_event_id = 'TEST-SCRATCH'),
    'void',
    'a fetched final with no pitcher row is void'
);

select is(
    (select void_reason from public.mlb_pitcher_k_settlements
     where sgo_event_id = 'TEST-SCRATCH'),
    'did_not_appear',
    'a fetched final with no pitcher row records the scratch reason'
);

select is(
    (select settlement_status from public.mlb_pitcher_k_settlements
     where sgo_event_id = 'TEST-UNFETCHED'),
    'pending',
    'a final whose boxscore was not fetched never becomes a false void'
);

select is(
    (select settlement_status from public.mlb_pitcher_k_settlements
     where sgo_event_id = 'TEST-DATA'),
    'data_pending',
    'missing strikeout facts remain pending'
);

select is(
    (select grade from public.mlb_pitcher_k_settlements
     where sgo_event_id = 'TEST-DATA'),
    null::text,
    'missing strikeout facts never grade an under as a win'
);

select * from finish();
rollback;
