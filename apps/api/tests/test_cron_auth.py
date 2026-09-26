"""HTTP-level auth coverage for the odds and settlement cron collectors.

The per-sport collector tests call cron_fetch_odds() directly, which bypasses
FastAPI's dependency layer entirely, so they prove nothing about whether these
routes are actually protected. These go through the app for that reason.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import auth, mlb, nba, ncaaf, nfl, nhl, ufc, wnba

SECRET = "test-cron-secret"

CRON_ROUTES = [
    pytest.param("/api/nba/cron/fetch-odds", nba, id="nba"),
    pytest.param("/api/mlb/cron/fetch-odds", mlb, id="mlb"),
    pytest.param("/api/nhl/cron/fetch-odds", nhl, id="nhl"),
    pytest.param("/api/wnba/cron/fetch-odds", wnba, id="wnba"),
    pytest.param("/api/nfl/cron/fetch-odds", nfl, id="nfl"),
    pytest.param("/api/ncaaf/cron/fetch-odds", ncaaf, id="ncaaf"),
]
PROTECTED_CRON_ROUTES = [
    *CRON_ROUTES,
    pytest.param("/api/nfl/cron/fetch-player-props", nfl, id="nfl-player-props"),
    pytest.param("/api/mlb/cron/settle-pitcher-ks", mlb, id="mlb-pitcher-k-settlement"),
    pytest.param("/api/ufc/cron/fetch-odds", ufc, id="ufc"),
]


@pytest.fixture
def client():
    # Deliberately not a context manager: skipping lifespan keeps the shared
    # httpx pool closed for tests that must never reach the network.
    return TestClient(app)


@pytest.fixture
def cron_secret(monkeypatch):
    monkeypatch.setenv(auth.CRON_SECRET_ENV, SECRET)
    return SECRET


@pytest.mark.parametrize("path,module", PROTECTED_CRON_ROUTES)
def test_rejects_request_without_token(client, cron_secret, path, module):
    assert client.post(path).status_code == 401


@pytest.mark.parametrize("path,module", PROTECTED_CRON_ROUTES)
def test_rejects_wrong_token(client, cron_secret, path, module):
    response = client.post(path, headers={"Authorization": "Bearer wrong-secret"})
    assert response.status_code == 401


@pytest.mark.parametrize("path,module", PROTECTED_CRON_ROUTES)
def test_rejects_non_bearer_scheme(client, cron_secret, path, module):
    response = client.post(path, headers={"Authorization": f"Basic {SECRET}"})
    assert response.status_code == 401


@pytest.mark.parametrize("path,module", PROTECTED_CRON_ROUTES)
def test_get_is_no_longer_allowed(client, cron_secret, path, module):
    # These endpoints trigger provider spend and writes, so they must not sit
    # behind a method that crawlers and prefetchers treat as safe.
    assert client.get(path).status_code == 405


@pytest.mark.parametrize("path,module", PROTECTED_CRON_ROUTES)
@pytest.mark.parametrize(
    "configured_value",
    [None, "", "   "],
    ids=["missing", "empty", "whitespace"],
)
def test_fails_closed_when_secret_is_unconfigured(
    client, monkeypatch, path, module, configured_value
):
    if configured_value is None:
        monkeypatch.delenv(auth.CRON_SECRET_ENV, raising=False)
    else:
        monkeypatch.setenv(auth.CRON_SECRET_ENV, configured_value)

    response = client.post(path, headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


@pytest.mark.parametrize("path,module", CRON_ROUTES)
@pytest.mark.parametrize("offset", [-1, 2, 400])
def test_rejects_offset_outside_supported_range(
    client, cron_secret, path, module, offset
):
    """Every collector supports only today and tomorrow."""
    response = client.post(
        path,
        params={"offset": offset},
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert response.status_code == 422


def test_rejects_overlong_collection_key(client, cron_secret):
    response = client.post(
        "/api/mlb/cron/fetch-odds",
        params={"collection_key": "x" * 129},
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("lookahead_days", [-1, 8, 400])
def test_nfl_rejects_lookahead_outside_supported_range(
    client, cron_secret, lookahead_days
):
    response = client.post(
        "/api/nfl/cron/fetch-odds",
        params={"lookahead_days": lookahead_days},
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("lookback_days", [0, 8, 400])
def test_mlb_settlement_rejects_lookback_outside_supported_range(
    client, cron_secret, lookback_days
):
    response = client.post(
        "/api/mlb/cron/settle-pitcher-ks",
        params={"lookback_days": lookback_days},
        headers={"Authorization": f"Bearer {SECRET}"},
    )
    assert response.status_code == 422


def test_valid_token_reaches_pitcher_k_settlement(client, cron_secret, monkeypatch):
    calls = []

    async def fake_settle(target):
        calls.append(target)
        return {"success": True}

    monkeypatch.setattr(mlb, "_settle_pitcher_k_date", fake_settle)
    monkeypatch.setattr(
        mlb,
        "load_stale_settlement_counts",
        lambda before_date: {
            "beforeDate": before_date.isoformat(),
            "oldestDate": None,
            "observations": 0,
            "byStatus": {},
        },
    )
    response = client.post(
        "/api/mlb/cron/settle-pitcher-ks",
        params={"date": "2026-08-17"},
        headers={"Authorization": f"Bearer {SECRET}"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert [target.isoformat() for target in calls] == ["2026-08-17"]


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_valid_token_reaches_the_collector(
    client, cron_secret, monkeypatch, path, module
):
    """Guards the opposite failure: a dependency that rejects every request."""
    calls = []

    async def fake_get_odds_map(**kwargs):
        calls.append(kwargs)
        # Collectors that gate success on a completed latest-value write
        # (NCAAF) read this; the rest ignore the extra key.
        if kwargs.get("collection_stats") is not None:
            kwargs["collection_stats"]["latest_persistence_ok"] = True
        return {}, 0

    monkeypatch.setattr(module, "get_odds_map", fake_get_odds_map)

    response = client.post(path, headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(calls) == 1


def test_valid_token_reaches_ufc_collector(client, cron_secret, monkeypatch):
    found = []

    async def fake_next_card(today):
        found.append(today)
        return None

    monkeypatch.setattr(ufc, "next_card", fake_next_card)

    response = client.post(
        "/api/ufc/cron/fetch-odds", headers={"Authorization": f"Bearer {SECRET}"}
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(found) == 1
