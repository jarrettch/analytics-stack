-- Run after the NBA props migration against a disposable PostgreSQL database.
-- All fixture observations are rolled back, including the append-only table.
begin;

do $$
declare
    observation jsonb := '{
        "collection_id": "test-sweep", "captured_at": "2026-10-24T15:33:00Z",
        "game_date": "2026-10-24", "matchup_key": "MIA@CHA",
        "starts_at": "2026-10-24T22:00:00Z", "provider": "sharpapi",
        "provider_event_id": "nba_heat_hornets_2026-10-24_b3",
        "selection_id": "test-points-over", "bookmaker_id": "draftkings",
        "market_type": "player_points", "stat_category": "points",
        "selection_name": "Bam Adebayo", "selection_raw": "Over",
        "provider_player_name": "Bam Adebayo", "side": "over", "line": 18.5,
        "american_odds": -115, "is_main_line": true, "is_alternate_line": false,
        "is_active": true, "is_stale_pregame_price": false,
        "provider_timestamp": "2026-10-24T15:32:50Z"
    }'::jsonb;
    combo jsonb := observation || '{
        "selection_id": "test-pra-over",
        "market_type": "player_points_+_rebounds_+_assists",
        "stat_category": "points_rebounds_assists", "line": 31.5,
        "combo_check": "passed"
    }'::jsonb;
    batch jsonb;
    rows_before bigint;
begin
    assert public.append_nba_player_prop_odds(jsonb_build_array(observation, observation)) = 1,
        'duplicate observations in one sweep must insert once';
    assert public.append_nba_player_prop_odds(jsonb_build_array(observation)) = 0,
        'replaying a workflow must be idempotent';
    assert public.append_nba_player_prop_odds(jsonb_build_array(
        observation || '{"american_odds": -120}'::jsonb)) = 1,
        'a changed price in a replay must survive';
    assert public.append_nba_player_prop_odds(jsonb_build_array(
        observation || '{"collection_id": "test-next-sweep"}'::jsonb)) = 1,
        'unchanged prices in a new sweep must survive';

    -- combo_check: required and constrained on combos, forbidden on singles.
    assert public.append_nba_player_prop_odds(jsonb_build_array(
        combo,
        combo || '{"selection_id": "test-pra-failed", "combo_check": "failed"}'::jsonb,
        combo || '{"selection_id": "test-pra-unchecked", "combo_check": "unchecked"}'::jsonb)) = 3,
        'combo rows store passed, failed and unchecked outcomes';
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            combo || '{"selection_id": "combo-no-check", "combo_check": null}'::jsonb));
        raise exception 'expected combo without combo_check to fail';
    exception when check_violation then null;
    end;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            combo || '{"selection_id": "combo-bad-check", "combo_check": "maybe"}'::jsonb));
        raise exception 'expected an unknown combo_check value to fail';
    exception when check_violation then null;
    end;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "single-with-check", "combo_check": "passed"}'::jsonb));
        raise exception 'expected combo_check on a single-stat market to fail';
    exception when check_violation then null;
    end;

    -- Shape: every row is a line with a side, in a known market and book.
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "no-line", "line": null}'::jsonb));
        raise exception 'expected a null line to fail';
    exception when not_null_violation then null;
    end;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "bad-side", "side": "home"}'::jsonb));
        raise exception 'expected a non over/under side to fail';
    exception when check_violation then null;
    end;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "bad-market", "market_type": "first_basket"}'::jsonb));
        raise exception 'expected an unlisted market to fail';
    exception when check_violation then null;
    end;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "bad-book", "bookmaker_id": "betmgm"}'::jsonb));
        raise exception 'expected an unlisted book to fail';
    exception when check_violation then null;
    end;

    select count(*) into rows_before from public.nba_player_prop_odds;
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "valid-before-invalid"}'::jsonb,
            observation || '{"selection_id": null}'::jsonb));
        raise exception 'expected required selection_id failure';
    exception when not_null_violation then null;
    end;
    assert (select count(*) from public.nba_player_prop_odds) = rows_before,
        'a bad row must roll back all other rows in its batch';
    begin
        perform public.append_nba_player_prop_odds(jsonb_build_array(
            observation, observation || '{"collection_id": "other-sweep"}'::jsonb));
        raise exception 'expected mixed collections to fail';
    exception when raise_exception then
        if sqlerrm <> 'one collection per insert is required' then raise; end if;
    end;

    select jsonb_agg(observation || jsonb_build_object('selection_id', 'bulk-' || n))
    into batch from generate_series(1, 2300) as n;
    assert public.append_nba_player_prop_odds(batch) = 2300,
        'large sweeps must return an exact scalar count';

    begin
        update public.nba_player_prop_odds set american_odds = 100
        where collection_id = 'test-sweep';
        raise exception 'update unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nba_player_prop_odds is append-only' then raise; end if;
    end;
    begin
        delete from public.nba_player_prop_odds where collection_id = 'test-sweep';
        raise exception 'delete unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nba_player_prop_odds is append-only' then raise; end if;
    end;
    begin
        truncate public.nba_player_prop_odds;
        raise exception 'truncate unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nba_player_prop_odds is append-only' then raise; end if;
    end;

    assert (select relrowsecurity from pg_class where oid = 'public.nba_player_prop_odds'::regclass),
        'row level security must be enabled';
    assert not has_function_privilege('anon', 'public.append_nba_player_prop_odds(jsonb)', 'execute'),
        'anonymous callers must not execute the insert function';
    assert not has_function_privilege('authenticated', 'public.append_nba_player_prop_odds(jsonb)', 'execute'),
        'authenticated clients must not execute the insert function';
    assert has_function_privilege('service_role', 'public.append_nba_player_prop_odds(jsonb)', 'execute'),
        'the service role must be able to append observations';
end;
$$;

rollback;
