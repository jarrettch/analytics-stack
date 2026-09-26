from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from routers import utils


TODAY = date(2026, 9, 22)


class _FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 22, 12, 0, tzinfo=tz)


class _Query:
    def __init__(self, client: "_Supabase"):
        self.client = client
        self.dates: list[str] = []

    def select(self, *_args):
        return self

    def eq(self, _column: str, value: str):
        self.dates = [value]
        return self

    def in_(self, _column: str, values: list[str]):
        self.dates = values
        return self

    def execute(self):
        self.client.calls.append(tuple(self.dates))
        if TODAY.isoformat() in self.dates:
            self.client.today_attempts += 1
            if self.client.today_attempts <= self.client.fail_today_attempts:
                raise OSError(11, "Resource temporarily unavailable")

        return SimpleNamespace(
            data=[
                {
                    "game_date": value,
                    "matchup_key": f"AWAY{index}@HOME{index}",
                    "odds_data": {"homeMoneyline": -110 - index},
                }
                for index, value in enumerate(self.dates)
            ]
        )


class _Supabase:
    def __init__(self, *, fail_today_attempts: int):
        self.fail_today_attempts = fail_today_attempts
        self.today_attempts = 0
        self.calls: list[tuple[str, ...]] = []

    def table(self, _table_name: str):
        return _Query(self)


def test_three_day_load_retries_as_one_atomic_query():
    client = _Supabase(fail_today_attempts=1)

    with (
        patch.object(utils, "supabase", client),
        patch.object(utils, "datetime", _FixedDateTime),
        patch.object(utils.time, "sleep") as sleep,
    ):
        result = utils.load_persisted_odds(table_name="mlb_odds")

    assert len(result) == 3
    assert client.calls == [
        ("2026-09-21", "2026-09-22", "2026-09-23"),
        ("2026-09-21", "2026-09-22", "2026-09-23"),
    ]
    sleep.assert_called_once()


def test_three_day_load_raises_instead_of_returning_partial_odds():
    client = _Supabase(fail_today_attempts=10)

    with (
        patch.object(utils, "supabase", client),
        patch.object(utils, "datetime", _FixedDateTime),
        patch.object(utils.time, "sleep"),
        pytest.raises(RuntimeError, match="Failed to load persisted odds"),
    ):
        utils.load_persisted_odds(table_name="wnba_odds")

    assert client.today_attempts == 3

