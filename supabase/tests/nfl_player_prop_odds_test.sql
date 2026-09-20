-- Run after the NFL props migration against a disposable PostgreSQL database.
-- All fixture observations are rolled back, including the append-only table.
begin;

do $$
declare
    observation jsonb := '{
        "collection_id": "test-sweep", "captured_at": "2026-09-20T16:00:00Z",
        "game_date": "2026-09-20", "matchup_key": "MIN@CHI",
        "starts_at": "2026-09-20T17:00:00Z", "provider": "sharpapi",
        "provider_event_id": "book-event", "selection_id": "test-defense",
        "bookmaker_id": "draftkings", "market_type": "anytime_touchdown_scorer",
        "market_shape": "scorer", "selection_name": "MIN Vikings D/ST",
        "selection_raw": "MIN Vikings D/ST", "provider_player_name": "Anytime TD",
        "line": null, "side": null, "american_odds": 500,
        "is_main_line": true, "is_alternate_line": false, "is_active": true,
        "is_stale_pregame_price": false, "provider_timestamp": "2026-09-20T15:59:00Z"
    }'::jsonb;
    batch jsonb;
    inserted integer;
    rows_before bigint;
begin
    inserted := public.append_nfl_player_prop_odds(jsonb_build_array(observation, observation));
    assert inserted = 1, 'scorer duplicates with null lines must insert once';
    assert public.append_nfl_player_prop_odds(jsonb_build_array(observation)) = 0,
        'replaying a workflow must be idempotent';
    assert public.append_nfl_player_prop_odds(jsonb_build_array(
        observation || '{"american_odds": 550}'::jsonb)) = 1,
        'a changed price in a replay must survive';
    assert public.append_nfl_player_prop_odds(jsonb_build_array(
        observation || '{"collection_id": "test-next-sweep"}'::jsonb)) = 1,
        'unchanged prices in a new sweep must survive';

    select count(*) into rows_before from public.nfl_player_prop_odds;
    begin
        perform public.append_nfl_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "valid-before-invalid"}'::jsonb,
            observation || '{"selection_id": null}'::jsonb));
        raise exception 'expected required selection_id failure';
    exception when not_null_violation then null;
    end;
    assert (select count(*) from public.nfl_player_prop_odds) = rows_before,
        'a bad row must roll back all other rows in its batch';

    begin
        perform public.append_nfl_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "bad-shape", "side": "over"}'::jsonb));
        raise exception 'expected scorer shape check failure';
    exception when check_violation then null;
    end;
    begin
        perform public.append_nfl_player_prop_odds(jsonb_build_array(
            observation || '{"selection_id": "bad-line", "market_shape": "line"}'::jsonb));
        raise exception 'expected line shape check failure';
    exception when check_violation then null;
    end;

    select jsonb_agg(observation || jsonb_build_object('selection_id', 'bulk-' || n))
    into batch from generate_series(1, 2300) as n;
    assert public.append_nfl_player_prop_odds(batch) = 2300,
        'large sweeps must return an exact scalar count';

    begin
        update public.nfl_player_prop_odds set american_odds = 600
        where collection_id = 'test-sweep';
        raise exception 'update unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nfl_player_prop_odds is append-only' then raise; end if;
    end;
    begin
        delete from public.nfl_player_prop_odds where collection_id = 'test-sweep';
        raise exception 'delete unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nfl_player_prop_odds is append-only' then raise; end if;
    end;
    begin
        truncate public.nfl_player_prop_odds;
        raise exception 'truncate unexpectedly succeeded';
    exception when raise_exception then
        if sqlerrm <> 'nfl_player_prop_odds is append-only' then raise; end if;
    end;

    assert (select relrowsecurity from pg_class where oid = 'public.nfl_player_prop_odds'::regclass),
        'row level security must be enabled';
    assert not has_function_privilege('anon', 'public.append_nfl_player_prop_odds(jsonb)', 'execute'),
        'anonymous callers must not execute the insert function';
    assert not has_function_privilege('authenticated', 'public.append_nfl_player_prop_odds(jsonb)', 'execute'),
        'authenticated clients must not execute the insert function';
    assert has_function_privilege('service_role', 'public.append_nfl_player_prop_odds(jsonb)', 'execute'),
        'the service role must be able to append observations';
end;
$$;

rollback;
