-- Run in the Supabase SQL editor after the pg_cron migration and the Vault
-- secret. Everything rolls back: pg_net only sends committed requests, so no
-- workflow is dispatched and the secret is restored. The full-year DST replay
-- of each job's schedule is apps/api/tests/test_collector_schedule.py.
begin;

do $$
declare
    request_id bigint;
    request record;
    queued bigint;
begin
    -- Each job fires at two UTC hours; exactly one lands on the ET hour.
    assert ops.in_et_hours('2026-10-23 15:00Z', array[11]), 'EDT: 15:00 UTC is 11:00 ET';
    assert not ops.in_et_hours('2026-10-23 16:00Z', array[11]), 'EDT: 16:00 UTC is noon, a second run';
    assert not ops.in_et_hours('2026-11-23 15:00Z', array[11]), 'EST: 15:00 UTC is 10:00 ET, too early';
    assert ops.in_et_hours('2026-11-23 16:00Z', array[11]), 'EST: 16:00 UTC is 11:00 ET';
    assert ops.in_et_hours('2026-11-01 17:30Z', array[12]), 'fall-back Sunday: 17:30 UTC is 12:30 ET';
    assert not ops.in_et_hours('2026-11-01 16:30Z', array[12]), 'fall-back Sunday: 16:30 UTC is 11:30 ET';
    -- 2am doesn't exist on spring-forward Sunday, which is why no job uses it.
    assert not ops.in_et_hours('2027-03-14 06:00Z', array[2]), 'spring-forward: 06:00 UTC is 01:00 ET';
    assert not ops.in_et_hours('2027-03-14 07:00Z', array[2]), 'spring-forward: 07:00 UTC is 03:00 ET';
    assert ops.in_et_hours('2027-03-14 07:00Z', array[3]), 'spring-forward: the 3am job still runs';
    assert ops.in_et_hours('2026-10-23 03:00Z', null), 'no hours means always';

    assert (
        select count(*) from cron.job
        where active and command like '%ops.dispatch_workflow(%' and jobname in (
            'fetch-odds-morning', 'fetch-odds-evening', 'fetch-tomorrow-odds',
            'fetch-mlb-odds', 'settle-mlb-pitcher-ks', 'fetch-ncaaf-odds-morning',
            'fetch-ncaaf-odds-evening', 'fetch-nfl-close-odds', 'fetch-ufc-odds-morning',
            'fetch-ufc-odds-afternoon', 'fetch-nfl-player-props'
        )
    ) = 11, 'all 11 collector jobs are installed and active';

    queued := (select count(*) from net.http_request_queue);
    assert ops.dispatch_workflow('fetch-ufc-odds.yml', array[-1]) is null,
        'outside its ET hours a job returns no request';
    assert (select count(*) from net.http_request_queue) = queued,
        'outside its ET hours a job queues nothing';

    request_id := ops.dispatch_workflow('fetch-ufc-odds.yml');
    select * into request from net.http_request_queue where id = request_id;
    assert request.method = 'POST', 'dispatch is a POST';
    assert request.url = 'https://api.github.com/repos/jarrettch/analytics-stack/actions/workflows/fetch-ufc-odds.yml/dispatches',
        'dispatch targets the workflow file';
    assert convert_from(request.body, 'utf8')::jsonb = '{"ref": "main"}', 'dispatch runs main';
    assert request.headers ->> 'Authorization' like 'Bearer _%', 'dispatch carries the Vault token';
    assert request.headers ->> 'Accept' = 'application/vnd.github+json', 'GitHub JSON media type';
    assert request.headers ->> 'X-GitHub-Api-Version' = '2022-11-28', 'pinned GitHub API version';
    assert request.headers ->> 'Content-Type' = 'application/json', 'JSON body';
    assert request.headers ->> 'User-Agent' is not null, 'GitHub rejects requests without a User-Agent';
    assert request.timeout_milliseconds = 10000, 'dispatch timeout';

    assert not has_function_privilege('anon', 'ops.dispatch_workflow(text, integer[])', 'execute'),
        'anon cannot dispatch workflows';
    assert not has_function_privilege('authenticated', 'ops.dispatch_workflow(text, integer[])', 'execute'),
        'authenticated cannot dispatch workflows';

    delete from vault.secrets where name = 'github_actions_dispatch_token';
    begin
        perform ops.dispatch_workflow('fetch-ufc-odds.yml');
        assert false, 'a missing token must raise';
    exception when raise_exception then
        assert sqlerrm like '%github_actions_dispatch_token is missing%', sqlerrm;
    end;
end;
$$;

rollback;
