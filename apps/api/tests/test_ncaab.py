from __future__ import annotations

import asyncio
import copy
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from main import app
from routers import espn_odds, ncaab, ncaaf, utils
from routers.espn_odds import extract_espn_odds, normalize_espn_odds
from routers.utils import CACHE_TTL_DEFAULT, ET, UTC, Cache

FIXTURES = Path(__file__).parent / "fixtures"
SLATE = date(2026, 11, 2)
CAPTURED = datetime(2026, 11, 3, 1, 22, 32, tzinfo=UTC)
USC = ("2026-11-02", "MILW@USC")
FLORIDA = ("2026-11-02", "MIA@FLA")
HAWAII = ("2025-11-10", "ETAM@HAW")


def _scoreboard(historical=False):
    name = "espn_ncaab_historical_scoreboard.json" if historical else "espn_ncaab_scoreboard.json"
    return json.loads((FIXTURES / name).read_text())


def _dk_entry():
    return _scoreboard()["events"][1]["competitions"][0]["odds"][0]


def _frozen_at(moment):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)
    return Frozen


def _fresh_cache():
    return Cache({"standings": 3600, "assembled": CACHE_TTL_DEFAULT})


def _today(scoreboard, persisted=None, slate=SLATE, standings=None):
    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ncaab, "cache", _fresh_cache()),
        patch.object(ncaab, "slate_date_et", return_value=slate),
        patch.object(ncaab, "fetch_espn_scoreboard", AsyncMock(return_value=scoreboard)) as fetch,
        patch.object(ncaab, "load_persisted_odds", return_value=persisted or {}) as load,
        patch.object(ncaab, "_standings_for_season", AsyncMock(return_value=standings or {})) as stats,
        patch.object(espn_odds, "datetime", _frozen_at(CAPTURED)),
    ):
        response = TestClient(app).get("/api/ncaab/today")
    assert response.status_code == 200
    load.assert_called_once_with(table_name="ncaab_odds")
    return response.json(), fetch, stats


def test_today_priced_ranked_game_and_unpriced_non_di_opponent():
    board = _scoreboard()
    unpriced = board["events"][2]["competitions"][0]
    assert not unpriced.get("odds")
    assert not unpriced["competitors"][1]["team"].get("conferenceId")
    games, fetch, stats = _today(board)
    fetch.assert_awaited_once_with(ncaab.ESPN_SCOREBOARD_URL, SLATE, {"groups": 50, "limit": 400})
    stats.assert_awaited_once_with(2027)
    assert len(games) == 1
    game = games[0]
    assert game["gameId"] == "401926771"
    assert game["sport"] == "ncaab"
    assert game["model"] == "MARKET (DK)"
    assert game["statsSeason"] == 2027
    assert game["date"] == game["slateDate"] == "2026-11-02"
    assert game["gameStatus"] == 1
    assert game["status"] == game["time"] == "10:30 PM ET"
    assert "week" not in game and "weekLabel" not in game
    assert game["homeMoneyline"] == -20000
    assert game["home"]["rank"] == 21
    assert game["away"]["rank"] is None
    assert game["home"]["espnSlug"] == "30"
    assert game["home"]["conferenceId"] == "7"
    assert game["home"]["pointsFor"] is None
    assert game["home"]["pointsAgainst"] is None
    assert game["home"]["predictedScore"] == 93.5
    assert game["away"]["predictedScore"] == 67
    assert game["projectedTotal"] == 160.5


def test_live_game_without_odds_stays_on_board_using_persisted_line():
    games, _, _ = _today(_scoreboard(), {FLORIDA: {"homeSpread": -8.5, "awaySpread": 8.5, "overUnder": 150}})
    live = games[0]
    assert live["gameId"] == "401906769"
    assert live["gameStatus"] == 2
    assert live["status"] == "2H 4:04"
    assert live["gameClock"] == "4:04"
    assert (live["away"]["score"], live["home"]["score"]) == (62, 71)
    assert live["overUnder"] == 150
    assert live["totalResult"] is None
    assert live["spreadResult"] is None


@pytest.mark.parametrize("missing", ["total", "pointSpread"])
def test_partial_market_with_no_standings_emits_null_projections(missing):
    board = _scoreboard()
    # Explicit variation of the real priced USC game: remove one market.
    entry = board["events"][1]["competitions"][0]["odds"][0]
    del entry[missing]
    if missing == "total":
        del entry["moneyline"]  # spread-only fixture case
    games, _, _ = _today(board)
    assert len(games) == 1
    assert games[0]["away"]["predictedScore"] is None
    assert games[0]["home"]["predictedScore"] is None
    assert games[0]["projectedTotal"] is None
    assert games[0]["home"]["pointsFor"] is None


def test_projection_ignores_standings_and_accepts_pick_em():
    games, _, _ = _today(_scoreboard(), standings={"USC": {"pointsFor": 10, "pointsAgainst": 20}})
    assert games[0]["home"]["predictedScore"] == 93.5
    assert ncaab._project_score({"overUnder": 150, "homeSpread": 0}) == (75, 75, 150)
    assert ncaab._project_score({}) == (None, None, None)


@pytest.mark.parametrize("period,label", [(1, "1H"), (2, "2H"), (3, "OT"), (4, "2OT"), (5, "3OT")])
def test_live_status_uses_halves_and_overtime(period, label):
    competition = {"status": {"period": period, "displayClock": "3:21", "type": {"state": "in"}}}
    assert ncaab._status(competition) == (2, f"{label} 3:21", period, "3:21")
    del competition["status"]["displayClock"]
    assert ncaab._status(competition) == (2, label, period, None)


def test_halftime_preserves_espn_short_detail():
    assert ncaab._status({"status": {
        "period": 1, "displayClock": "0:00",
        "type": {"state": "in", "name": "STATUS_HALFTIME", "shortDetail": "Halftime"},
    }}) == (2, "Halftime", 1, "0:00")


@pytest.mark.parametrize("rank", [0, 99, None])
def test_unranked_values(rank):
    assert ncaab._rank({"curatedRank": {"current": rank}}) is None


def test_overtime_finals_and_post_midnight_tip_use_persisted_et_keys():
    persisted = {
        ("2025-11-09", "UVM@BRWN"): {"homeSpread": 3, "awaySpread": -3, "overUnder": 160},
        ("2025-11-09", "USM@SC"): {"homeSpread": -4, "awaySpread": 4, "overUnder": 162},
        HAWAII: {"homeSpread": -25, "awaySpread": 25, "overUnder": 180},
    }
    games, fetch, stats = _today(_scoreboard(True), persisted, slate=date(2025, 11, 9))
    assert len(games) == 3
    assert [g["status"] for g in games] == ["F/OT", "F/OT", "Final"]
    assert [g["gameStatus"] for g in games] == [3, 3, 3]
    assert [g["period"] for g in games] == [4, 3, 2]
    assert all(g["gameClock"] is None for g in games)
    assert [g["totalResult"] for g in games] == ["over", "push", "under"]
    assert [g["spreadResult"] for g in games] == [True, "push", True]
    hawaii = games[-1]
    assert hawaii["date"] == "2025-11-10"
    assert hawaii["slateDate"] == "2025-11-09"
    assert hawaii["overUnder"] == 180
    assert fetch.await_args.args[1] == date(2025, 11, 9)
    stats.assert_awaited_once_with(2026)


@pytest.mark.parametrize("now", [datetime(2025, 11, 9, 23, tzinfo=ET), datetime(2025, 11, 10, 1, tzinfo=ET)])
def test_hawaii_stays_on_previous_slate_before_and_after_midnight(now):
    # Exercise the real slate rollover helper while replaying the real event.
    with patch.object(utils, "datetime", _frozen_at(now)):
        slate = ncaab.slate_date_et()
    games, _, _ = _today(_scoreboard(True), {HAWAII: {"overUnder": 180}}, slate=slate)
    assert games[0]["date"] == "2025-11-10"
    assert games[0]["slateDate"] == "2025-11-09"


@pytest.mark.parametrize("unconfigured", [False, True])
@pytest.mark.parametrize("stale", [False, True])
def test_persistence_failure_returns_503_even_with_stale_board(unconfigured, stale):
    cache = _fresh_cache()
    if stale:
        cache.set("assembled", [{"gameId": "stale"}])
        cache._entries["assembled"]["last_fetch"] = 0
    with (
        patch.object(utils, "supabase", None if unconfigured else MagicMock()),
        patch.object(ncaab, "cache", cache),
        patch.object(ncaab, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(ncaab, "load_persisted_odds", side_effect=RuntimeError("read failed")),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(ncaab.get_todays_games())
    assert error.value.status_code == 503
    assert error.value.detail == "NCAAB odds feed unavailable"


@pytest.mark.parametrize("slate,expected", [(date(2026, 11, 2), 2027), (date(2027, 1, 3), 2027)])
def test_missing_season_falls_back_to_end_year(slate, expected):
    board = _scoreboard()
    for event in board["events"]:
        del event["season"]["year"]
    games, _, stats = _today(board, slate=slate)
    stats.assert_awaited_once_with(expected)
    assert games[0]["statsSeason"] == expected


@pytest.mark.parametrize("record,expected", [("2-1", (82.3, 71.5)), ("0-0", (None, None))])
def test_standings_use_overall_averages_without_dividing_again(record, expected):
    payload = {"children": [{"isConference": True, "shortName": "Big Ten", "standings": {"entries": [{
        "team": {"abbreviation": "USC"},
        "stats": [
            {"name": "overall", "type": "total", "displayValue": record},
            {"name": "streak", "type": "streak", "displayValue": "W2"},
            {"name": "avgPointsFor", "type": "avgpointsfor", "value": 82.3},
            {"name": "avgPointsAgainst", "type": "avgpointsagainst", "value": 71.5},
            {"name": "avgPointsFor", "type": "vsconference_avgpointsfor", "value": 0},
            {"name": "avgPointsAgainst", "type": "vsconference_avgpointsagainst", "value": 0},
        ],
    }]}}]}
    response = MagicMock()
    response.json.return_value = payload
    client = AsyncMock()
    client.get.return_value = response
    with patch.object(ncaab, "get_http_client", return_value=client):
        standings = asyncio.run(ncaab._standings_for_season(2027))
    client.get.assert_awaited_once_with(ncaab.ESPN_STANDINGS_URL, params={"season": 2027, "group": 50})
    assert standings["USC"] == {"conference": "Big Ten", "streak": "W2", "pointsFor": expected[0], "pointsAgainst": expected[1]}


def test_extractor_only_captures_pregame_and_keeps_ids_and_open_prices():
    fresh, rows = extract_espn_odds(_scoreboard()["events"], {}, sport="ncaab", captured_at=CAPTURED)
    assert set(fresh) == {USC}
    assert fresh[USC]["awayTeamId"] == "270"
    assert fresh[USC]["homeTeamId"] == "30"
    assert fresh[USC]["openOverUnder"] == 156.5
    assert len(rows) == 1
    assert (rows[0]["sport"], rows[0]["provider"], rows[0]["bookmaker"]) == ("ncaab", "espn", "draftkings")
    assert rows[0]["game_date"] == "2026-11-02"
    assert rows[0]["captured_at"] == CAPTURED.isoformat()
    fresh, rows = extract_espn_odds(_scoreboard()["events"], {}, sport="ncaab", captured_at=datetime(2026, 11, 3, 4, tzinfo=UTC))
    assert not fresh and not rows  # stale pregame status cannot override tip time


@pytest.mark.parametrize("offset", [0, 1])
def test_cron_uses_calendar_date_and_ncaab_parameters(offset):
    async def fetch(**kwargs):
        kwargs["collection_stats"].update(latest_persistence_ok=True, snapshot_persistence_ok=True)
        return {USC: {}}, 1
    now = datetime(2026, 11, 3, 1, 30, tzinfo=ET)
    with (
        patch.object(ncaab, "datetime", _frozen_at(now)),
        patch.object(ncaab, "fetch_espn_odds", AsyncMock(side_effect=fetch)) as collect,
        patch.object(ncaab, "cache", _fresh_cache()),
    ):
        result = asyncio.run(ncaab.cron_fetch_odds(offset))
    kwargs = collect.await_args.kwargs
    assert kwargs["sport"] == "ncaab"
    assert kwargs["table_name"] == "ncaab_odds"
    assert kwargs["scoreboard_url"] == ncaab.ESPN_SCOREBOARD_URL
    assert kwargs["scoreboard_params"] == {"groups": 50, "limit": 400}
    assert kwargs["target_date"] == date(2026, 11, 3 + offset)
    assert result["date"] == f"2026-11-0{3 + offset}"
    assert result["success"] is True
    assert result["fresh_from_api"] == result["total_matchups"] == 1
    assert result["persisted_to"] == "supabase:ncaab_odds+odds_snapshots"


@pytest.mark.parametrize("module", [ncaab, ncaaf])
def test_tomorrow_scoreboard_loads_actual_et_dates_and_preserves_missing_total(module):
    # Now is Nov 8. Tomorrow's Nov 9 bucket holds Hawaii's Nov 10 ET tip.
    # The old calendar window missed Nov 10 and overwrote the saved total.
    board = _scoreboard(True)
    hawaii = board["events"][-1]
    competition = hawaii["competitions"][0]
    competition["status"] = {"type": {"state": "pre"}}
    competition["odds"] = [_dk_entry()]
    del competition["odds"][0]["total"]["over"]["open"]
    del competition["odds"][0]["total"]["over"]["close"]
    board["events"].append(copy.deepcopy(board["events"][0]))  # dates deduplicate
    prior = normalize_espn_odds(_dk_entry())
    prior["overUnder"] = 155.5
    now = datetime(2025, 11, 8, 17, tzinfo=ET)
    order = []

    async def scoreboard(*args):
        order.append("scoreboard")
        return board

    def load(game_date=None, *, table_name):
        order.append(game_date)
        return {HAWAII: prior} if game_date == date(2025, 11, 10) else {}

    with (
        patch.object(module, "datetime", _frozen_at(now)),
        patch.object(module, "cache", _fresh_cache()),
        patch.object(espn_odds, "datetime", _frozen_at(now)),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(side_effect=scoreboard)) as fetch,
        patch.object(espn_odds, "load_persisted_odds", side_effect=load) as loader,
        patch.object(espn_odds, "save_persisted_odds", return_value=True) as save,
        patch.object(espn_odds, "save_odds_snapshots", return_value=1) as snapshots,
    ):
        result = asyncio.run(module.cron_fetch_odds(offset=1))
    assert result["success"] is True
    assert result["date"] == "2025-11-09"
    assert result["fresh_from_api"] == result["total_matchups"] == 1
    assert fetch.await_args.args[1] == date(2025, 11, 9)
    assert order == ["scoreboard", date(2025, 11, 9), date(2025, 11, 10)]
    assert loader.call_args_list == [
        call(date(2025, 11, 9), table_name=module.TABLE_NAME),
        call(date(2025, 11, 10), table_name=module.TABLE_NAME),
    ]
    save.assert_called_once()
    assert save.call_args.kwargs == {"game_date": date(2025, 11, 10), "table_name": module.TABLE_NAME}
    assert save.call_args.args[0]["ETAM@HAW"]["overUnder"] == 155.5
    assert snapshots.call_args.args[0][0]["odds_data"]["overUnder"] == 155.5


@pytest.mark.parametrize("module", [ncaab, ncaaf])
def test_failed_second_date_read_stops_all_writes(module):
    with (
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard(True))),
        patch.object(espn_odds, "load_persisted_odds", side_effect=[{}, RuntimeError("supabase down")]),
        patch.object(espn_odds, "save_persisted_odds") as save,
        patch.object(espn_odds, "save_odds_snapshots") as snapshots,
    ):
        result = asyncio.run(module.cron_fetch_odds(offset=1))
    assert result["success"] is False
    assert "supabase down" in result["error"]
    save.assert_not_called()
    snapshots.assert_not_called()


@pytest.mark.parametrize("module", [ncaab, ncaaf])
def test_empty_scoreboard_loads_nothing_and_succeeds(module):
    with (
        patch.object(module, "cache", _fresh_cache()),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value={"events": []})),
        patch.object(espn_odds, "load_persisted_odds") as load,
        patch.object(espn_odds, "save_persisted_odds") as save,
        patch.object(espn_odds, "save_odds_snapshots", return_value=0),
    ):
        result = asyncio.run(module.cron_fetch_odds(offset=0))
    load.assert_not_called()
    save.assert_not_called()
    assert result["success"] is True
    assert result["total_matchups"] == result["persisted_loaded"] == result["fresh_from_api"] == 0
    assert result["pregame_snapshot_candidates"] == 0


def test_cron_surfaces_latest_write_failure_with_stats():
    with (
        patch.object(ncaab, "cache", _fresh_cache()),
        patch.object(espn_odds, "datetime", _frozen_at(CAPTURED)),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(espn_odds, "load_persisted_odds", return_value={}),
        patch.object(espn_odds, "save_persisted_odds", return_value=False),
        patch.object(espn_odds, "save_odds_snapshots", return_value=0),
    ):
        result = asyncio.run(ncaab.cron_fetch_odds(offset=0))
    assert result["success"] is False
    assert result["latest_persistence_ok"] is False
    assert result["pregame_snapshot_candidates"] == result["fresh_from_api"] == 1
