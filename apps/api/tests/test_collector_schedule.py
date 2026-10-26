"""The pg_cron migration must dispatch each collector exactly at its ET times.

pg_cron schedules in UTC, so every job fires at both UTC offsets ET can have
and ops.in_et_hours keeps the one on the intended ET hour. This applies the
migrations' schedule and unschedule calls in order, then replays a year
spanning both DST transitions against the resulting cron expressions; the
SQL test covers ops.in_et_hours itself.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATIONS = sorted((REPO_ROOT / "supabase/migrations").glob("*.sql"))
WORKFLOWS = REPO_ROOT / ".github/workflows"
ET = ZoneInfo("America/New_York")

DAILY = frozenset(range(7))
SUNDAY = frozenset({6})
PROPS_TIMES = [f"{hour:02d}:{minute}" for hour in range(8, 23) for minute in ("07", "37")]

EXPECTED = {
    "fetch-odds-morning": ("fetch-odds.yml", ["11:00"], DAILY),
    "fetch-odds-evening": ("fetch-odds.yml", ["17:30"], DAILY),
    "fetch-tomorrow-odds": ("fetch-tomorrow-odds.yml", ["03:00"], DAILY),
    "fetch-mlb-odds": ("fetch-mlb-odds.yml", ["15:00"], DAILY),
    "settle-mlb-pitcher-ks": ("settle-mlb-pitcher-ks.yml", ["04:30"], DAILY),
    "fetch-ncaaf-odds-morning": ("fetch-ncaaf-odds.yml", ["10:00"], DAILY),
    "fetch-ncaaf-odds-evening": ("fetch-ncaaf-odds.yml", ["17:00"], DAILY),
    "fetch-nfl-close-odds": ("fetch-nfl-close-odds.yml", ["12:30"], SUNDAY),
    "fetch-ufc-odds-morning": ("fetch-ufc-odds.yml", ["10:20"], DAILY),
    "fetch-ufc-odds-afternoon": ("fetch-ufc-odds.yml", ["16:20"], DAILY),
    "fetch-player-props": ("fetch-player-props.yml", PROPS_TIMES, DAILY),
}

JOB = re.compile(
    r"cron\.schedule\('(?P<name>[^']+)', '(?P<expr>[^']+)', "
    r"\$job\$select ops\.dispatch_workflow\('(?P<workflow>[^']+)', array\[(?P<hours>[^\]]*)\]\)\$job\$\)"
)
UNSCHEDULE = re.compile(r"cron\.unschedule\('(?P<name>[^']+)'\)")


def cron_calls(sql: str) -> list[re.Match]:
    return sorted([*JOB.finditer(sql), *UNSCHEDULE.finditer(sql)], key=lambda m: m.start())


def cron_migrations() -> list[Path]:
    return [path for path in MIGRATIONS if cron_calls(path.read_text())]


def scheduled_jobs():
    """The jobs left after every migration, in order. Scheduling by name replaces."""
    jobs = {}
    for path in cron_migrations():
        for call in cron_calls(path.read_text()):
            if call.re is JOB:
                jobs[call["name"]] = call
            else:
                assert call["name"] in jobs, f"{path.name} unschedules unknown job {call['name']}"
                del jobs[call["name"]]
    return jobs


def cron_field(field: str, full: range) -> set[int]:
    return set(full) if field == "*" else {int(value) for value in field.split(",")}


def et_firings(expr: str, et_hours: set[int], start: date, end: date) -> dict[date, list[str]]:
    """ET date -> ET times the job dispatches, replaying every UTC minute it fires."""
    minute, hour, day, month, weekday = expr.split()
    assert (day, month) == ("*", "*"), expr
    minutes, hours = cron_field(minute, range(60)), cron_field(hour, range(24))
    weekdays = cron_field(weekday, range(7))  # cron: 0 is Sunday
    firings = defaultdict(list)
    for offset in range((end - start).days + 1):
        utc_day = start + timedelta(days=offset)
        if (utc_day.isoweekday() % 7) not in weekdays:
            continue
        for h in sorted(hours):
            for m in sorted(minutes):
                at = datetime(utc_day.year, utc_day.month, utc_day.day, h, m, tzinfo=UTC).astimezone(ET)
                if at.hour in et_hours:
                    firings[at.date()].append(at.strftime("%H:%M"))
    return firings


def test_migration_schedules_exactly_the_expected_jobs():
    assert set(scheduled_jobs()) == set(EXPECTED)


def guarded_schedule_blocks(sql: str) -> list[str]:
    blocks = re.findall(r"^do \$\$\n(.*?)^\$\$;", sql, re.M | re.S)
    return [body for body in blocks if "github_actions_dispatch_token is missing" in body]


@pytest.mark.parametrize("migration", cron_migrations(), ids=lambda path: path.name)
def test_a_missing_token_schedules_nothing_whatever_client_runs_the_migration(migration: Path):
    """Statements a client sends one at a time autocommit, so the token check
    and every schedule or unschedule must stay in one DO block, with the check
    first. A swap then never leaves a job missing or doubled."""
    sql = migration.read_text()
    [body] = guarded_schedule_blocks(sql)
    guard = (
        "begin if not exists (select from vault.secrets where name = 'github_actions_dispatch_token') then "
        "raise exception 'vault secret github_actions_dispatch_token is missing; not scheduling dispatches'; "
        "end if;"
    )

    assert " ".join(body.split()).startswith(guard)
    assert len(cron_calls(body)) == len(cron_calls(sql))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_job_dispatches_once_per_et_time_across_both_dst_changes(name: str):
    workflow, times, weekdays = EXPECTED[name]
    job = scheduled_jobs()[name]
    assert job["workflow"] == workflow
    # Fall back 2026-11-01, spring forward 2027-03-14, fall back 2027-11-07.
    start, end = date(2026, 10, 24), date(2027, 11, 14)
    firings = et_firings(job["expr"], {int(h) for h in job["hours"].split(",")}, start, end)

    wrong = {}
    for offset in range(1, (end - start).days):  # skip edges the UTC replay only half covers
        et_day = start + timedelta(days=offset)
        expected = times if et_day.weekday() in weekdays else []
        if firings.get(et_day, []) != expected:
            wrong[et_day.isoformat()] = firings.get(et_day, [])
    assert wrong == {}


@pytest.mark.parametrize("workflow", sorted({workflow for workflow, _, _ in EXPECTED.values()}))
def test_dispatched_workflows_are_manual_only(workflow: str):
    """GitHub's own schedule would run each collector a second time, hours late."""
    contents = (WORKFLOWS / workflow).read_text()

    assert "workflow_dispatch:" in contents
    assert "schedule:" not in contents


def test_watchdog_checks_every_collector_within_its_window():
    """Daily collectors get 26 hours. The weekly one gets 7.5 days: a missed
    Sunday then fails the next check after early Monday UTC."""
    contents = (WORKFLOWS / "check-collectors.yml").read_text()
    checks = [(m[1], int(m[2])) for m in re.finditer(r"^\s*check (\S+\.yml) (\d+)$", contents, re.M)]
    expected = {
        workflow: 26 if weekdays == DAILY else 180
        for workflow, _, weekdays in EXPECTED.values()
    }

    assert len(checks) == len({workflow for workflow, _ in checks}), "one check per collector"
    assert dict(checks) == expected


def test_watchdog_flags_cancelled_runs_in_the_shared_sharpapi_group():
    """GitHub keeps one pending run per concurrency group and cancels the
    older one, so a cancelled run there may be a skipped collection."""
    contents = (WORKFLOWS / "check-collectors.yml").read_text()
    checks = dict(re.findall(r"^\s*check_cancelled (\S+\.yml) (\d+)$", contents, re.M))
    dispatched = {workflow for workflow, _, _ in EXPECTED.values()}
    shared = {
        path.name for path in WORKFLOWS.glob("*.yml")
        if path.name in dispatched and re.search(r"^\s*group: sharpapi-odds$", path.read_text(), re.M)
    }

    assert shared == {"fetch-player-props.yml", "fetch-ufc-odds.yml"}
    assert checks == {workflow: "26" for workflow in shared}


@pytest.mark.parametrize("first_sunday", [date(2026, 10, 18), date(2027, 3, 7)], ids=["fall-back", "spring-forward"])
@pytest.mark.parametrize("delay_hours", range(0, 12))
def test_weekly_window_flags_a_missed_sunday_on_monday_without_false_alarms(first_sunday, delay_hours):
    """Sunday 12:30 ET across a DST change, against the watchdog's 18:00 UTC
    start plus GitHub's delay."""
    window = timedelta(hours=180)
    sundays = [
        datetime.combine(first_sunday + timedelta(weeks=week), datetime.min.time(), ET)
        .replace(hour=12, minute=30)
        .astimezone(UTC)
        for week in range(4)
    ]
    assert len({s.hour for s in sundays}) == 2, "the runs straddle a DST change"

    def watchdog_on(day: date) -> datetime:
        return datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=18 + delay_hours)

    for check in (watchdog_on(first_sunday + timedelta(days=day)) for day in range(22)):
        last_run = max(s for s in sundays if s <= check)
        assert check - last_run < window, f"false alarm at {check}"

    for missed in sundays[1:]:
        monday_check = watchdog_on(missed.date() + timedelta(days=1))
        last_run = max(s for s in sundays if s <= monday_check and s != missed)
        assert monday_check - last_run >= window, f"missed {missed} not flagged at {monday_check}"
