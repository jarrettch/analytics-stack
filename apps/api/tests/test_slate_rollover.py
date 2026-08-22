"""The slate day runs until 6am ET, not midnight.

Rolling at midnight dropped in-progress West Coast games off the board: a
7:10pm PT first pitch is 10:10pm ET and can run past 1am ET, at which point its
ET calendar date is already "yesterday".

The cron prefetch endpoints deliberately stay on the calendar date. The 2am ET
workflow passes offset=1 to mean "tomorrow", and a 6am baseline at 2am resolves
to the previous day, which would quietly turn that into "today".
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from routers.utils import ET, slate_date_et


def et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.mark.parametrize(
    "now,expected,why",
    [
        (et(2026, 8, 21, 12, 0), date(2026, 8, 21), "midday is its own slate"),
        (et(2026, 8, 21, 22, 10), date(2026, 8, 21), "10pm ET first pitch"),
        (et(2026, 8, 21, 23, 59), date(2026, 8, 21), "one minute to midnight"),
        (et(2026, 8, 22, 0, 30), date(2026, 8, 21), "past midnight, game still live"),
        (et(2026, 8, 22, 1, 45), date(2026, 8, 21), "extra innings on the coast"),
        (et(2026, 8, 22, 5, 59), date(2026, 8, 21), "just before rollover"),
        (et(2026, 8, 22, 6, 0), date(2026, 8, 22), "rollover"),
        (et(2026, 8, 22, 6, 1), date(2026, 8, 22), "just after rollover"),
    ],
)
def test_slate_date_tracks_the_sports_day(now, expected, why):
    assert slate_date_et(now) == expected, why


def test_midnight_to_six_no_longer_advances_the_board():
    """The exact window that used to drop live games."""
    before = slate_date_et(et(2026, 8, 21, 23, 30))
    after = slate_date_et(et(2026, 8, 22, 0, 30))
    assert before == after == date(2026, 8, 21)
    # ...and the old behaviour would have moved it.
    assert et(2026, 8, 22, 0, 30).date() != before


def test_rollover_survives_a_dst_boundary():
    """Nov 1 2026 is the EDT->EST fall back; 6am ET is still 6am ET."""
    assert slate_date_et(et(2026, 11, 1, 5, 30)) == date(2026, 10, 31)
    assert slate_date_et(et(2026, 11, 1, 6, 30)) == date(2026, 11, 1)


def test_cron_baseline_is_not_the_slate_date():
    """Guards the 2am ET prefetch.

    fetch-tomorrow-odds.yml runs at 06:00 UTC (2am EDT) with offset=1. If the
    cron endpoints ever switch to slate_date_et(), offset=1 silently becomes
    today and tomorrow's opening lines are never collected.
    """
    cron_run = et(2026, 8, 22, 2, 0)
    assert cron_run.date() == date(2026, 8, 22), "calendar date the cron relies on"
    assert slate_date_et(cron_run) == date(2026, 8, 21), "slate date lags at 2am"
    assert slate_date_et(cron_run) != cron_run.date(), (
        "these must stay distinct — that difference is the whole reason "
        "cron/fetch-odds keeps datetime.now(ET).date()"
    )

