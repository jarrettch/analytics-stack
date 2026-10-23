-- GitHub runs scheduled workflows hours late under load: the daily odds runs
-- started a median 3.2h late in October 2026, and the Sunday 12:30 ET NFL
-- close run fired after the 1pm kickoffs every week of the season. pg_cron
-- fires on the minute, so it owns the clock and dispatches the workflows,
-- which keep their Render wake-up, retries and response checks.
--
-- Manual step, once per project, never committed: store a fine-grained GitHub
-- token with Actions read/write on jarrettch/analytics-stack only.
--   select vault.create_secret('<token>', 'github_actions_dispatch_token');
-- net.http_post only queues the request, so a failed dispatch (401 once the
-- token expires) shows up in net._http_response, never as a failed cron run.
-- .github/workflows/check-collectors.yml fails when a collector stops running.
create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net with schema extensions;

-- The token sits in plaintext in net.http_request_queue until pg_net sends
-- it, and pg_net grants that table to PUBLIC. Supabase doesn't let projects
-- change those grants, and removing them breaks pg_net:
-- https://supabase.com/kb/troubleshooting/revoking-access-to-pg_net-objects-has-no-effect
-- net must stay out of the Data API's exposed schemas, so anon and
-- authenticated can't reach it, and every role with a direct database login
-- can read the token there, Vault access or not. Give LOGIN only to roles
-- trusted with it; the token is limited to Actions on this one repo.

-- Not exposed through the Data API; only the postgres role running cron jobs
-- calls into it.
create schema if not exists ops;
revoke all on schema ops from public, anon, authenticated;

-- pg_cron schedules in UTC. Each job fires at both UTC offsets ET can have,
-- and this keeps the firing that lands on the intended ET hour, so the
-- daylight-saving changes need no edits.
create or replace function ops.in_et_hours(at timestamptz, et_hours integer[])
returns boolean
language sql
stable
set search_path = ''
as $$
    select et_hours is null
        or extract(hour from at at time zone 'America/New_York')::integer = any(et_hours)
$$;

create or replace function ops.dispatch_workflow(workflow text, et_hours integer[] default null)
returns bigint
language plpgsql
set search_path = ''
as $$
declare
    token text;
begin
    if not ops.in_et_hours(now(), et_hours) then
        return null;
    end if;

    select decrypted_secret into token
    from vault.decrypted_secrets
    where name = 'github_actions_dispatch_token';
    if token is null then
        raise exception 'vault secret github_actions_dispatch_token is missing';
    end if;

    -- GitHub answers 204. The response lands in net._http_response.
    return net.http_post(
        url := format(
            'https://api.github.com/repos/jarrettch/analytics-stack/actions/workflows/%s/dispatches',
            workflow
        ),
        body := '{"ref": "main"}'::jsonb,
        headers := jsonb_build_object(
            'Authorization', 'Bearer ' || token,
            'Accept', 'application/vnd.github+json',
            'X-GitHub-Api-Version', '2022-11-28',
            'User-Agent', 'analytics-stack-pg-cron',
            'Content-Type', 'application/json'
        ),
        timeout_milliseconds := 10000
    );
end;
$$;

revoke all on function ops.in_et_hours(timestamptz, integer[]) from public, anon, authenticated;
revoke all on function ops.dispatch_workflow(text, integer[]) from public, anon, authenticated;

-- Without the token every job would fail when it fires, and the watchdog
-- could take a day to notice. The check and the scheduling are one
-- statement, so whatever client runs this file, a missing token schedules
-- nothing. Only the name is checked; the value is never read.
-- Scheduling by name replaces an existing job, so rerunning this is safe.
-- Times are ET.
do $$
begin
    if not exists (select from vault.secrets where name = 'github_actions_dispatch_token') then
        raise exception 'vault secret github_actions_dispatch_token is missing; not scheduling dispatches';
    end if;

    -- 11:00 catches 1pm ET weekend tips; 17:30 is the last line before 7pm tips.
    perform cron.schedule('fetch-odds-morning', '0 15,16 * * *', $job$select ops.dispatch_workflow('fetch-odds.yml', array[11])$job$);
    perform cron.schedule('fetch-odds-evening', '30 21,22 * * *', $job$select ops.dispatch_workflow('fetch-odds.yml', array[17])$job$);
    -- 03:00, after the last West Coast games, once books post tomorrow's openers.
    -- Not 02:00: on spring-forward Sunday that hour doesn't exist, so neither
    -- UTC firing would match. Keep every job out of the 01:00-02:59 ET range.
    perform cron.schedule('fetch-tomorrow-odds', '0 7,8 * * *', $job$select ops.dispatch_workflow('fetch-tomorrow-odds.yml', array[3])$job$);
    -- 15:00 MLB-only poll.
    perform cron.schedule('fetch-mlb-odds', '0 19,20 * * *', $job$select ops.dispatch_workflow('fetch-mlb-odds.yml', array[15])$job$);
    -- 04:30, after every MLB game is final.
    perform cron.schedule('settle-mlb-pitcher-ks', '30 8,9 * * *', $job$select ops.dispatch_workflow('settle-mlb-pitcher-ks.yml', array[4])$job$);
    -- 10:00 prices early kickoffs before ESPN drops them; 17:00 catches late ones.
    perform cron.schedule('fetch-ncaaf-odds-morning', '0 14,15 * * *', $job$select ops.dispatch_workflow('fetch-ncaaf-odds.yml', array[10])$job$);
    perform cron.schedule('fetch-ncaaf-odds-evening', '0 21,22 * * *', $job$select ops.dispatch_workflow('fetch-ncaaf-odds.yml', array[17])$job$);
    -- Sunday 12:30, before the 1pm window kicks off.
    perform cron.schedule('fetch-nfl-close-odds', '30 16,17 * * 0', $job$select ops.dispatch_workflow('fetch-nfl-close-odds.yml', array[12])$job$);
    -- 10:20 and 16:20 keep clear of the props runs at :07 and :37, which share
    -- the SharpAPI key.
    perform cron.schedule('fetch-ufc-odds-morning', '20 14,15 * * *', $job$select ops.dispatch_workflow('fetch-ufc-odds.yml', array[10])$job$);
    perform cron.schedule('fetch-ufc-odds-afternoon', '20 20,21 * * *', $job$select ops.dispatch_workflow('fetch-ufc-odds.yml', array[16])$job$);
    -- Props collect 10-90 minutes before kickoff, so 08:07-20:37 covers London
    -- 9:30am games through 8:20pm night games. The runner checks ESPN and exits
    -- early in empty windows; limiting the hours keeps the private repo's
    -- Actions minutes down, since every dispatch now actually runs.
    perform cron.schedule('fetch-nfl-player-props', '7,37 * * * *', $job$select ops.dispatch_workflow('fetch-nfl-player-props.yml', array[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20])$job$);
end;
$$;
