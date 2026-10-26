from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from main import app
from routers import nba_player_props as props
from routers import utils

# 2026-10-24 17:37 ET, a close tick for a 7:00 pm ET tip.
NOW = datetime(2026, 10, 24, 21, 37, tzinfo=utils.UTC)
TIP = datetime(2026, 10, 24, 23, 0, tzinfo=utils.UTC)
LINES = {
    "player_points": 20.5, "player_rebounds": 8.5, "player_assists": 4.5,
    "player_made_threes": 1.5, "player_points_+_rebounds_+_assists": 33.5,
    "player_points_+_rebounds": 29.5, "player_points_+_assists": 25.5,
    "player_rebounds_+_assists": 13.5,
}


def event(start=TIP, away="MIA", home="CHA", state="pre", name="STATUS_SCHEDULED"):
    return {"date": start.isoformat(), "competitions": [{
        "status": {"type": {"state": state, "name": name}},
        "competitors": [
            {"homeAway": "away", "team": {"abbreviation": away}},
            {"homeAway": "home", "team": {"abbreviation": home}},
        ],
    }]}


def quote(market="player_points", side="over", **changes):
    row = {
        "league": "nba", "market_type": market, "sportsbook": "draftkings",
        "home_team": "Charlotte Hornets", "away_team": "Miami Heat",
        "event_id": "nba_heat_hornets_2026-10-24_b3", "event_uuid": "book-uuid",
        "event_start_time": TIP.isoformat(),
        "selection_id": f"{market}-{side}", "selection": side.title(), "selection_type": side,
        "player_name": "Test Player", "stat_category": market.removeprefix("player_"),
        "line": LINES[market], "odds_american": -110, "odds_decimal": 1.909,
        "odds_probability": 0.5238, "timestamp": NOW.isoformat(),
        "is_live": False, "is_main_line": True, "is_alternate_line": False,
        "is_active": True, "is_stale_pregame_price": False,
    }
    row.update(changes)
    return row


def both_sides(market, **changes):
    return [quote(market, "over", **changes), quote(market, "under", **changes)]


@pytest.fixture
def collector(monkeypatch):
    events = [event()]

    class CollectionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(props, "datetime", CollectionClock)

    async def scoreboard(target):
        return {"events": [e for e in events
                           if props._timestamp(e["date"]).astimezone(utils.ET).date() == target]}, True

    async def fetch(league, market, *, sweep):
        sweep.pages_fetched += 1
        sweep.requests_made += 1
        return both_sides(market)

    monkeypatch.setattr(props, "_scoreboard_for_date_with_status", AsyncMock(side_effect=scoreboard))
    monkeypatch.setattr(props, "fetch_sharp_odds", AsyncMock(side_effect=fetch))
    persist = Mock(side_effect=lambda rows: len(rows))
    monkeypatch.setattr(props, "persist_props", persist)
    return events, persist


def collect(phase="close", now=NOW):
    return asyncio.run(props.collect_player_props("run-123-nba-props-" + phase, now, phase))


def persisted(collector):
    return collector[1].call_args.args[0]


def test_complete_sweep_has_one_atomic_write_and_shared_id(collector):
    result = collect()
    assert result["success"] and result["sweep_complete"]
    assert result["games_selected"] == result["games_persisted"] == 1
    assert result["markets_requested"] == result["markets_completed"] == result["pages_fetched"] == 8
    assert result["rows_inserted"] == 16
    collector[1].assert_called_once()
    rows = persisted(collector)
    assert {r["collection_id"] for r in rows} == {"run-123-nba-props-close"}
    assert {r["matchup_key"] for r in rows} == {"MIA@CHA"}
    assert {r["game_date"] for r in rows} == {"2026-10-24"}
    assert rows[0]["selection_name"] == "Test Player"
    assert rows[0]["selection_raw"] == "Over"
    assert result["book_rows"] == {"2026-10-24 MIA@CHA": {"draftkings": 16}}


def test_singles_have_no_combo_check_and_consistent_combos_pass(collector):
    result = collect()
    rows = persisted(collector)
    singles = [r for r in rows if r["market_type"] in props.SINGLE_MARKETS]
    combos = [r for r in rows if r["market_type"] in props.COMBO_COMPONENTS]
    assert {r["combo_check"] for r in singles} == {None}
    assert {r["combo_check"] for r in combos} == {"passed"}
    assert result["combo_checks"] == {"draftkings": {"passed": 8, "failed": 0, "unchecked": 0}}
    assert result["combo_failures"] == []


def run_checks(rows):
    return props.apply_combo_checks(rows)


def normalized(market, side="over", book="draftkings", player="Test Player", line=None, **extra):
    row = {
        "game_date": "2026-10-24", "matchup_key": "MIA@CHA", "bookmaker_id": book,
        "selection_name": player, "market_type": market, "side": side,
        "line": LINES[market] if line is None else line, "combo_check": None,
        "is_active": True, "is_stale_pregame_price": False,
    }
    row.update(extra)
    return row


def components(book="draftkings", player="Test Player", **lines):
    rows = []
    for market in ("player_points", "player_rebounds", "player_assists"):
        for side in ("over", "under"):
            rows.append(normalized(market, side, book, player, lines.get(market)))
    return rows


def test_mislabeled_combo_fails_but_is_kept_with_example():
    # The WNBA Finals case: an R+A row carrying the PRA line.
    combo = normalized("player_rebounds_+_assists", line=33.5)
    report = run_checks(components() + [combo])
    assert combo["combo_check"] == "failed"
    assert report["combo_checks"]["draftkings"]["failed"] == 1
    assert report["combo_failures"] == [{
        "game_date": "2026-10-24", "matchup_key": "MIA@CHA", "bookmaker_id": "draftkings",
        "selection_name": "Test Player", "market_type": "player_rebounds_+_assists",
        "side": "over", "line": 33.5, "component_sum": 13.0,
    }]


@pytest.mark.parametrize("line,outcome", [(16.0, "passed"), (16.5, "failed"), (10.0, "passed"), (9.5, "failed")])
def test_tolerance_is_three_either_way(line, outcome):
    combo = normalized("player_rebounds_+_assists", line=line)
    run_checks(components() + [combo])
    assert combo["combo_check"] == outcome


def test_missing_component_leaves_combo_unchecked():
    rows = [r for r in components() if r["market_type"] != "player_assists"]
    combo = normalized("player_rebounds_+_assists")
    report = run_checks(rows + [combo])
    assert combo["combo_check"] == "unchecked"
    assert report["combo_checks"]["draftkings"] == {"passed": 0, "failed": 0, "unchecked": 1}


def test_over_and_under_disagreeing_makes_component_ambiguous():
    rows = components()
    next(r for r in rows if r["market_type"] == "player_rebounds" and r["side"] == "under")["line"] = 9.5
    combo = normalized("player_rebounds_+_assists")
    run_checks(rows + [combo])
    assert combo["combo_check"] == "unchecked"


def test_line_moving_mid_sweep_makes_component_ambiguous():
    rows = components() + [normalized("player_assists", "over", line=5.5)]
    combo = normalized("player_points_+_assists")
    run_checks(rows + [combo])
    assert combo["combo_check"] == "unchecked"


def test_stale_or_pulled_components_do_not_count():
    rows = components()
    for row in rows:
        if row["market_type"] == "player_assists":
            row["is_stale_pregame_price"] = True
    combo = normalized("player_rebounds_+_assists")
    run_checks(rows + [combo])
    assert combo["combo_check"] == "unchecked"


def test_components_never_cross_books_players_or_games():
    fanduel_combo = normalized("player_rebounds_+_assists", book="fanduel")
    other_player = normalized("player_rebounds_+_assists", player="Other Player")
    other_game = normalized("player_rebounds_+_assists", matchup_key="BKN@CLE")
    report = run_checks(components() + [fanduel_combo, other_player, other_game])
    assert fanduel_combo["combo_check"] == other_player["combo_check"] == other_game["combo_check"] == "unchecked"
    assert report["combo_checks"]["fanduel"]["unchecked"] == 1


def test_each_combo_side_is_checked_on_its_own():
    over = normalized("player_rebounds_+_assists", "over")
    under = normalized("player_rebounds_+_assists", "under", line=33.5)
    run_checks(components() + [over, under])
    assert (over["combo_check"], under["combo_check"]) == ("passed", "failed")


def test_failed_combo_is_persisted_and_does_not_fail_the_sweep(collector, monkeypatch):
    async def fetch(league, market, *, sweep):
        if market == "player_rebounds_+_assists":
            return both_sides(market, line=33.5)
        return both_sides(market)

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["rows_rejected"] == 0
    assert result["combo_checks"]["draftkings"] == {"passed": 6, "failed": 2, "unchecked": 0}
    assert len(result["combo_failures"]) == 2
    failed = [r for r in persisted(collector) if r["combo_check"] == "failed"]
    assert {r["market_type"] for r in failed} == {"player_rebounds_+_assists"}


def test_missing_book_is_not_a_failure(collector, monkeypatch):
    async def fetch(league, market, *, sweep):
        rows = both_sides(market)
        if market == "player_points":
            rows += both_sides(market, sportsbook="fanduel", selection_id=f"{market}-fd")
        return rows

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"]
    assert result["book_rows"] == {"2026-10-24 MIA@CHA": {"draftkings": 16, "fanduel": 2}}
    assert "fanduel" not in result["combo_checks"]


@pytest.mark.parametrize("changes", [
    {"selection_id": None}, {"selection_id": " "}, {"home_team": "Mystery Team"},
    {"is_live": True}, {"is_main_line": False}, {"is_alternate_line": True},
    {"is_active": None}, {"is_stale_pregame_price": None},
    {"line": None}, {"line": float("nan")}, {"selection_type": "other"},
    {"odds_american": -110.5}, {"odds_american": 0}, {"player_name": None},
    {"timestamp": "2026-10-24T12:00:00"}, {"timestamp": TIP.isoformat()},
    {"market_type": "player_steals"}, {"league": "wnba"}, {"sportsbook": "betmgm"},
])
def test_one_bad_row_prevents_entire_insert(collector, monkeypatch, changes):
    async def fetch(league, market, *, sweep):
        if market == "player_points":
            return [quote(market, **changes), quote(market, "under")]
        return both_sides(market)

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert not result["success"] and not result["sweep_complete"]
    assert result["rows_rejected"] == 1
    collector[1].assert_not_called()
    if "home_team" in changes:
        assert result["unmapped_teams"] == ["Mystery Team"]


def test_page_failure_after_valid_markets_does_not_write(collector, monkeypatch):
    monkeypatch.setattr(props, "fetch_sharp_odds", AsyncMock(side_effect=[
        both_sides("player_points"), ValueError("missing cursor"),
    ]))
    result = collect()
    assert not result["sweep_complete"]
    assert result["markets_requested"] == 2 and result["markets_completed"] == 1
    collector[1].assert_not_called()


@pytest.mark.parametrize("espn,provider_away,matchup", [
    ("GS", "Golden State Warriors", "GSW@CHA"),
    ("SA", "San Antonio Spurs", "SAS@CHA"),
    ("WSH", "Washington Wizards", "WAS@CHA"),
])
def test_espn_abbreviations_and_provider_names_meet_on_nba_tricodes(collector, monkeypatch, espn, provider_away, matchup):
    collector[0][:] = [event(away=espn)]

    async def fetch(league, market, *, sweep):
        return both_sides(market, away_team=provider_away)

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"]
    assert {r["matchup_key"] for r in persisted(collector)} == {matchup}


def test_fanduel_phoenix_name_maps(collector, monkeypatch):
    collector[0][:] = [event(away="PHX")]

    async def fetch(league, market, *, sweep):
        return both_sides(market, away_team="PHO Suns")

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    assert collect()["success"]
    assert {r["matchup_key"] for r in persisted(collector)} == {"PHX@CHA"}


def test_unknown_espn_team_is_a_schedule_failure(collector):
    collector[0][:] = [event(away="XYZ")]
    result = collect()
    assert not result["success"] and not result["schedule_ok"]
    props.fetch_sharp_odds.assert_not_called()


@pytest.mark.parametrize("minutes,selected", [(9, 0), (10, 1), (90, 1), (91, 0), (-1, 0)])
def test_close_window_boundaries(collector, minutes, selected):
    collector[0][:] = [event(NOW + timedelta(minutes=minutes))]
    assert collect()["games_selected"] == selected


def test_intraday_selects_todays_whole_slate(collector):
    morning = datetime(2026, 10, 24, 15, 37, tzinfo=utils.UTC)  # 11:37 ET
    collector[0][:] = [
        event(TIP),
        event(TIP + timedelta(hours=3), "GS", "PHX"),
        event(TIP + timedelta(days=1), "BKN", "CLE"),  # tomorrow: not today's slate
        event(morning + timedelta(minutes=5), "NY", "BOS"),  # inside the guard
        event(TIP, "SA", "DAL", state="post", name="STATUS_FINAL"),
    ]
    result = collect("intraday", morning)
    assert result["phase"] == "intraday"
    assert result["games_selected"] == 2
    assert collect("close", morning)["games_selected"] == 0


def test_postponed_games_are_never_selected(collector):
    collector[0][:] = [event(name="STATUS_POSTPONED")]
    assert collect()["games_selected"] == 0
    assert collect("intraday")["games_selected"] == 0


def test_close_window_crossing_et_midnight_reads_both_dates(collector):
    late = datetime(2026, 10, 25, 3, 37, tzinfo=utils.UTC)  # 23:37 ET
    collector[0][:] = [event(late + timedelta(minutes=40), "GS", "PHX")]  # 00:17 ET next day
    result = collect(now=late)
    assert result["games_selected"] == 1
    queried = {call.args[0] for call in props._scoreboard_for_date_with_status.await_args_list}
    assert {d.isoformat() for d in queried} >= {"2026-10-24", "2026-10-25"}


NEXT_NIGHT = TIP + timedelta(days=1)


def back_to_back_fetch(*nights):
    async def fetch(league, market, *, sweep):
        rows = []
        for start, event_id in nights:
            rows += both_sides(market, event_start_time=start.isoformat(), event_id=event_id,
                               selection_id=f"{market}-{event_id}")
        return [{**row, "selection_id": f"{row['selection_id']}-{row['selection_type']}"} for row in rows]
    return fetch


def test_back_to_back_same_matchup_resolves_each_night(collector, monkeypatch):
    collector[0].append(event(NEXT_NIGHT))
    monkeypatch.setattr(props, "fetch_sharp_odds", back_to_back_fetch(
        (TIP, "nba_heat_hornets_2026-10-24_b3"), (NEXT_NIGHT, "nba_heat_hornets_2026-10-25_b3")))
    result = collect()
    assert result["success"] and result["rows_rejected"] == 0
    assert result["games_selected"] == 1 and result["games_persisted"] == 2
    by_event = {r["provider_event_id"]: r["game_date"] for r in persisted(collector)}
    assert by_event == {"nba_heat_hornets_2026-10-24_b3": "2026-10-24",
                        "nba_heat_hornets_2026-10-25_b3": "2026-10-25"}
    assert {r["starts_at"] for r in persisted(collector)} == {TIP.isoformat(), NEXT_NIGHT.isoformat()}


@pytest.mark.parametrize("start,event_id", [
    (TIP + timedelta(hours=7), "nba_heat_hornets_b3"),  # outside both nights' bound
    (NEXT_NIGHT, "nba_heat_hornets_2026-10-24_b3"),  # start a day off; id disagrees
])
def test_back_to_back_never_guesses_the_night(collector, monkeypatch, start, event_id):
    collector[0].append(event(NEXT_NIGHT))
    monkeypatch.setattr(props, "fetch_sharp_odds", back_to_back_fetch((start, event_id)))
    result = collect()
    assert not result["success"] and result["rows_rejected"] == 16
    collector[1].assert_not_called()


def test_back_to_back_without_id_date_uses_nearest_start(collector, monkeypatch):
    collector[0].append(event(NEXT_NIGHT))
    monkeypatch.setattr(props, "fetch_sharp_odds", back_to_back_fetch(
        (TIP + timedelta(minutes=30), "nba_heat_hornets_b3")))
    result = collect()
    assert result["success"]
    assert {r["game_date"] for r in persisted(collector)} == {"2026-10-24"}


def test_zero_games_is_success_without_provider_request(collector):
    collector[0].clear()
    result = collect()
    assert result["success"] and result["schedule_ok"] and result["sweep_complete"]
    props.fetch_sharp_odds.assert_not_called()
    collector[1].assert_not_called()


@pytest.mark.parametrize("payload,ok", [({}, False), ({}, True), ({"events": [{}]}, True)])
def test_schedule_failure_is_not_a_noop(collector, monkeypatch, payload, ok):
    monkeypatch.setattr(props, "_scoreboard_for_date_with_status", AsyncMock(return_value=(payload, ok)))
    result = collect()
    assert not result["success"] and not result["schedule_ok"]
    props.fetch_sharp_odds.assert_not_called()


def test_archives_games_outside_trigger_window(collector, monkeypatch):
    later = TIP + timedelta(days=1)
    collector[0].append(event(later, "BKN", "CLE"))

    async def fetch(league, market, *, sweep):
        return both_sides(market) + both_sides(
            market, away_team="Brooklyn Nets", home_team="Cleveland Cavaliers",
            event_start_time=later.isoformat(), selection_id=f"{market}-later")

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["games_selected"] == 1 and result["games_persisted"] == 2


def test_bad_rows_for_unselected_games_are_skipped(collector, monkeypatch):
    later = TIP + timedelta(days=1)
    collector[0].append(event(later, "BKN", "CLE"))

    async def fetch(league, market, *, sweep):
        bad_future = quote(market, away_team="Brooklyn Nets", home_team="Cleveland Cavaliers",
                           event_start_time=later.isoformat(), selection_id=None)
        return both_sides(market) + ([bad_future] if market == "player_points" else [])

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["rows_skipped"] == 1 and result["rows_rejected"] == 0


def test_delayed_workflow_fails_before_provider_request(collector):
    collector[0][:] = [event(NOW + timedelta(minutes=9))]
    result = asyncio.run(props.collect_player_props("late-run", NOW - timedelta(minutes=5)))
    assert result["games_selected"] == 1 and not result["success"]
    props.fetch_sharp_odds.assert_not_called()


def test_persistence_failure_and_replay_counts(collector):
    collector[1].side_effect = ValueError("database unavailable")
    result = collect()
    assert result["sweep_complete"] and not result["success"] and not result["persistence_ok"]
    collector[1].side_effect = lambda rows: 0
    assert collect()["rows_deduplicated"] == 16


def test_rpc_uses_one_call_for_large_collection(monkeypatch):
    database = Mock()
    database.rpc.return_value.execute.return_value = SimpleNamespace(data=2300)
    monkeypatch.setattr(utils, "supabase", database)
    rows = [quote()] * 2300
    assert props.persist_props(rows) == 2300
    database.rpc.assert_called_once_with("append_nba_player_prop_odds", {"observations": rows})


def test_endpoint_passes_phase_and_window(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    collector = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(props, "collect_player_props", collector)
    monkeypatch.setattr(props, "_collection_lock", asyncio.Lock())
    client = TestClient(app)
    response = client.post("/api/nba/cron/fetch-player-props",
                           params={"collection_key": "run-1", "window_at": NOW.isoformat(), "phase": "intraday"},
                           headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 200
    assert collector.call_args.args == ("run-1", NOW, "intraday")
    for params in ({"collection_key": "run", "phase": "morning"},
                   {"collection_key": "run", "window_at": "2026-10-24T21:37:00"},
                   {}):
        response = client.post("/api/nba/cron/fetch-player-props", params=params,
                               headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == 422


def test_endpoint_requires_cron_auth():
    response = TestClient(app).post("/api/nba/cron/fetch-player-props", params={"collection_key": "run"})
    assert response.status_code in (401, 403, 503)


def test_endpoint_timeout_reports_failure(monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(props, "collect_player_props", slow)
    monkeypatch.setattr(props, "SWEEP_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(props, "_collection_lock", asyncio.Lock())
    result = asyncio.run(props.cron_fetch_player_props("run-timeout"))
    assert not result["success"] and not result["sweep_complete"]


def test_overlapping_sweep_fails_without_starting_another(monkeypatch):
    async def run():
        lock = asyncio.Lock()
        monkeypatch.setattr(props, "_collection_lock", lock)
        collector = AsyncMock()
        monkeypatch.setattr(props, "collect_player_props", collector)
        async with lock:
            result = await props.cron_fetch_player_props("overlap")
        assert not result["success"]
        collector.assert_not_called()

    asyncio.run(run())


YESTERDAY = TIP.astimezone(utils.ET).date()


def cell(matchup="MIA@CHA", book="draftkings", market="player_points", usable=4, unchecked=0, failed=0):
    return {"matchup_key": matchup, "bookmaker_id": book, "market_type": market,
            "usable_rows": usable, "usable_players": usable // 2, "unchecked_rows": unchecked,
            "failed_rows": failed}


def full_game(matchup):
    return [cell(matchup, book, market) for book in props.BOOKS for market in props.MARKETS]


@pytest.fixture
def coverage(monkeypatch):
    """Yesterday's slate: two played games and a postponed one."""
    events = [
        event(TIP, "MIA", "CHA", state="post", name="STATUS_FINAL"),
        event(TIP + timedelta(hours=1), "NY", "BOS", state="post", name="STATUS_FINAL"),
        event(TIP, "GS", "PHX", state="post", name="STATUS_POSTPONED"),
    ]
    cells = []

    async def scoreboard(target):
        return {"events": [e for e in events
                           if props._timestamp(e["date"]).astimezone(utils.ET).date() == target]}, True

    monkeypatch.setattr(props, "_scoreboard_for_date_with_status", AsyncMock(side_effect=scoreboard))
    monkeypatch.setattr(props, "close_coverage_cells", Mock(side_effect=lambda target: cells))
    return events, cells


def test_coverage_reports_book_by_market_gaps_per_game(coverage):
    _, cells = coverage
    cells += full_game("MIA@CHA")
    cells += [c for c in full_game("NYK@BOS") if c["bookmaker_id"] == "draftkings"]
    result = asyncio.run(props.close_coverage(YESTERDAY))
    assert result["success"] and result["schedule_ok"]
    assert result["games"] == 2 and result["cells_expected"] == 32 and result["cells_covered"] == 24
    assert result["matrix"]["draftkings"] == dict.fromkeys(props.MARKETS, 2)
    assert result["matrix"]["fanduel"] == dict.fromkeys(props.MARKETS, 1)
    [gap] = result["game_gaps"]
    assert gap["matchup_key"] == "NYK@BOS"
    assert gap["missing"] == [f"fanduel {market}" for market in props.MARKETS]


def test_unchecked_and_failed_combos_never_count_as_covered(coverage):
    _, cells = coverage
    cells += [c for c in full_game("MIA@CHA") + full_game("NYK@BOS")
              if not (c["matchup_key"] == "MIA@CHA" and c["bookmaker_id"] == "fanduel"
                      and c["market_type"].count("+"))]
    cells += [cell("MIA@CHA", "fanduel", "player_points_+_rebounds", usable=0, unchecked=6),
              cell("MIA@CHA", "fanduel", "player_points_+_assists", usable=0, failed=2)]
    result = asyncio.run(props.close_coverage(YESTERDAY))
    [gap] = result["game_gaps"]
    assert gap["unchecked_only"] == ["fanduel player_points_+_rebounds"]
    assert gap["missing"] == ["fanduel player_points_+_rebounds_+_assists", "fanduel player_points_+_assists",
                              "fanduel player_rebounds_+_assists"]
    assert gap["failed_rows"] == 2
    assert result["cells_covered"] == 28


def test_called_off_games_are_not_expected_and_their_rows_are_flagged(coverage):
    _, cells = coverage
    cells += full_game("MIA@CHA") + full_game("NYK@BOS") + [cell("GSW@PHX")]
    result = asyncio.run(props.close_coverage(YESTERDAY))
    assert result["games"] == 2 and result["game_gaps"] == []
    assert result["unexpected_matchups"] == ["GSW@PHX"]


def test_coverage_with_no_games_is_success(coverage):
    events, _ = coverage
    events.clear()
    result = asyncio.run(props.close_coverage(YESTERDAY))
    assert result["success"] and result["games"] == 0 and result["cells_expected"] == 0


def test_coverage_fails_without_schedule_or_query(coverage, monkeypatch):
    props._scoreboard_for_date_with_status.side_effect = None
    props._scoreboard_for_date_with_status.return_value = ({}, False)
    result = asyncio.run(props.close_coverage(YESTERDAY))
    assert not result["success"] and not result["schedule_ok"]

    events, _ = coverage
    props._scoreboard_for_date_with_status.return_value = ({"events": events}, True)
    monkeypatch.setattr(props, "close_coverage_cells", Mock(side_effect=ValueError("database unavailable")))
    result = asyncio.run(props.close_coverage(YESTERDAY))
    assert result["schedule_ok"] and not result["success"] and "database unavailable" in result["error"]


def test_coverage_query_is_one_rpc_by_date(monkeypatch):
    database = Mock()
    database.rpc.return_value.execute.return_value = SimpleNamespace(data=[cell()])
    monkeypatch.setattr(utils, "supabase", database)
    assert props.close_coverage_cells(YESTERDAY) == [cell()]
    database.rpc.assert_called_once_with("nba_player_prop_close_coverage", {"target": "2026-10-24"})
    database.rpc.return_value.execute.return_value = SimpleNamespace(data=None)
    with pytest.raises(ValueError):
        props.close_coverage_cells(YESTERDAY)


def test_coverage_endpoint_defaults_to_yesterday_and_rejects_today(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 25, 3, 30, tzinfo=utils.UTC).astimezone(tz)  # 23:30 ET on the 24th

    monkeypatch.setattr(props, "datetime", Clock)
    reporter = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(props, "close_coverage", reporter)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-secret"}
    assert client.post("/api/nba/cron/player-props-coverage", headers=headers).status_code == 200
    assert reporter.call_args.args == (datetime(2026, 10, 23).date(),)
    response = client.post("/api/nba/cron/player-props-coverage", params={"date": "2026-10-22"}, headers=headers)
    assert response.status_code == 200 and reporter.call_args.args == (datetime(2026, 10, 22).date(),)
    assert client.post("/api/nba/cron/player-props-coverage", params={"date": "2026-10-24"},
                       headers=headers).status_code == 422
    assert client.post("/api/nba/cron/player-props-coverage").status_code == 401
