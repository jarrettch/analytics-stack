"""SGO event windows should skip started games without shrinking future slates."""

from datetime import date, datetime

import pytest

from routers.utils import ET, UTC, sgo_event_window


def test_same_day_window_starts_at_current_time():
    now = datetime(2026, 8, 22, 15, 0, tzinfo=ET)

    starts_after, starts_before = sgo_event_window(date(2026, 8, 22), now)

    assert starts_after == datetime(2026, 8, 22, 19, 0, tzinfo=UTC)
    assert starts_before == datetime(2026, 8, 23, 4, 0, tzinfo=UTC)


def test_future_day_window_still_starts_at_midnight_et():
    now = datetime(2026, 8, 22, 2, 0, tzinfo=ET)

    starts_after, starts_before = sgo_event_window(date(2026, 8, 23), now)

    assert starts_after == datetime(2026, 8, 23, 4, 0, tzinfo=UTC)
    assert starts_before == datetime(2026, 8, 24, 4, 0, tzinfo=UTC)


def test_future_day_window_respects_et_daylight_saving_transition():
    now = datetime(2026, 10, 31, 21, 0, tzinfo=ET)

    starts_after, starts_before = sgo_event_window(date(2026, 11, 1), now)

    assert starts_after == datetime(2026, 11, 1, 4, 0, tzinfo=UTC)
    assert starts_before == datetime(2026, 11, 2, 5, 0, tzinfo=UTC)


def test_collection_window_uses_calendar_date_during_display_rollover():
    now = datetime(2026, 8, 22, 2, 0, tzinfo=ET)

    starts_after, starts_before = sgo_event_window(date(2026, 8, 22), now)

    assert starts_after == datetime(2026, 8, 22, 6, 0, tzinfo=UTC)
    assert starts_before == datetime(2026, 8, 23, 4, 0, tzinfo=UTC)


def test_past_day_window_is_rejected():
    now = datetime(2026, 8, 23, 0, 1, tzinfo=ET)

    with pytest.raises(ValueError, match="past ET date"):
        sgo_event_window(date(2026, 8, 22), now)
