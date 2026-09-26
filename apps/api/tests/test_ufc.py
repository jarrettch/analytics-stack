from __future__ import annotations

import asyncio
import copy
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from routers import ufc, utils
from routers.sharp_odds import SharpOddsError
from routers.utils import CACHE_TTL_DEFAULT, ET, UTC, Cache

FIXTURES = Path(__file__).parent / "fixtures"
CARD_DATE = date(2026, 9, 26)
PREGAME = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
MAIN_EVENT = "401911630"   # Raul Rosas Jr. vs Raoni Barcelos
OPPONENT_CHANGE = "401914464"  # ESPN: Amaya vs Black; books: Amaya vs Machado
BRYCZEK_VIEIRA = "401911631"  # FanDuel has the moneyline, DraftKings does not


def _card() -> dict:
    return json.loads((FIXTURES / "espn_ufc_card.json").read_text())["events"][0]


def _markets() -> dict[str, list[dict]]:
    return json.loads((FIXTURES / "sharp_ufc_markets.json").read_text())


def _calendar() -> list[dict]:
    return json.loads((FIXTURES / "espn_ufc_calendar.json").read_text())


def _fresh_cache() -> Cache:
    return Cache({"calendar": 3600, "assembled": CACHE_TTL_DEFAULT})


def _report(markets=None, card=None):
    fights = ufc.card_fights(card or _card())
    return fights, ufc.match_card(fights, ufc.build_pools(markets or _markets(), fights))


# --- card selection ---------------------------------------------------------


def test_card_filter_excludes_contender_series_and_flags_unknown_formats():
    assert ufc.is_ufc_card("UFC 332: Silva vs. Wang") is True
    assert ufc.is_ufc_card("UFC Fight Night: Rosas Jr. vs. Barcelos") is True
    assert ufc.is_ufc_card("Noche UFC: Silva vs. Delgado") is True
    assert ufc.is_ufc_card("Dana White's Contender Series: Season 10, Week 8") is False
    assert ufc.is_ufc_card("UFC 335") is True
    assert ufc.is_ufc_card("UFC Freedom 250: Topuria vs. Gaethje") is True
    assert ufc.is_ufc_card("UFC on ESPN 65: Smith vs. Jones") is True
    assert ufc.is_ufc_card("UFC BJJ Invitational 2") is False


@pytest.mark.parametrize("label", [
    "UFC BJJ 2: Tackett vs. Canuto",  # the real, numbered grappling series
    "UFC BJJ 8",
    "UFC BJJ Invitational",
])
def test_grappling_events_are_excluded(label):
    assert ufc.is_ufc_card(label) is False
    assert ufc.is_ufc_card("Road to the Octagon") is None


def test_grappling_event_cannot_displace_the_next_card():
    calendar = [{"label": "UFC BJJ 2: Tackett vs. Canuto", "startDate": "2026-09-25T00:00Z"}] + _calendar()

    assert ufc.next_card_entry(calendar, date(2026, 9, 24))["label"] == "UFC Fight Night: Rosas Jr. vs. Barcelos"


def test_past_unrecognized_entries_do_not_warn(caplog):
    calendar = [{"label": "Octagon Showcase", "startDate": "2026-06-15T00:00Z"}] + _calendar()

    ufc.next_card_entry(calendar, CARD_DATE)

    assert "Octagon Showcase" not in caplog.text


def test_next_card_skips_contender_series_and_past_cards(caplog):
    calendar = _calendar() + [{"label": "Octagon Showcase", "startDate": "2026-09-28T00:00Z"}]

    assert ufc.next_card_entry(calendar, CARD_DATE)["label"] == "UFC Fight Night: Rosas Jr. vs. Barcelos"
    # The next day, the Tuesday Contender Series must not displace UFC 332.
    assert ufc.next_card_entry(calendar, date(2026, 9, 27))["label"] == "UFC 332: Silva vs. Wang"
    assert "Octagon Showcase" in caplog.text


def test_fights_run_main_event_first_with_espn_corners():
    fights = ufc.card_fights(_card())

    assert [fight.order for fight in fights] == list(range(1, 13))
    main = fights[0]
    assert main.fight_id == MAIN_EVENT
    assert [f["name"] for f in main.fighters] == ["Raul Rosas Jr.", "Raoni Barcelos"]
    assert main.weight_class == "Bantamweight"


# --- name matching ----------------------------------------------------------


@pytest.mark.parametrize("espn,sharp", [
    ("José Aldo", "Jose Aldo"),
    ("Waldo Cortes-Acosta", "Waldo Cortes Acosta"),
    ("Raul Rosas Jr.", "Raul Rosas Jr"),
    ("Alatengheili", "Alateng Heili"),
    ("Jose Delgado", "Jose Miguel Delgado"),
    ("Ilimbek Akylbek Uulu", "Ilimbek Akylbek"),
])
def test_names_match(espn, sharp):
    assert ufc.names_match(espn, sharp)


def test_reversed_name_order_is_an_accepted_miss():
    assert not ufc.names_match("Rongzhu", "Zhu Rong")


@pytest.mark.parametrize("espn,sharp", [
    ("Jean Silva", "Silva"),
    ("Silva", "Jean Silva"),
    ("Jose Miguel Delgado", "Delgado"),
])
def test_single_token_subset_never_matches(espn, sharp):
    assert not ufc.names_match(espn, sharp)


def test_surname_only_row_does_not_bind_even_as_the_sole_candidate():
    fights = [_espn_fight("F1", "Jean Silva", "Ann Lee")]
    pool = [_sharp("S1", "Silva", "Ann Lee")]

    report = ufc.match_card(fights, {"draftkings": pool})

    assert report.bindings == {}
    assert report.unmatched == [("F1", "draftkings")]


def test_token_order_is_ignored_for_book_name_order():
    # UFC 332: ESPN and FanDuel "Wang Cong", DraftKings "Cong Wang".
    assert ufc.names_match("Wang Cong", "Cong Wang")


def test_mononyms_still_match_by_equality():
    assert ufc.names_match("Alatengheili", "Alatengheili")


def test_live_card_binds_both_books_and_leaves_opponent_change_unpriced():
    fights, report = _report()

    bound = {fight_id for fight_id, _ in report.bindings}
    assert len(fights) == 12
    assert bound == {fight.fight_id for fight in fights} - {OPPONENT_CHANGE}
    assert sorted(report.unmatched) == [(OPPONENT_CHANGE, "draftkings"), (OPPONENT_CHANGE, "fanduel")]
    assert report.ambiguous == []


def _espn_fight(fight_id, a, b, order=1):
    return ufc.Fight(fight_id, order, "LW", datetime(2026, 9, 27, tzinfo=UTC), "pre", "", "",
                     [{"name": a, "id": f"{fight_id}a", "winner": False},
                      {"name": b, "id": f"{fight_id}b", "winner": False}])


def _sharp(event_id, home, away, book="draftkings"):
    return ufc.SharpFight(book, event_id, f"uuid-{event_id}", {"home": home, "away": away})


def test_a_fighter_with_two_candidates_rejects_the_fight(caplog):
    fights = [_espn_fight("F1", "Jose Silva", "Ann Lee")]
    pool = [_sharp("S1", "Jose Carlos Silva", "Ann Lee"), _sharp("S2", "Jose Aldo Silva", "Kim Park")]

    report = ufc.match_card(fights, {"draftkings": pool})

    assert report.bindings == {}
    assert report.ambiguous == [("F1", "draftkings")]
    assert report.unmatched == []
    assert "ambiguous" in caplog.text


def test_two_espn_fights_claiming_one_sharp_fight_are_both_rejected():
    # Each ESPN fight sees exactly one candidate per fighter ("Jean Silva" is
    # a subset of "Jean Carlos Silva"); only the shared claim reveals it.
    fights = [_espn_fight("F1", "Jean Silva", "Ann Lee"), _espn_fight("F2", "Jean Carlos Silva", "Ann Lee Park")]
    pool = [_sharp("S1", "Jean Silva", "Ann Lee")]

    report = ufc.match_card(fights, {"draftkings": pool})

    assert report.bindings == {}
    assert sorted(report.ambiguous) == [("F1", "draftkings"), ("F2", "draftkings")]


def test_fighters_from_different_sharp_fights_do_not_bind():
    fights = [_espn_fight("F1", "Ann Lee", "Kim Park")]
    pool = [_sharp("S1", "Ann Lee", "Zoe Ray"), _sharp("S2", "Kim Park", "Bo Diaz")]

    report = ufc.match_card(fights, {"draftkings": pool})

    assert report.bindings == {}
    assert report.unmatched == [("F1", "draftkings")]


def test_match_ignores_sharp_home_away_assignment():
    fights = [_espn_fight("F1", "Ann Lee", "Kim Park")]
    sharp = _sharp("S1", "Kim Park", "Ann Lee")  # reversed relative to ESPN
    sharp.rows["moneyline"] = [
        {"selection_type": "home", "odds_american": -200},
        {"selection_type": "away", "odds_american": 170},
    ]

    report = ufc.match_card(fights, {"draftkings": [sharp]})
    fresh = ufc.fresh_markets(report.bindings[("F1", "draftkings")])

    assert fresh["moneyline"] == {"fighterAMoneyline": 170, "fighterBMoneyline": -200}


def test_live_and_alternate_rows_never_enter_the_pool():
    markets = _markets()
    for row in markets["moneyline"]:
        row["is_live"] = True
    for row in markets["total_rounds"]:
        row["is_alternate_line"] = True
    fights = ufc.card_fights(_card())

    pools = ufc.build_pools(markets, fights)

    rows = [r for book in pools.values() for sharp in book for rs in sharp.rows.values() for r in rs]
    assert rows and {r["market_type"] for r in rows} == {"go_the_distance"}


# --- bundles, book selection, staleness ------------------------------------


def _bundle(fight_id: str, book: str, *, persisted=None, markets=None, captured_at=PREGAME):
    fights, report = _report(markets)
    fight = next(f for f in fights if f.fight_id == fight_id)
    binding = report.bindings.get((fight_id, book))
    return ufc.assemble_book(
        fight, persisted,
        ufc.fresh_markets(binding) if binding else {},
        event_uuid=binding.sharp.event_uuid if binding else None,
        captured_at=captured_at,
    )


def test_bundle_carries_identity_prices_and_devigged_probabilities():
    bundle = _bundle(MAIN_EVENT, "draftkings")

    assert bundle["fighterA"] == "Raul Rosas Jr."
    assert bundle["fighterAEspnId"] and bundle["fighterBEspnId"]
    assert bundle["fighterAMoneyline"] is not None and bundle["fighterBMoneyline"] is not None
    assert bundle["fighterAWinProbability"] + bundle["fighterBWinProbability"] == pytest.approx(1)
    assert bundle["totalRounds"] is not None
    assert bundle["sharpEventUuid"]
    assert bundle["observed"]["moneyline"] == {"lastObservedAt": PREGAME.isoformat(), "isStale": False}


def test_book_without_a_moneyline_pair_never_prices_the_fight():
    books = {
        "draftkings": _bundle(BRYCZEK_VIEIRA, "draftkings"),
        "fanduel": _bundle(BRYCZEK_VIEIRA, "fanduel"),
    }
    # DraftKings has a total for this fight but no moneyline; FanDuel the reverse.
    assert books["draftkings"]["totalRounds"] is not None
    assert books["fanduel"]["totalRounds"] is None

    fight = next(f for f in ufc.card_fights(_card()) if f.fight_id == BRYCZEK_VIEIRA)
    payload = ufc.fight_payload(fight, books)

    assert payload["bookmaker"] == "fanduel"
    assert payload["totalRounds"] is None  # never borrowed from DraftKings
    assert all(f["moneyline"] is not None for f in payload["fighters"])


def test_missing_market_keeps_persisted_value_marked_stale():
    persisted = _bundle(MAIN_EVENT, "draftkings")
    markets = _markets()
    markets["total_rounds"] = [r for r in markets["total_rounds"] if "raulrosas" not in r["event_id"]]
    later = datetime(2026, 9, 26, 18, 0, tzinfo=UTC)

    bundle = _bundle(MAIN_EVENT, "draftkings", persisted=persisted, markets=markets, captured_at=later)

    assert bundle["totalRounds"] == persisted["totalRounds"]
    assert bundle["observed"]["total_rounds"] == {"lastObservedAt": PREGAME.isoformat(), "isStale": True}
    assert bundle["observed"]["moneyline"]["isStale"] is False


def test_stale_moneyline_does_not_qualify_a_book():
    fight = next(f for f in ufc.card_fights(_card()) if f.fight_id == MAIN_EVENT)
    dk = _bundle(MAIN_EVENT, "draftkings")
    dk["observed"]["moneyline"]["isStale"] = True
    fd = copy.deepcopy(dk)

    assert ufc.fight_payload(fight, {"draftkings": dk, "fanduel": _bundle(MAIN_EVENT, "fanduel")})["bookmaker"] == "fanduel"
    payload = ufc.fight_payload(fight, {"draftkings": dk, "fanduel": fd})
    assert payload["bookmaker"] is None
    assert all(f["moneyline"] is None for f in payload["fighters"])
    assert payload["totalRounds"] is None and payload["goesTheDistance"] is None


def test_stale_total_renders_flagged_not_current():
    fight = next(f for f in ufc.card_fights(_card()) if f.fight_id == MAIN_EVENT)
    dk = _bundle(MAIN_EVENT, "draftkings")
    dk["observed"]["total_rounds"]["isStale"] = True

    total = ufc.fight_payload(fight, {"draftkings": dk})["totalRounds"]

    assert total["isStale"] is True
    assert total["lastObservedAt"] == PREGAME.isoformat()


# --- collection -------------------------------------------------------------


def _collect(*, markets=None, persisted=None, save_ok=True, captured_at=PREGAME, card=None):
    writes: list = []
    snapshots: list = []

    def save(odds_map, game_date=None, *, table_name):
        writes.append((game_date, table_name, odds_map))
        return save_ok

    def save_snapshots(rows, *, stats=None):
        snapshots.extend(rows)
        stats.update({"inserted": len(rows), "deduplicated": 0, "persistence_ok": True})
        return len(rows)

    data = markets or _markets()

    async def fetch(league, market, *, sweep):
        sweep.requests_made += 1
        return data[market]

    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ufc, "load_persisted_odds", return_value=persisted or {}),
        patch.object(ufc, "fetch_sharp_odds", fetch),
        patch.object(ufc, "save_persisted_odds", save),
        patch.object(ufc, "save_odds_snapshots", save_snapshots),
    ):
        stats = asyncio.run(ufc.collect_card(card or _card(), CARD_DATE, captured_at=captured_at))
    return stats, writes, snapshots


def test_collection_writes_every_fight_once_and_snapshots_each_book():
    stats, writes, snapshots = _collect()

    assert len(writes) == 1  # one upsert for the whole card
    game_date, table, odds_map = writes[0]
    assert (game_date, table) == (CARD_DATE, "ufc_odds")
    assert set(odds_map) == {f.fight_id for f in ufc.card_fights(_card())} - {OPPONENT_CHANGE}
    assert set(odds_map[MAIN_EVENT]) == {"draftkings", "fanduel"}
    assert stats["requests_made"] == 3
    assert stats["latest_persistence_ok"] is True
    assert stats["unmatched"] == 2 and stats["ambiguous"] == 0

    main = [s for s in snapshots if s["matchup_key"] == MAIN_EVENT]
    assert {s["bookmaker"] for s in main} == {"draftkings", "fanduel"}
    assert {s["provider"] for s in main} == {"sharpapi"}
    assert {s["game_date"] for s in snapshots} == {"2026-09-26"}  # ESPN card date, not book dates
    assert all("observed" not in s["odds_data"] for s in snapshots)
    assert all(s["odds_data"]["staleMarkets"] == [] for s in snapshots)


def test_started_fights_are_frozen_not_rewritten():
    # 22:00Z: prelims (21:00Z) have started, the 00:00Z main card has not.
    stats, writes, snapshots = _collect(captured_at=datetime(2026, 9, 26, 22, 0, tzinfo=UTC))

    written = set(writes[0][2])
    assert MAIN_EVENT in written
    assert BRYCZEK_VIEIRA not in written  # a prelim
    assert stats["fights_started"] == 7
    assert BRYCZEK_VIEIRA not in {s["matchup_key"] for s in snapshots}


def test_no_match_market_keeps_every_persisted_value_stale():
    _, first, _ = _collect()
    persisted = {(CARD_DATE.isoformat(), fid): books for fid, books in first[0][2].items()}
    markets = _markets()
    markets["go_the_distance"] = []  # authoritative no_match

    stats, writes, snapshots = _collect(markets=markets, persisted=persisted,
                                captured_at=datetime(2026, 9, 26, 14, 0, tzinfo=UTC))

    assert stats["no_match_markets"] == ["go_the_distance"]
    dk = writes[0][2][MAIN_EVENT]["draftkings"]
    # History says the carried price is stale, not observed at this capture.
    history = [s for s in snapshots if s["matchup_key"] == MAIN_EVENT and s["bookmaker"] == "draftkings"]
    assert history[0]["odds_data"]["staleMarkets"] == ["go_the_distance"]
    assert dk["goesDistanceYes"] == persisted[(CARD_DATE.isoformat(), MAIN_EVENT)]["draftkings"]["goesDistanceYes"]
    assert dk["observed"]["go_the_distance"]["isStale"] is True


def test_incomplete_sweep_writes_nothing():
    save = MagicMock()

    async def fetch(league, market, *, sweep):
        if market == "go_the_distance":
            raise SharpOddsError("SharpAPI page unavailable after retries")
        return _markets()[market]

    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ufc, "load_persisted_odds", return_value={}),
        patch.object(ufc, "fetch_sharp_odds", fetch),
        patch.object(ufc, "save_persisted_odds", save),
        patch.object(ufc, "save_odds_snapshots", save),
        pytest.raises(SharpOddsError),
    ):
        asyncio.run(ufc.collect_card(_card(), CARD_DATE, captured_at=PREGAME))

    save.assert_not_called()


def test_persisted_read_failure_aborts_before_any_request():
    fetch = AsyncMock()
    with (
        patch.object(utils, "supabase", None),
        patch.object(ufc, "fetch_sharp_odds", fetch),
        pytest.raises(RuntimeError),
    ):
        asyncio.run(ufc.collect_card(_card(), CARD_DATE, captured_at=PREGAME))

    fetch.assert_not_called()


# --- cron -------------------------------------------------------------------


def _frozen_at(moment: datetime) -> type[datetime]:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)

    return Frozen


def _cron(collect):
    with (
        patch.object(ufc, "cache", _fresh_cache()),
        patch.object(ufc, "next_card", AsyncMock(return_value=(_card(), CARD_DATE))),
        patch.object(ufc, "collect_card", collect),
        patch.object(ufc, "datetime", _frozen_at(PREGAME)),
    ):
        return asyncio.run(ufc.cron_fetch_odds())


def test_cron_fails_with_stats_when_latest_write_fails():
    result = _cron(AsyncMock(return_value={"latest_persistence_ok": False, "fights_written": 11}))

    assert result["success"] is False
    assert result["fights_written"] == 11
    assert result["card"]["date"] == "2026-09-26"


def test_cron_reports_incomplete_sweep():
    result = _cron(AsyncMock(side_effect=SharpOddsError("SharpAPI page cap reached")))

    assert result["success"] is False
    assert "sweep incomplete" in result["error"]


def test_cron_succeeds_when_written():
    assert _cron(AsyncMock(return_value={"latest_persistence_ok": True}))["success"] is True


# --- /next-card -------------------------------------------------------------


def _next_card(persisted=None, supabase=True, load=None):
    with (
        patch.object(utils, "supabase", MagicMock() if supabase else None),
        patch.object(ufc, "cache", _fresh_cache()),
        patch.object(ufc, "slate_date_et", return_value=CARD_DATE),
        patch.object(ufc, "next_card", AsyncMock(return_value=(_card(), CARD_DATE))) as found,
        patch.object(ufc, "load_persisted_odds", load or MagicMock(return_value=persisted or {})),
    ):
        return asyncio.run(ufc.get_next_card()), found


def test_next_card_renders_every_fight_with_unpriced_ones_in_place():
    _, writes, _ = _collect()
    persisted = {(CARD_DATE.isoformat(), fid): books for fid, books in writes[0][2].items()}

    card, found = _next_card(persisted)

    assert found.await_args.args[0] == CARD_DATE
    assert card["name"] == "UFC Fight Night: Rosas Jr. vs. Barcelos"
    assert card["venue"] == "Meta APEX"
    assert [f["order"] for f in card["fights"]] == list(range(1, 13))
    main = card["fights"][0]
    assert main["bookmaker"] == "draftkings"
    assert main["gameStatus"] == 1
    assert [f["name"] for f in main["fighters"]] == ["Raul Rosas Jr.", "Raoni Barcelos"]
    unpriced = next(f for f in card["fights"] if f["fightId"] == OPPONENT_CHANGE)
    assert unpriced["bookmaker"] is None
    assert unpriced["totalRounds"] is None and unpriced["goesTheDistance"] is None
    assert all(f["moneyline"] is None and f["winner"] is None for f in unpriced["fighters"])


@pytest.mark.parametrize("supabase", [True, False], ids=["read-fails", "unconfigured"])
def test_next_card_returns_503_without_persisted_odds(supabase):
    with pytest.raises(HTTPException) as error:
        _next_card(supabase=supabase, load=MagicMock(side_effect=RuntimeError("down")))

    assert error.value.status_code == 503
    assert error.value.detail == "UFC odds feed unavailable"


def test_final_fight_reports_winner():
    card = _card()
    competition = next(c for c in card["competitions"] if c["id"] == MAIN_EVENT)
    competition["status"]["type"]["state"] = "post"
    for competitor in competition["competitors"]:
        competitor["winner"] = competitor["athlete"]["displayName"] == "Raul Rosas Jr."
    fight = next(f for f in ufc.card_fights(card) if f.fight_id == MAIN_EVENT)

    payload = ufc.fight_payload(fight, None)

    assert payload["gameStatus"] == 3
    assert [f["winner"] for f in payload["fighters"]] == [True, False]


@pytest.mark.parametrize("flags", [[False, False], [None, None], [True, True]], ids=["draw", "missing", "both"])
def test_final_without_a_single_winner_reports_no_result(flags):
    card = _card()
    competition = next(c for c in card["competitions"] if c["id"] == MAIN_EVENT)
    competition["status"]["type"]["state"] = "post"
    for competitor, flag in zip(competition["competitors"], flags):
        competitor["winner"] = flag
    fight = next(f for f in ufc.card_fights(card) if f.fight_id == MAIN_EVENT)

    assert [f["winner"] for f in ufc.fight_payload(fight, None)["fighters"]] == [None, None]


def _break_first_bout(card: dict, how: str) -> None:
    competition = card["competitions"][0]
    if how == "one-fighter":
        competition["competitors"] = competition["competitors"][:1]
    elif how == "blank-name":
        competition["competitors"][0]["athlete"]["displayName"] = "  "
    elif how == "blank-id":
        competition["competitors"][0]["id"] = ""
    elif how == "same-id":
        competition["competitors"][1]["id"] = competition["competitors"][0]["id"]


@pytest.mark.parametrize("how", ["one-fighter", "blank-name", "blank-id", "same-id"])
def test_malformed_bout_rejects_the_whole_card(how):
    card = _card()
    _break_first_bout(card, how)

    with pytest.raises(ufc.CardValidationError):
        ufc.card_fights(card)


SKELETON = {"id": "600000001", "name": "UFC Fight Night: Qatar", "date": "2026-11-21T17:00Z"}


def test_card_without_bouts_is_valid_and_empty():
    # ESPN lists announced cards before their fights.
    assert ufc.card_fights(SKELETON) == []


def test_skeleton_card_collects_nothing_and_succeeds():
    load, fetch, save = MagicMock(), AsyncMock(), MagicMock()
    with (
        patch.object(ufc, "load_persisted_odds", load),
        patch.object(ufc, "fetch_sharp_odds", fetch),
        patch.object(ufc, "save_persisted_odds", save),
        patch.object(ufc, "save_odds_snapshots", save),
    ):
        stats = asyncio.run(ufc.collect_card(SKELETON, date(2026, 11, 21), captured_at=PREGAME))

    load.assert_not_called()
    fetch.assert_not_called()
    save.assert_not_called()
    assert stats["latest_persistence_ok"] is True
    assert stats["requests_made"] == 0 and stats["fights"] == 0


def test_skeleton_card_renders_even_when_supabase_is_down():
    with (
        patch.object(utils, "supabase", None),
        patch.object(ufc, "cache", _fresh_cache()),
        patch.object(ufc, "next_card", AsyncMock(return_value=(SKELETON, date(2026, 11, 21)))),
    ):
        card = asyncio.run(ufc.get_next_card())

    assert card["name"] == "UFC Fight Night: Qatar"
    assert card["fights"] == []


@pytest.mark.parametrize("bad_id", [None, "", "401911631"], ids=["missing", "empty", "duplicate"])
def test_invalid_competition_ids_abort_before_any_read_or_request(bad_id):
    card = _card()
    card["competitions"][0]["id"] = bad_id
    fetch = AsyncMock()
    load = MagicMock()
    with (
        patch.object(utils, "supabase", MagicMock()),
        patch.object(ufc, "load_persisted_odds", load),
        patch.object(ufc, "fetch_sharp_odds", fetch),
        pytest.raises(ufc.CardValidationError),
    ):
        asyncio.run(ufc.collect_card(card, CARD_DATE, captured_at=PREGAME))

    load.assert_not_called()
    fetch.assert_not_called()


def test_cron_collects_calendar_date_not_slate_date():
    # 1:30am ET: the display slate is still yesterday; collection is not.
    now = datetime(2026, 9, 27, 1, 30, tzinfo=ET)
    found = AsyncMock(return_value=None)
    with patch.object(ufc, "next_card", found), patch.object(ufc, "datetime", _frozen_at(now)):
        result = asyncio.run(ufc.cron_fetch_odds())

    assert found.await_args.args[0] == date(2026, 9, 27)
    assert result["success"] is True and result["card"] is None
