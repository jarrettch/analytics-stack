-- Run after the NBA props table and close coverage migrations against a
-- disposable PostgreSQL database. All fixture observations are rolled back.
begin;

do $$
declare
    -- Tip at 23:00Z; the close window is 21:30Z up to, not including, 23:00Z.
    observation jsonb := '{
        "collection_id": "coverage-test", "captured_at": "2026-10-24T22:30:00Z",
        "game_date": "2026-10-24", "matchup_key": "MIA@CHA",
        "starts_at": "2026-10-24T23:00:00Z", "provider": "sharpapi",
        "provider_event_id": "nba_heat_hornets_2026-10-24_b3",
        "selection_id": "points-over", "bookmaker_id": "draftkings",
        "market_type": "player_points", "stat_category": "points",
        "selection_name": "Bam Adebayo", "selection_raw": "Over",
        "provider_player_name": "Bam Adebayo", "side": "over", "line": 18.5,
        "american_odds": -115, "is_main_line": true, "is_alternate_line": false,
        "is_active": true, "is_stale_pregame_price": false,
        "provider_timestamp": "2026-10-24T22:30:00Z"
    }'::jsonb;
    combo jsonb := observation || '{
        "market_type": "player_points_+_rebounds_+_assists",
        "stat_category": "points_rebounds_assists", "line": 31.5
    }'::jsonb;
    cell record;
begin
    perform public.append_nba_player_prop_odds(jsonb_build_array(
        observation,
        observation || '{"selection_id": "points-under", "side": "under"}'::jsonb,
        observation || '{"selection_id": "points-other", "selection_name": "LaMelo Ball"}'::jsonb,
        -- The window's edges: 90 minutes before counts, the tip itself doesn't.
        observation || '{"selection_id": "edge-open", "selection_name": "Edge Open",
                         "provider_timestamp": "2026-10-24T21:30:00Z"}'::jsonb,
        observation || '{"selection_id": "edge-early", "selection_name": "Edge Early",
                         "provider_timestamp": "2026-10-24T21:29:59Z"}'::jsonb,
        observation || '{"selection_id": "edge-tip", "selection_name": "Edge Tip",
                         "provider_timestamp": "2026-10-24T23:00:00Z"}'::jsonb,
        observation || '{"selection_id": "pulled", "selection_name": "Pulled", "is_active": false}'::jsonb,
        observation || '{"selection_id": "stale", "selection_name": "Stale", "is_stale_pregame_price": true}'::jsonb,
        observation || '{"selection_id": "fd-points", "bookmaker_id": "fanduel"}'::jsonb,
        observation || '{"selection_id": "other-day", "game_date": "2026-10-25",
                         "starts_at": "2026-10-25T23:00:00Z",
                         "provider_timestamp": "2026-10-25T22:30:00Z"}'::jsonb,
        combo || '{"selection_id": "pra-passed", "combo_check": "passed"}'::jsonb,
        combo || '{"selection_id": "pra-unchecked", "combo_check": "unchecked"}'::jsonb,
        combo || '{"selection_id": "pra-failed", "combo_check": "failed"}'::jsonb,
        combo || '{"selection_id": "pr-unchecked", "market_type": "player_points_+_rebounds",
                   "combo_check": "unchecked"}'::jsonb
    ));

    select * into cell from public.nba_player_prop_close_coverage('2026-10-24')
    where bookmaker_id = 'draftkings' and market_type = 'player_points';
    assert cell.usable_rows = 4, format('usable rows: in-window, active, fresh; got %s', cell.usable_rows);
    assert cell.usable_players = 3, format('players are distinct selection names; got %s', cell.usable_players);
    assert cell.unchecked_rows = 0 and cell.failed_rows = 0, 'single-stat rows are never unchecked or failed';

    select * into cell from public.nba_player_prop_close_coverage('2026-10-24')
    where bookmaker_id = 'draftkings' and market_type = 'player_points_+_rebounds_+_assists';
    assert (cell.usable_rows, cell.unchecked_rows, cell.failed_rows) = (1, 1, 1),
        format('combo outcomes are counted apart; got %s', cell);

    select * into cell from public.nba_player_prop_close_coverage('2026-10-24')
    where market_type = 'player_points_+_rebounds';
    assert (cell.usable_rows, cell.usable_players, cell.unchecked_rows) = (0, 0, 1),
        'an unchecked-only cell comes back with no usable rows';

    assert (select count(*) from public.nba_player_prop_close_coverage('2026-10-24')) = 4,
        'one row per matchup, book and market with a row in the window';
    assert (select usable_rows from public.nba_player_prop_close_coverage('2026-10-24')
            where bookmaker_id = 'fanduel') = 1, 'books are counted apart';
    assert (select count(*) from public.nba_player_prop_close_coverage('2026-10-23')) = 0,
        'a date with no rows has no cells';

    assert not has_function_privilege('anon', 'public.nba_player_prop_close_coverage(date)', 'execute'),
        'anon cannot read coverage';
    assert not has_function_privilege('authenticated', 'public.nba_player_prop_close_coverage(date)', 'execute'),
        'authenticated cannot read coverage';
    assert has_function_privilege('service_role', 'public.nba_player_prop_close_coverage(date)', 'execute'),
        'the API reads coverage as service_role';
end;
$$;

rollback;
