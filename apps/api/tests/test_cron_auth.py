"""HTTP-level auth coverage for the five cron collectors.

The per-sport collector tests call cron_fetch_odds() directly, which bypasses
FastAPI's dependency layer entirely, so they prove nothing about whether these
routes are actually protected. These go through the app for that reason.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app
from routers import auth, mlb, nba, nfl, nhl, wnba

SECRET = "test-cron-secret"

CRON_ROUTES = [
    pytest.param("/api/nba/cron/fetch-odds", nba, id="nba"),
    pytest.param("/api/mlb/cron/fetch-odds", mlb, id="mlb"),
    pytest.param("/api/nhl/cron/fetch-odds", nhl, id="nhl"),
    pytest.param("/api/wnba/cron/fetch-odds", wnba, id="wnba"),
    pytest.param("/api/nfl/cron/fetch-odds", nfl, id="nfl"),
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


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_rejects_request_without_token(client, cron_secret, path, module):
    assert client.post(path).status_code == 401


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_rejects_wrong_token(client, cron_secret, path, module):
    response = client.post(path, headers={"Authorization": "Bearer wrong-secret"})
    assert response.status_code == 401


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_rejects_non_bearer_scheme(client, cron_secret, path, module):
    response = client.post(path, headers={"Authorization": f"Basic {SECRET}"})
    assert response.status_code == 401


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_get_is_no_longer_allowed(client, cron_secret, path, module):
    # These endpoints trigger provider spend and writes, so they must not sit
    # behind a method that crawlers and prefetchers treat as safe.
    assert client.get(path).status_code == 405


@pytest.mark.parametrize("path,module", CRON_ROUTES)
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


@pytest.mark.parametrize("path,module", CRON_ROUTES)
def test_valid_token_reaches_the_collector(
    client, cron_secret, monkeypatch, path, module
):
    """Guards the opposite failure: a dependency that rejects every request."""
    calls = []

    async def fake_get_odds_map(**kwargs):
        calls.append(kwargs)
        return {}, 0

    monkeypatch.setattr(module, "get_odds_map", fake_get_odds_map)

    response = client.post(path, headers={"Authorization": f"Bearer {SECRET}"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert len(calls) == 1
