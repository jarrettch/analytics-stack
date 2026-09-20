from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from main import app
from routers import nfl, utils
from routers import nfl_player_props as props

NOW = datetime(2026, 9, 20, 16, tzinfo=utils.UTC)
KICKOFF = NOW + timedelta(hours=1)


def event(start=KICKOFF, away="MIN", home="CHI"):
    return {"date": start.isoformat(), "competitions": [{
        "status": {"type": {"state": "pre", "name": "STATUS_SCHEDULED"}},
        "competitors": [
            {"homeAway": "away", "team": {"abbreviation": away}},
            {"homeAway": "home", "team": {"abbreviation": home}},
        ],
    }]}


def quote(market="player_receptions", **changes):
    row = {
        "league": "nfl", "market_type": market, "sportsbook": "draftkings",
        "home_team": "Chicago Bears", "away_team": "Minnesota Vikings",
        "event_id": "nfl_bears_vikings_2026-09-20_b2", "event_uuid": "book-uuid",
        "event_start_time": KICKOFF.isoformat(), "selection_id": market + "-selection",
        "selection": "Over", "selection_type": "over", "player_name": "Test Player",
        "line": 4.5, "odds_american": -110, "odds_decimal": 1.909,
        "odds_probability": 0.5238, "timestamp": NOW.isoformat(),
        "is_live": False, "is_main_line": True, "is_alternate_line": False,
        "is_active": True, "is_stale_pregame_price": False,
    }
    if market == "anytime_touchdown_scorer":
        row.update(line=None, selection="MIN Vikings D/ST", selection_type="away",
                   player_name="Anytime TD", is_player_prop=False)
    row.update(changes)
    return row


@pytest.fixture
def collector(monkeypatch):
    events = [event()]

    class CollectionClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(props, "datetime", CollectionClock)

    async def scoreboard(target):
        return {"events": [e for e in events if props._timestamp(e["date"]).astimezone(utils.ET).date() == target]}, True

    async def fetch(league, market, *, sweep):
        sweep.pages_fetched += 1
        sweep.requests_made += 1
        return [quote(market)]

    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status", AsyncMock(side_effect=scoreboard))
    monkeypatch.setattr(props, "fetch_sharp_odds", AsyncMock(side_effect=fetch))
    persist = Mock(side_effect=lambda rows: len(rows))
    monkeypatch.setattr(props, "persist_props", persist)
    return events, persist


def collect():
    return asyncio.run(props.collect_player_props("run-123-nfl", NOW))


def test_complete_sweep_has_one_atomic_write_and_shared_id(collector):
    result = collect()
    assert result["success"]
    assert result["games_selected"] == result["games_persisted"] == 1
    assert result["rows_inserted"] == result["markets_requested"] == result["pages_fetched"] == 5
    assert result["markets_completed"] == 5
    collector[1].assert_called_once()
    rows = collector[1].call_args.args[0]
    assert {r["collection_id"] for r in rows} == {"run-123-nfl"}
    assert {r["matchup_key"] for r in rows} == {"MIN@CHI"}
    assert rows[-1]["selection_name"] == "MIN Vikings D/ST"
    assert rows[-1]["provider_player_name"] == "Anytime TD"
    assert rows[-1]["side"] is rows[-1]["line"] is None
    assert rows[0]["selection_name"] == "Test Player"
    assert rows[0]["implied_probability"] == 0.5238


@pytest.mark.parametrize("selection,player,side", [
    ("Test Player", "Test Player", "other"),
    ("Chicago Defense", "Chicago Defense", "other"),
    ("CHI Bears D/ST", "Anytime TD", "home"),
    ("MIN Vikings D/ST", "Anytime TD", "away"),
])
def test_scorer_names_and_sides(collector, selection, player, side):
    row = quote("anytime_touchdown_scorer", selection=selection, player_name=player, selection_type=side)
    normalized = asyncio.run(props.normalize_prop(row, row["market_type"], props.NFLSchedule(), "run", NOW, set()))
    assert normalized["selection_name"] == selection
    assert normalized["side"] is normalized["line"] is None


@pytest.mark.parametrize("changes", [
    {"selection_id": None}, {"selection_id": " "}, {"home_team": "Mystery Team"},
    {"is_live": True}, {"is_main_line": False}, {"is_alternate_line": True},
    {"is_active": None}, {"is_stale_pregame_price": None},
    {"line": None}, {"line": float("nan")}, {"selection_type": "other"},
    {"odds_american": -110.5}, {"odds_american": 0}, {"player_name": None},
    {"timestamp": "2026-09-20T12:00:00"}, {"timestamp": KICKOFF.isoformat()},
    {"market_type": "moneyline"}, {"league": "ncaaf"},
])
def test_one_bad_row_prevents_entire_insert(collector, monkeypatch, changes):
    async def fetch(league, market, *, sweep):
        return [quote(market, **changes)] if market == "player_receptions" else [quote(market)]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert not result["success"]
    assert not result["sweep_complete"]
    assert result["rows_rejected"] == 1
    collector[1].assert_not_called()
    if "home_team" in changes:
        assert result["unmapped_teams"] == ["Mystery Team"]


def test_page_failure_after_valid_markets_does_not_write(collector, monkeypatch):
    monkeypatch.setattr(props, "fetch_sharp_odds", AsyncMock(side_effect=[
        [quote()], ValueError("missing cursor"),
    ]))
    result = collect()
    assert not result["sweep_complete"]
    assert result["markets_requested"] == 2
    assert result["markets_completed"] == 1
    collector[1].assert_not_called()


def test_schedule_outage_during_identity_resolution_prevents_insert(collector, monkeypatch):
    scoreboard = nfl._scoreboard_for_date_with_status

    async def partial_schedule(target):
        if target == NOW.astimezone(utils.ET).date():
            return await scoreboard(target)
        return {}, False

    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status", partial_schedule)
    result = collect()
    assert not result["success"] and not result["schedule_ok"]
    assert not result["sweep_complete"]
    collector[1].assert_not_called()


def test_ambiguous_schedule_identity_prevents_insert(collector):
    collector[0].append(event(KICKOFF + timedelta(days=1)))
    result = collect()
    assert not result["success"] and result["rows_rejected"] == 5
    collector[1].assert_not_called()


def test_zero_games_is_success_without_provider_request(collector):
    collector[0].clear()
    result = collect()
    assert result["success"] and result["schedule_ok"] and result["sweep_complete"]
    assert result["games_selected"] == 0
    props.fetch_sharp_odds.assert_not_called()
    collector[1].assert_not_called()


@pytest.mark.parametrize("payload,ok", [({}, False), ({}, True), ({"events": [{}]}, True)])
def test_schedule_failure_is_not_a_noop(collector, monkeypatch, payload, ok):
    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status", AsyncMock(return_value=(payload, ok)))
    result = collect()
    assert not result["success"] and not result["schedule_ok"]
    props.fetch_sharp_odds.assert_not_called()
    collector[1].assert_not_called()


@pytest.mark.parametrize("minutes,selected", [(9, 0), (10, 1), (90, 1), (91, 0), (-1, 0)])
def test_eligibility_window_boundaries(collector, minutes, selected):
    collector[0][:] = [event(NOW + timedelta(minutes=minutes))]
    result = collect()
    assert result["games_selected"] == selected


@pytest.mark.parametrize("start", [
    datetime(2026, 11, 27, 20, tzinfo=utils.UTC),  # Black Friday
    datetime(2026, 12, 25, 18, tzinfo=utils.UTC),  # Christmas
    datetime(2027, 1, 16, 21, tzinfo=utils.UTC),  # Saturday playoffs
    datetime(2026, 10, 11, 13, 30, tzinfo=utils.UTC),  # London
    datetime(2026, 9, 21, 4, 30, tzinfo=utils.UTC),  # crosses ET midnight
])
def test_window_is_schedule_driven_on_any_day(collector, start):
    collector[0][:] = [event(start)]
    selected = asyncio.run(props.NFLSchedule().selected(start - timedelta(hours=1)))
    assert len(selected) == 1
    assert selected[0].starts_at == start


def test_both_book_dates_resolve_to_same_espn_date(collector):
    async def normalize():
        schedule = props.NFLSchedule()
        rows = []
        for book, delta in (("draftkings", 0), ("fanduel", 1)):
            row = quote(sportsbook=book, event_id=f"book-{book}",
                        event_start_time=(KICKOFF + timedelta(days=delta)).isoformat())
            rows.append(await props.normalize_prop(row, row["market_type"], schedule, "run", NOW, set()))
        return rows

    first, second = asyncio.run(normalize())
    assert first["game_date"] == second["game_date"] == "2026-09-20"
    assert first["starts_at"] == second["starts_at"]
    assert first["provider_event_id"] != second["provider_event_id"]


def test_archives_games_outside_trigger_window(collector, monkeypatch):
    later = KICKOFF + timedelta(days=4)
    collector[0].append(event(later, "BUF", "NYJ"))

    async def fetch(league, market, *, sweep):
        return [quote(market), quote(market, away_team="Buffalo Bills", home_team="New York Jets",
                                    event_start_time=later.isoformat(), selection_id=market + "-later")]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"]
    assert result["games_selected"] == 1
    assert result["games_persisted"] == 2


@pytest.mark.parametrize("changes", [
    {"selection_id": None}, {"line": None}, {"odds_american": 0},
    {"event_start_time": "invalid"}, {"is_live": True},
])
def test_bad_future_rows_do_not_erase_selected_close(collector, monkeypatch, changes):
    later = KICKOFF + timedelta(days=5)
    collector[0].append(event(later, "BUF", "NYJ"))

    async def fetch(league, market, *, sweep):
        good_future = quote(market, away_team="Buffalo Bills", home_team="New York Jets",
                            event_start_time=later.isoformat(), selection_id=market + "-future")
        bad_future = {**good_future, "selection_id": market + "-bad", **changes}
        return [quote(market), good_future, bad_future] if market == "player_receptions" else [quote(market)]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["sweep_complete"]
    assert result["rows_skipped"] == 1 and result["rows_rejected"] == 0
    assert result["rows_inserted"] == 6 and result["games_persisted"] == 2


def test_earlier_game_post_kickoff_rows_do_not_erase_later_close(collector, monkeypatch):
    early = NOW - timedelta(hours=3)
    early_event = event(early, "BUF", "NYJ")
    early_event["competitions"][0]["status"]["type"]["state"] = "in"
    collector[0].append(early_event)

    async def fetch(league, market, *, sweep):
        return [quote(market), quote(market, away_team="Buffalo Bills", home_team="New York Jets",
                                    event_start_time=early.isoformat(), selection_id=market + "-early",
                                    timestamp=(early + timedelta(hours=2)).isoformat())]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["games_selected"] == 1
    assert result["rows_skipped"] == 5 and result["rows_rejected"] == 0
    assert result["rows_inserted"] == 5 and result["games_persisted"] == 1
    assert all(r["matchup_key"] == "MIN@CHI" for r in collector[1].call_args.args[0])


def test_unknown_matchup_cannot_be_skipped_based_on_provider_date(collector, monkeypatch):
    async def fetch(league, market, *, sweep):
        return [quote(market), quote(market, away_team="Unmapped Team",
                                    event_start_time=(KICKOFF + timedelta(days=5)).isoformat())]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert not result["success"]
    assert result["rows_rejected"] == 5 and result["rows_skipped"] == 0
    assert result["unmapped_teams"] == ["Unmapped Team"]
    collector[1].assert_not_called()


@pytest.mark.parametrize("failure", [({}, False), ({}, True), ({"events": [{}]}, True)])
@pytest.mark.parametrize("failed_offset", [-1, 0, 1])
def test_incidental_schedule_failure_is_fetched_once_per_date(collector, monkeypatch, failure, failed_offset):
    later = KICKOFF + timedelta(days=5)
    failed_date = (later + timedelta(days=failed_offset)).date()
    scoreboard = nfl._scoreboard_for_date_with_status

    async def partial_schedule(target):
        if target == failed_date:
            return failure
        return await scoreboard(target)

    async def fetch(league, market, *, sweep):
        return [quote(market)] + [quote(
            market, away_team="Buffalo Bills", home_team="New York Jets",
            event_start_time=later.isoformat(), selection_id=f"future-{market}-{index}",
        ) for index in range(100)]

    lookup = AsyncMock(side_effect=partial_schedule)
    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status", lookup)
    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    result = collect()
    assert result["success"] and result["schedule_ok"]
    assert result["rows_skipped"] == 500 and result["rows_inserted"] == 5
    queried_dates = [call.args[0] for call in lookup.await_args_list]
    assert queried_dates.count(failed_date) == 1
    assert len(queried_dates) == len(set(queried_dates))


def test_failed_schedule_cache_expires_with_sweep(monkeypatch):
    target = NOW.date()
    lookup = AsyncMock(side_effect=[({}, False), ({"events": [event()]}, True)])
    monkeypatch.setattr(nfl, "_scoreboard_for_date_with_status", lookup)

    async def run():
        schedule = props.NFLSchedule()
        for _ in range(2):
            with pytest.raises(props.ScheduleError, match="unavailable"):
                await schedule.day(target)
        assert lookup.await_count == 1
        recovered = await props.NFLSchedule().day(target)
        assert len(recovered) == 1
        assert lookup.await_count == 2

    asyncio.run(run())


def test_pulled_and_stale_prices_are_archived_with_flags(collector, monkeypatch):
    async def fetch(league, market, *, sweep):
        return [quote(market, is_active=False, is_stale_pregame_price=True)]

    monkeypatch.setattr(props, "fetch_sharp_odds", fetch)
    assert collect()["success"]
    assert all(not r["is_active"] and r["is_stale_pregame_price"] for r in collector[1].call_args.args[0])


def test_persistence_failure_and_replay_counts(collector):
    collector[1].side_effect = ValueError("database unavailable")
    result = collect()
    assert result["sweep_complete"]
    assert not result["success"] and not result["persistence_ok"]
    collector[1].side_effect = lambda rows: 0
    result = collect()
    assert result["success"] and result["rows_deduplicated"] == 5


def test_rpc_uses_one_call_for_large_collection(monkeypatch):
    database = Mock()
    database.rpc.return_value.execute.return_value = SimpleNamespace(data=2300)
    monkeypatch.setattr(utils, "supabase", database)
    rows = [quote()] * 2300
    assert props.persist_props(rows) == 2300
    database.rpc.assert_called_once_with("append_nfl_player_prop_odds", {"observations": rows})


def test_team_name_helper_preserves_main_line_behavior():
    assert nfl.get_tricode_from_team_name(" Chicago Bears_NFL ") == "CHI"
    assert nfl.get_tricode_from_team_name("ARI Cardinals") == "ARI"
    assert nfl.get_tricode_from_team_name("JAC") == "JAX"
    assert nfl.get_tricode_from_sgo_team_id("Chicago Bears") == ""
    for tri, name in nfl.NFL_TEAM_NAMES.items():
        assert nfl.get_tricode_from_team_name(name) == tri


@pytest.mark.parametrize("key", [None, "", " ", "a" * 129])
def test_endpoint_requires_valid_collection_key(monkeypatch, key):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    response = TestClient(app).post("/api/nfl/cron/fetch-player-props",
                                    params={} if key is None else {"collection_key": key},
                                    headers={"Authorization": "Bearer test-secret"})
    assert response.status_code == 422


def test_endpoint_delegates_with_exact_collection_key(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    collector = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(props, "collect_player_props", collector)
    monkeypatch.setattr(props, "_collection_lock", asyncio.Lock())
    response = TestClient(app).post("/api/nfl/cron/fetch-player-props?collection_key=run-123",
                                    headers={"Authorization": "Bearer test-secret"})
    assert response.json()["success"]
    assert collector.call_args.args[0] == "run-123"


def test_endpoint_timeout_reports_failure(monkeypatch):
    async def slow(*args):
        await asyncio.sleep(1)

    monkeypatch.setattr(props, "collect_player_props", slow)
    monkeypatch.setattr(props, "SWEEP_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(props, "_collection_lock", asyncio.Lock())
    result = asyncio.run(props.cron_fetch_player_props("run-timeout"))
    assert not result["success"] and not result["sweep_complete"]


def test_delayed_workflow_fails_before_provider_request(collector):
    collector[0][:] = [event(NOW + timedelta(minutes=9))]
    result = asyncio.run(props.collect_player_props("late-run", NOW - timedelta(minutes=5)))
    assert result["games_selected"] == 1
    assert not result["success"]
    props.fetch_sharp_odds.assert_not_called()
    collector[1].assert_not_called()


def test_endpoint_uses_workflow_window_and_rejects_naive_time(monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "test-secret")
    collector = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(props, "collect_player_props", collector)
    monkeypatch.setattr(props, "_collection_lock", asyncio.Lock())
    client = TestClient(app)
    for timestamp, expected in ((NOW.isoformat(), 200), ("2026-09-20T16:00:00", 422)):
        response = client.post("/api/nfl/cron/fetch-player-props",
                               params={"collection_key": "run", "window_at": timestamp},
                               headers={"Authorization": "Bearer test-secret"})
        assert response.status_code == expected
    assert collector.call_args.args[1] == NOW


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
