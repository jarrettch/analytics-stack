from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace

from routers import mlb, mlb_pitcher_k_sharp as sharp
from routers.mlb_pitcher_k_odds import PitcherKCollectionStats


CAPTURED_AT = datetime(2026, 9, 29, 1, 20, tzinfo=timezone.utc)
TARGET_DATE = date(2026, 9, 29)


def sharp_row(*, side: str = "over", book: str = "draftkings") -> dict:
    return {
        "league": "mlb",
        "market_type": "player_strikeouts",
        "sportsbook": book,
        "event_id": "mlb_braves_phillies_2026-09-29_b2",
        "event_uuid": "b66c1afa97eb4906",
        "event_start_time": "2026-09-29T18:00:00Z",
        "away_team": "Philadelphia Phillies",
        "home_team": "Atlanta Braves",
        "player_name": "Chris Sale",
        "selection": side.title(),
        "selection_id": f"sale-{book}-{side}",
        "selection_type": side,
        "line": 7.5,
        "odds_american": 102 if side == "over" else -130,
        "is_live": False,
        "is_main_line": True,
        "is_alternate_line": False,
        "is_active": True,
        "is_stale_pregame_price": False,
        "timestamp": "2026-09-29T01:19:07Z",
    }


def schedule() -> list[dict]:
    return [{
        "gamePk": "9001",
        "startsAt": "2026-09-29T18:00:00Z",
        "awayAbbr": "PHI",
        "homeAbbr": "ATL",
        "awayPitcherId": 669923,
        "awayPitcher": "Jesus Luzardo",
        "homePitcherId": 519242,
        "homePitcher": "Chris Sale",
        "abstractGameState": "Preview",
        "detailedState": "Scheduled",
    }]


def test_normalizes_sharp_row_with_provider_and_official_team_context():
    row = sharp.normalize_sharp_pitcher_k_row(
        sharp_row(),
        target_date=TARGET_DATE,
        schedule=schedule(),
        collection_id="11111111-1111-1111-1111-111111111111",
        captured_at=CAPTURED_AT,
    )

    assert row is not None
    assert row["provider"] == "sharpapi"
    assert row["player_id"] == "name:chris sale"
    assert row["player_team_id"] == "ATL"
    assert row["home_team_id"] == "ATL"
    assert row["away_team_id"] == "PHI"
    assert row["side"] == "over"
    assert row["line"] == 7.5
    assert row["american_odds"] == 102
    assert row["is_alternate"] is False


def test_normalizer_ignores_rows_from_another_et_date():
    row = sharp_row()
    row["event_start_time"] = "2026-09-30T18:00:00Z"
    row["is_main_line"] = None

    assert sharp.normalize_sharp_pitcher_k_row(
        row,
        target_date=TARGET_DATE,
        schedule=schedule(),
        collection_id="11111111-1111-1111-1111-111111111111",
        captured_at=CAPTURED_AT,
    ) is None


def test_collects_both_sides_and_copies_identity_stats(monkeypatch):
    async def fetch(*_args, **_kwargs):
        return [sharp_row(side="over"), sharp_row(side="under")]

    persisted = []

    def persist(rows, stats):
        persisted.extend(rows)
        stats.rows_found = len(rows)
        stats.rows_inserted = len(rows)
        stats.succeeded = True
        return stats

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return CAPTURED_AT if tz is None else CAPTURED_AT.astimezone(tz)

    monkeypatch.setattr(sharp, "datetime", Frozen)
    monkeypatch.setattr(sharp, "fetch_sharp_odds", fetch)
    monkeypatch.setattr(sharp, "persist_pitcher_k_rows", persist)
    monkeypatch.setattr(
        sharp,
        "reconcile_pitcher_k_identities",
        lambda *_args: SimpleNamespace(
            events_found=1,
            events_resolved=1,
            pitchers_found=1,
            pitchers_resolved=1,
            frozen_rows_skipped=0,
            succeeded=True,
        ),
    )

    stats = asyncio.run(sharp.collect_sharp_pitcher_k_snapshots(
        target_date=TARGET_DATE,
        schedule=schedule(),
        collection_id="11111111-1111-1111-1111-111111111111",
    ))

    assert {row["side"] for row in persisted} == {"over", "under"}
    assert stats.succeeded is True
    assert stats.identity_succeeded is True
    assert stats.identity_pitchers_resolved == 1


def test_mlb_fallback_collects_sharp_ks_instead_of_marking_them_skipped(monkeypatch):
    collected = PitcherKCollectionStats(
        outcomes_found=2,
        rows_found=2,
        rows_inserted=2,
        identity_events_found=1,
        identity_events_resolved=1,
        identity_pitchers_found=1,
        identity_pitchers_resolved=1,
        identity_succeeded=True,
        succeeded=True,
    )

    async def fetch_sgo(**kwargs):
        await kwargs["optional_market_fallback_consumer"]()
        return {}, 0

    async def collect_sharp(**_kwargs):
        return collected

    async def get_schedule(*_args, **_kwargs):
        return schedule()

    monkeypatch.setattr(mlb, "fetch_sgo_odds", fetch_sgo)
    monkeypatch.setattr(mlb, "collect_sharp_pitcher_k_snapshots", collect_sharp)
    monkeypatch.setattr(mlb, "get_schedule", get_schedule)
    stats = PitcherKCollectionStats()

    asyncio.run(mlb.get_odds_map(
        target_date=TARGET_DATE,
        request_time=CAPTURED_AT,
        pitcher_k_stats=stats,
        pitcher_k_collection_id="11111111-1111-1111-1111-111111111111",
    ))

    assert stats.succeeded is True
    assert stats.skipped is False
    assert stats.rows_inserted == 2
    assert stats.identity_pitchers_resolved == 1
