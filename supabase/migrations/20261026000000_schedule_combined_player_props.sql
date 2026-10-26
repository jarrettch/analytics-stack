-- NFL and NBA props now collect in one workflow, fetch-player-props.yml
-- (apps/api/specs/nba_player_props.md). Apply this only at rollout step 4,
-- after that workflow is on main and a manual dispatch of it succeeded:
-- until then pg_cron must keep dispatching fetch-nfl-player-props.yml.
--
-- The new job and the unschedule are one DO block, so whatever client runs
-- this file, the ticks are never left unscheduled or dispatched twice.
-- ops.dispatch_workflow() is unchanged. Rerunning this is safe.
do $$
begin
    if not exists (select from vault.secrets where name = 'github_actions_dispatch_token') then
        raise exception 'vault secret github_actions_dispatch_token is missing; not scheduling dispatches';
    end if;

    -- 08:07 catches London 9:30am NFL games; 22:07 is the last close sweep
    -- before 10:30pm NBA tips, and 22:37 catches anything later. 11:37 and
    -- 15:37 are also NBA's intraday sweeps; the runner gate picks the phase.
    perform cron.schedule('fetch-player-props', '7,37 * * * *', $job$select ops.dispatch_workflow('fetch-player-props.yml', array[8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22])$job$);
    if exists (select from cron.job where jobname = 'fetch-nfl-player-props') then
        perform cron.unschedule('fetch-nfl-player-props');
    end if;
end;
$$;
