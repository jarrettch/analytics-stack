from __future__ import annotations

import asyncio
import copy
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers import espn_odds, ncaaf, utils
from routers.espn_odds import (
    extract_espn_odds,
    normalize_espn_odds,
    select_draftkings_odds,
)
from routers.utils import CACHE_TTL_DEFAULT, ET, UTC, Cache

FIXTURE = Path(__file__).parent / "fixtures" / "espn_ncaaf_scoreboard.json"
SLATE = date(2026, 9, 26)
PREGAME = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
TEX_TENN = ("2026-09-26", "TEX@TENN")
ILL_OSU = ("2026-09-26", "ILL@OSU")


def _scoreboard() -> dict:
    return json.loads(FIXTURE.read_text())


def _competition(scoreboard: dict, index: int = 0) -> dict:
    return scoreboard["events"][index]["competitions"][0]


def _dk_entry() -> dict:
    return _competition(_scoreboard())["odds"][0]


def _frozen_at(moment: datetime) -> type[datetime]:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)

    return Frozen


def _fresh_cache() -> Cache:
    return Cache({"standings": 3600, "assembled": CACHE_TTL_DEFAULT})


# --- provider selection -----------------------------------------------------


def test_selects_draftkings_by_provider_id_not_index():
    competition = _competition(_scoreboard())
    dk = competition["odds"][0]
    other = copy.deepcopy(dk)
    other["provider"] = {"id": "58", "name": "ESPN BET"}
    competition["odds"] = [other, dk]

    assert select_draftkings_odds(competition) is dk


def test_selection_ignores_provider_name_spelling():
    competition = _competition(_scoreboard())
    competition["odds"][0]["provider"]["name"] = "DraftKings"  # MLB spelling

    assert select_draftkings_odds(competition) is competition["odds"][0]


def test_no_draftkings_entry_means_no_line_rather_than_index_zero(caplog):
    competition = _competition(_scoreboard())
    competition["odds"][0]["provider"] = {"id": "58", "name": "Draft Kings"}

    assert select_draftkings_odds(competition) is None
    assert "no DraftKings entry" in caplog.text


# --- normalization ----------------------------------------------------------


def test_normalizes_close_prices_to_numbers():
    odds = normalize_espn_odds(_dk_entry())

    assert odds["homeMoneyline"] == 164
    assert odds["awayMoneyline"] == -198
    assert odds["homeSpread"] == 5.5
    assert odds["awaySpread"] == -5.5
    assert odds["homeSpreadOdds"] == -115
    assert odds["awaySpreadOdds"] == -105
    assert odds["overUnder"] == 54.5  # "o54.5" with the leading o stripped
    assert odds["overOdds"] == -112
    assert odds["underOdds"] == -108
    assert odds["awayWinProbability"] > 0.5 > odds["homeWinProbability"]
    assert odds["awayWinProbability"] + odds["homeWinProbability"] == pytest.approx(1)


def test_opening_prices_ride_along_in_the_same_bundle():
    odds = normalize_espn_odds(_dk_entry())

    assert odds["openHomeMoneyline"] == 210
    assert odds["openAwayMoneyline"] == -258
    assert odds["openHomeSpread"] == 7
    assert odds["openOverUnder"] == 56.5


def test_missing_close_leg_falls_back_to_open_then_persisted():
    entry = _dk_entry()
    entry["moneyline"]["home"]["close"] = {"odds": "OFF"}
    del entry["total"]["over"]["close"]
    del entry["total"]["over"]["open"]

    odds = normalize_espn_odds(entry, persisted={"overUnder": 55, "overOdds": -120})

    assert odds["homeMoneyline"] == 210  # open
    assert odds["overUnder"] == 55  # persisted
    assert odds["overOdds"] == -120
    assert odds["underOdds"] == -108  # untouched leg


def test_even_price_parses_as_plus_100():
    entry = _dk_entry()
    entry["pointSpread"]["home"]["close"]["odds"] = "EVEN"

    assert normalize_espn_odds(entry)["homeSpreadOdds"] == 100


# --- extraction -------------------------------------------------------------


def test_extract_keys_by_espn_abbreviations_and_keeps_team_ids():
    fresh, snapshots = extract_espn_odds(
        _scoreboard()["events"], {}, sport="ncaaf", captured_at=PREGAME
    )

    assert set(fresh) == {TEX_TENN, ILL_OSU}
    assert fresh[TEX_TENN]["awayTeamId"] == "251"
    assert fresh[TEX_TENN]["homeTeamId"] == "2633"
    assert len(snapshots) == 2
    assert {row["provider"] for row in snapshots} == {"espn"}
    assert {row["bookmaker"] for row in snapshots} == {"draftkings"}
    assert snapshots[0]["captured_at"] == PREGAME.isoformat()


def test_game_started_per_espn_status_is_neither_fresh_nor_snapshotted():
    scoreboard = _scoreboard()
    _competition(scoreboard)["status"] = {"type": {"state": "in"}}

    fresh, snapshots = extract_espn_odds(
        scoreboard["events"], {}, sport="ncaaf", captured_at=PREGAME
    )

    assert set(fresh) == {ILL_OSU}
    assert [row["matchup_key"] for row in snapshots] == ["ILL@OSU"]


def test_game_past_scheduled_start_is_neither_fresh_nor_snapshotted():
    # Status still says "pre", but the clock is past the 16:00Z kickoff.
    scoreboard = _scoreboard()
    scoreboard["events"][0]["date"] = "2026-09-26T11:00Z"

    fresh, snapshots = extract_espn_odds(
        scoreboard["events"], {}, sport="ncaaf", captured_at=PREGAME
    )

    assert TEX_TENN not in fresh
    assert "TEX@TENN" not in {row["matchup_key"] for row in snapshots}


def test_game_without_odds_block_is_absent_from_fresh_map():
    scoreboard = _scoreboard()
    del _competition(scoreboard)["odds"]

    fresh, _ = extract_espn_odds(scoreboard["events"], {}, sport="ncaaf")

    assert TEX_TENN not in fresh


# --- /today -----------------------------------------------------------------


def _run_today(scoreboard: dict, persisted: dict, standings: dict | None = None):
    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ncaaf, "cache", _fresh_cache()),
        patch.object(ncaaf, "slate_date_et", return_value=SLATE),
        patch.object(ncaaf, "fetch_espn_scoreboard", AsyncMock(return_value=scoreboard)) as fetch,
        patch.object(ncaaf, "load_persisted_odds", return_value=persisted),
        patch.object(ncaaf, "_standings_for_season", AsyncMock(return_value=standings or {})),
    ):
        return asyncio.run(ncaaf.get_todays_games()), fetch


def test_today_anchors_on_slate_date_and_shapes_matchups():
    games, fetch = _run_today(_scoreboard(), {})

    assert fetch.await_args.args[1] == SLATE
    assert [game["gameId"] for game in games] == ["401856704", "401858465"]
    game = games[0]
    assert game["sport"] == "ncaaf"
    assert game["model"] == "MARKET (DK)"
    assert game["weekLabel"] == "WK4"
    assert game["homeMoneyline"] == 164
    away, home = game["away"], game["home"]
    assert (away["abbreviation"], away["rank"]) == ("TEX", 1)
    assert "seed" not in away  # CFP seeds differ from rank; never derive one
    assert home["espnSlug"] == "2633"
    assert home["conferenceId"] == "8"
    assert home["yardsPG"] is None


def test_unranked_99_normalizes_to_null():
    games, _ = _run_today(_scoreboard(), {})

    illinois = games[1]["away"]
    assert illinois["abbreviation"] == "ILL"
    assert illinois["rank"] is None


def test_kicked_off_game_stays_on_board_through_persisted_odds():
    scoreboard = _scoreboard()
    competition = _competition(scoreboard)
    del competition["odds"]  # ESPN drops the block at kickoff
    competition["status"] = {"period": 2, "displayClock": "4:11", "type": {"state": "in"}}
    persisted = {TEX_TENN: normalize_espn_odds(_dk_entry())}

    games, _ = _run_today(scoreboard, persisted)

    live = next(game for game in games if game["gameId"] == "401856704")
    assert live["gameStatus"] == 2
    assert live["homeMoneyline"] == 164


def test_game_with_no_fresh_or_persisted_line_is_dropped():
    scoreboard = _scoreboard()
    del _competition(scoreboard)["odds"]

    games, _ = _run_today(scoreboard, {})

    assert [game["gameId"] for game in games] == ["401858465"]


@pytest.mark.parametrize("with_stale_board", [False, True])
def test_today_returns_503_when_persisted_odds_read_fails(with_stale_board: bool):
    cache = _fresh_cache()
    if with_stale_board:
        cache.set("assembled", [{"gameId": "stale"}])
        cache._entries["assembled"]["last_fetch"] = 0

    def failed(*_args, **_kwargs):
        raise RuntimeError("Failed to load persisted odds")

    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ncaaf, "cache", cache),
        patch.object(ncaaf, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(ncaaf, "load_persisted_odds", failed),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(ncaaf.get_todays_games())

    assert error.value.status_code == 503
    assert error.value.detail == "NCAAF odds feed unavailable"


def test_today_returns_503_when_supabase_is_unconfigured():
    # The shared loader returns {} here; NCAAF must not read that as "no odds".
    with (
        patch.object(utils, "supabase", None),
        patch.object(ncaaf, "cache", _fresh_cache()),
        patch.object(ncaaf, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        pytest.raises(HTTPException) as error,
    ):
        asyncio.run(ncaaf.get_todays_games())

    assert error.value.status_code == 503
    assert error.value.detail == "NCAAF odds feed unavailable"


# --- standings --------------------------------------------------------------


def _stat(stat_type: str, value, display=None) -> dict:
    return {"name": stat_type, "type": stat_type, "value": value, "displayValue": display or str(value)}


def test_standings_walk_divisions_and_key_stats_by_type():
    entry = {
        "team": {"abbreviation": "APP"},
        "stats": [
            _stat("pointsfor", 90),
            _stat("pointsagainst", 30),
            _stat("streak", 0, "W3"),
            _stat("total", 0, "3-0"),
            # Split rows reuse the stat name; keying on name would pick these up.
            {"name": "pointsFor", "type": "vsaprankedteams_pointsfor", "value": 0},
        ],
    }
    payload = {"children": [{
        "isConference": True,
        "shortName": "Sun Belt",
        "children": [{"isConference": False, "standings": {"entries": [entry]}}],
    }]}

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return payload

    client = AsyncMock()
    client.get.return_value = Response()
    with patch.object(ncaaf, "get_http_client", return_value=client):
        standings = asyncio.run(ncaaf._standings_for_season(2026))

    assert standings["APP"] == {
        "conference": "Sun Belt",
        "streak": "W3",
        "pointsFor": 30.0,
        "pointsAgainst": 10.0,
    }


# --- cron -------------------------------------------------------------------


def test_cron_collects_the_calendar_date_and_reports_stats():
    async def fake_fetch(**kwargs):
        kwargs["collection_stats"].update({
            "pregame_snapshot_candidates": 2,
            "snapshots_inserted": 2,
            "snapshots_deduplicated": 0,
            "snapshot_persistence_ok": True,
            "latest_persistence_ok": True,
        })
        return {TEX_TENN: {}, ILL_OSU: {}}, 2

    fetch = AsyncMock(side_effect=fake_fetch)
    now = datetime(2026, 9, 27, 1, 30, tzinfo=ET)  # before the 6am slate rollover
    with (
        patch.object(ncaaf, "fetch_espn_odds", fetch),
        patch.object(ncaaf, "datetime", _frozen_at(now)),
    ):
        result = asyncio.run(ncaaf.cron_fetch_odds(offset=1))

    assert result["success"] is True
    assert result["fresh_from_api"] == 2
    assert result["snapshot_persistence_ok"] is True
    kwargs = fetch.await_args.kwargs
    assert kwargs["table_name"] == "ncaaf_odds"
    assert kwargs["scoreboard_params"] == {"groups": 80, "limit": 200}
    assert kwargs["target_date"] == date(2026, 9, 28)  # calendar date + 1
    assert result["date"] == "2026-09-28"


def test_cron_reports_failure_when_persisted_read_raises():
    with patch.object(
        espn_odds, "load_persisted_odds", side_effect=RuntimeError("supabase down")
    ):
        result = asyncio.run(ncaaf.cron_fetch_odds(offset=0))

    assert result["success"] is False
    assert "supabase down" in result["error"]


def test_fetch_espn_odds_persists_fresh_rows_and_snapshots():
    saved: dict = {}
    snapshots: list = []

    def save(odds_map, game_date=None, *, table_name):
        saved[(game_date, table_name)] = odds_map
        return True

    def save_snapshots(rows, *, stats=None):
        snapshots.extend(rows)
        stats.update({"inserted": len(rows), "deduplicated": 0, "persistence_ok": True})
        return len(rows)

    with (
        patch.object(espn_odds, "load_persisted_odds", return_value={}),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(espn_odds, "save_persisted_odds", save),
        patch.object(espn_odds, "save_odds_snapshots", save_snapshots),
        patch.object(espn_odds, "datetime", _frozen_at(PREGAME)),
    ):
        stats: dict = {}
        odds, fresh = asyncio.run(espn_odds.fetch_espn_odds(
            sport="ncaaf",
            scoreboard_url="unused",
            table_name="ncaaf_odds",
            target_date=SLATE,
            collection_stats=stats,
        ))

    assert fresh == 2
    assert set(saved[(SLATE, "ncaaf_odds")]) == {"TEX@TENN", "ILL@OSU"}
    assert {row["sport"] for row in snapshots} == {"ncaaf"}
    assert stats["snapshot_persistence_ok"] is True
    assert stats["latest_persistence_ok"] is True
    assert set(odds) == {TEX_TENN, ILL_OSU}


# --- latest-value persistence ----------------------------------------------


def _two_date_scoreboard() -> dict:
    scoreboard = _scoreboard()
    scoreboard["events"][1]["date"] = "2026-09-27T16:00Z"
    return scoreboard


def _collect(scoreboard: dict, save, save_snapshots=None) -> tuple[dict, dict]:
    def snapshots_ok(rows, *, stats=None):
        stats.update({"inserted": len(rows), "deduplicated": 0, "persistence_ok": True})
        return len(rows)

    stats: dict = {}
    with (
        patch.object(espn_odds, "load_persisted_odds", return_value={}),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=scoreboard)),
        patch.object(espn_odds, "save_persisted_odds", save),
        patch.object(espn_odds, "save_odds_snapshots", save_snapshots or snapshots_ok),
        patch.object(espn_odds, "datetime", _frozen_at(PREGAME)),
    ):
        odds, _ = asyncio.run(espn_odds.fetch_espn_odds(
            sport="ncaaf",
            scoreboard_url="unused",
            table_name="ncaaf_odds",
            target_date=SLATE,
            collection_stats=stats,
        ))
    return odds, stats


def test_first_date_write_failure_still_attempts_later_dates():
    # The failing write is the first date: a short-circuiting loop would skip
    # the second, which only the call list can distinguish.
    attempted: list[date] = []

    def save(odds_map, game_date=None, *, table_name):
        attempted.append(game_date)
        return game_date != SLATE

    _, stats = _collect(_two_date_scoreboard(), save)

    assert attempted == [SLATE, date(2026, 9, 27)]
    assert stats["latest_persistence_ok"] is False


def test_collection_with_no_fresh_dates_is_latest_persistence_ok():
    save = MagicMock()
    scoreboard = {"events": []}

    _, stats = _collect(scoreboard, save)

    save.assert_not_called()
    assert stats["latest_persistence_ok"] is True


def test_exception_before_final_assignment_leaves_latest_persistence_false():
    def save(odds_map, game_date=None, *, table_name):
        return True

    def snapshots_raise(rows, *, stats=None):
        raise RuntimeError("history write exploded")

    stats: dict = {}
    with (
        patch.object(espn_odds, "load_persisted_odds", return_value={}),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(espn_odds, "save_persisted_odds", save),
        patch.object(espn_odds, "save_odds_snapshots", snapshots_raise),
        patch.object(espn_odds, "datetime", _frozen_at(PREGAME)),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(espn_odds.fetch_espn_odds(
            sport="ncaaf",
            scoreboard_url="unused",
            table_name="ncaaf_odds",
            target_date=SLATE,
            collection_stats=stats,
        ))

    assert stats["latest_persistence_ok"] is False


def test_cron_fails_with_stats_when_latest_write_fails():
    async def fake_fetch(**kwargs):
        kwargs["collection_stats"].update({
            "pregame_snapshot_candidates": 2,
            "snapshots_inserted": 2,
            "snapshots_deduplicated": 0,
            "snapshot_persistence_ok": True,
            "latest_persistence_ok": False,
        })
        return {TEX_TENN: {}, ILL_OSU: {}}, 2

    with patch.object(ncaaf, "fetch_espn_odds", AsyncMock(side_effect=fake_fetch)):
        result = asyncio.run(ncaaf.cron_fetch_odds(offset=0))

    assert result["success"] is False
    assert "error" not in result
    assert result["latest_persistence_ok"] is False
    assert result["fresh_from_api"] == 2
    assert result["total_matchups"] == 2
    assert result["persisted_loaded"] == 0
    assert result["pregame_snapshot_candidates"] == 2
    assert result["snapshots_inserted"] == 2
    assert result["snapshots_deduplicated"] == 0
    assert result["snapshot_persistence_ok"] is True


def test_cron_without_supabase_reports_failure_for_nonempty_collection():
    # Real persistence helpers, no Supabase: nothing was written, so the
    # collector must not claim success.
    with (
        patch.object(utils, "supabase", None),
        patch.object(espn_odds, "fetch_espn_scoreboard", AsyncMock(return_value=_scoreboard())),
        patch.object(espn_odds, "datetime", _frozen_at(PREGAME)),
        patch.object(ncaaf, "datetime", _frozen_at(PREGAME)),
    ):
        result = asyncio.run(ncaaf.cron_fetch_odds(offset=0))

    assert result["success"] is False
    assert result["fresh_from_api"] == 2
    assert result["latest_persistence_ok"] is False
    assert result["snapshot_persistence_ok"] is False
