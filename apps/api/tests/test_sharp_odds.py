from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest
from routers import sharp_odds as sharp


def page(rows=None, *, more=False, cursor=None, reason=None):
    return {
        "data": rows if rows is not None else [{"selection_id": "one"}],
        "pagination": {"has_more": more, "next_cursor": cursor},
        "meta": {"store": {"reason": reason}},
    }


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("SHARP_API_KEY", "test-key")
    monkeypatch.setattr(sharp, "_request_lock", asyncio.Lock())
    monkeypatch.setattr(sharp, "_next_request_at", 0)
    clock = [1000.0]
    starts = []
    requests = []
    responses = []

    async def sleep(seconds):
        clock[0] += seconds

    async def get(url, **kwargs):
        starts.append(clock[0])
        requests.append({**kwargs, "params": dict(kwargs["params"])})
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        code, payload, headers = response
        return httpx.Response(code, json=payload, headers=headers,
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(sharp.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(sharp.time, "time", lambda: clock[0])
    monkeypatch.setattr(sharp.asyncio, "sleep", sleep)
    client = AsyncMock()
    client.get.side_effect = get
    monkeypatch.setattr(sharp, "get_http_client", lambda: client)
    return responses, starts, requests


def fetch():
    sweep = sharp.SharpSweep()
    rows = asyncio.run(sharp.fetch_sharp_odds("nfl", "player_receptions", sweep=sweep))
    return rows, sweep


def test_cursor_scan_and_no_conditional_requests(provider):
    responses, starts, requests = provider
    responses.extend([(200, page(more=True, cursor="next"), {}),
                      (200, page([{"selection_id": "two"}]), {})])
    rows, sweep = fetch()
    assert len(rows) == 2
    assert sweep.pages_fetched == 2
    assert starts == [1000, 1005]
    assert requests[1]["params"]["cursor"] == "next"
    assert "cursor" not in requests[0]["params"]
    for request in requests:
        assert request["headers"] == {"X-API-Key": "test-key"}
        assert request["params"]["is_live"] == "false"
        assert request["params"]["is_main_line"] == "true"
        assert request["params"]["sportsbook"] == "draftkings,fanduel"
        assert "offset" not in request["params"]


@pytest.mark.parametrize("payload", [page(more=True), {"data": [{}]},
                                     page([], more=True, cursor="next", reason="no_match")])
def test_incomplete_pagination_fails(provider, payload):
    provider[0].append((200, payload, {}))
    with pytest.raises(sharp.SharpOddsError):
        fetch()


def test_repeated_cursor_fails(provider):
    provider[0].extend([(200, page(more=True, cursor="same"), {})] * 2)
    with pytest.raises(sharp.SharpOddsError, match="repeated"):
        fetch()


def test_page_cap_fails_instead_of_truncating(provider, monkeypatch):
    monkeypatch.setattr(sharp, "MAX_PAGES", 1)
    provider[0].append((200, page(more=True, cursor="more"), {}))
    with pytest.raises(sharp.SharpOddsError, match="page cap"):
        fetch()


@pytest.mark.parametrize("reason", ["warming", "store_empty"])
def test_transient_empty_retries(provider, reason):
    provider[0].extend([(200, page([], reason=reason), {}), (200, page(), {})])
    rows, sweep = fetch()
    assert rows
    assert sweep.requests_made == 2
    assert sweep.pages_fetched == 1
    assert provider[1] == [1000, 1005]


def test_no_match_is_authoritative(provider):
    provider[0].append((200, page([], reason="no_match"), {}))
    rows, sweep = fetch()
    assert rows == []
    assert sweep.requests_made == 1


@pytest.mark.parametrize("code,payload", [(304, None), (200, page([]))])
def test_no_replay_or_unexplained_empty_response(provider, code, payload):
    provider[0].append((code, payload, {}))
    with pytest.raises(sharp.SharpOddsError):
        fetch()


def test_429_retry_after_and_rate_headers(provider):
    provider[0].extend([
        (429, {}, {"Retry-After": "30", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1020"}),
        (200, page(), {"X-RateLimit-Remaining": "4", "X-RateLimit-Reset": "1052"}),
    ])
    _, sweep = fetch()
    assert provider[1] == [1000, 1030]
    assert sweep.requests_made == 2
    assert sweep.rate_limit == {"remaining": 4, "reset_s": 22, "throttled_waits": 1}


def test_requests_use_remaining_window_budget(provider):
    provider[0].extend([
        (200, page(more=True, cursor="next"), {"X-RateLimit-Remaining": "2", "X-RateLimit-Reset": "1060"}),
        (200, page(), {}),
    ])
    fetch()
    assert provider[1] == [1000, 1030]


@pytest.mark.parametrize("response", [(429, {}, {}), (503, {}, {}),
                                      (200, page([], reason="warming"), {}),
                                      httpx.ConnectError("offline")])
def test_retries_are_bounded(provider, response):
    provider[0].extend([response] * sharp.MAX_ATTEMPTS)
    with pytest.raises(sharp.SharpOddsError):
        fetch()
    assert len(provider[1]) == sharp.MAX_ATTEMPTS
    assert provider[1] == [1000, 1005, 1015, 1035]


def test_pacing_survives_market_boundaries(provider):
    provider[0].extend([(200, page(), {})] * 2)
    fetch()
    fetch()
    assert provider[1] == [1000, 1005]


def test_provider_cursor_rejection_aborts_without_offset_fallback(provider):
    provider[0].extend([
        (200, page(more=True, cursor="rejected"), {}),
        (400, {"error": {"code": "invalid_cursor"}}, {}),
    ])
    with pytest.raises(sharp.SharpOddsError, match="HTTP 400: invalid_cursor"):
        fetch()
    assert len(provider[2]) == 2
    assert all("offset" not in request["params"] for request in provider[2])
