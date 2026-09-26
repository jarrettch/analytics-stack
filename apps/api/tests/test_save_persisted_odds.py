from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from routers import utils

GAME_DATE = date(2026, 9, 26)
ODDS = {"TEX@TENN": {"homeMoneyline": 164}}


class _Table:
    def __init__(self, client):
        self.client = client

    def upsert(self, rows, on_conflict):
        self.client.upserts.append((rows, on_conflict))
        return self

    def execute(self):
        if self.client.error:
            raise self.client.error
        return SimpleNamespace(data=[])


class _Client:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.upserts: list = []

    def table(self, _name):
        return _Table(self)


def test_empty_map_is_a_successful_noop_even_without_supabase():
    client = _Client()
    with patch.object(utils, "supabase", client):
        assert utils.save_persisted_odds({}, GAME_DATE, table_name="ncaaf_odds") is True
    assert client.upserts == []

    with patch.object(utils, "supabase", None):
        assert utils.save_persisted_odds({}, GAME_DATE, table_name="ncaaf_odds") is True


def test_nonempty_write_reports_whether_it_landed():
    client = _Client()
    with patch.object(utils, "supabase", client):
        assert utils.save_persisted_odds(ODDS, GAME_DATE, table_name="ncaaf_odds") is True
    rows, on_conflict = client.upserts[0]
    assert rows == [{
        "game_date": "2026-09-26",
        "matchup_key": "TEX@TENN",
        "odds_data": {"homeMoneyline": 164},
    }]
    assert on_conflict == "game_date,matchup_key"

    with patch.object(utils, "supabase", _Client(error=RuntimeError("down"))):
        assert utils.save_persisted_odds(ODDS, GAME_DATE, table_name="ncaaf_odds") is False


def test_nonempty_write_without_supabase_fails():
    with patch.object(utils, "supabase", None):
        assert utils.save_persisted_odds(ODDS, GAME_DATE, table_name="ncaaf_odds") is False
