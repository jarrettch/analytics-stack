import asyncio
import time

import httpx

from routers import usage


def test_normalize_camel_case_sgo_usage_shape_redacts_account_fields():
    result = usage.normalize_sgo_usage(
        {
            "success": True,
            "data": {
                "keyID": "secret-key-id",
                "customerID": "secret-customer-id",
                "email": "private@example.com",
                "isActive": True,
                "tier": "amateur",
                "rateLimits": {
                    "per-month": {
                        "maxEntitiesPerInterval": 2500,
                        "currentIntervalEntities": 625,
                        "maxRequestsPerInterval": "unlimited",
                        "currentIntervalRequests": "n/a",
                        "currentIntervalEndTime": "2026-09-01T00:00:00.000Z",
                    }
                },
            },
        }
    )

    monthly = next(item for item in result["intervals"] if item["key"] == "per-month")
    assert result["provider"] == "SportsGameOdds"
    assert result["tier"] == "amateur"
    assert result["active"] is True
    assert result["billingCycleAnchor"] == "2026-04-08T00:00:00Z"
    assert result["billingWindowDays"] == 30
    assert monthly["entities"] == {"current": 625, "max": 2500}
    assert monthly["requests"] == {"current": "n/a", "max": "unlimited"}
    assert monthly["resetsAt"] == "2026-09-01T00:00:00.000Z"
    assert "keyID" not in result
    assert "customerID" not in result
    assert "email" not in result


def test_normalize_current_sgo_usage_shape():
    result = usage.normalize_sgo_usage(
        {
            "success": True,
            "data": {
                "isActive": True,
                "tier": "amateur",
                "rateLimits": {
                    "per-minute": {
                        "max-requests": 10,
                        "current-requests": 2,
                        "max-entities": "unlimited",
                    },
                    "per-month": {
                        "max-requests": "unlimited",
                        "max-entities": 2500,
                        "current-entities": 400,
                    },
                },
            },
        }
    )

    minute = result["intervals"][0]
    monthly = result["intervals"][-1]
    assert minute["requests"] == {"current": 2, "max": 10}
    assert minute["entities"] == {"current": None, "max": "unlimited"}
    assert monthly["entities"] == {"current": 400, "max": 2500}


def test_billing_cycle_anchor_env_override(monkeypatch):
    monkeypatch.setenv("SGO_BILLING_CYCLE_ANCHOR", "2026-09-05T14:30:00-04:00")
    payload = {"success": True, "data": {"isActive": True, "rateLimits": {}}}
    assert (
        usage.normalize_sgo_usage(payload)["billingCycleAnchor"]
        == "2026-09-05T18:30:00Z"
    )

    monkeypatch.setenv("SGO_BILLING_CYCLE_ANCHOR", "2026-09-07")
    assert (
        usage.normalize_sgo_usage(payload)["billingCycleAnchor"]
        == "2026-04-08T00:00:00Z"
    )

    monkeypatch.setenv("SGO_BILLING_CYCLE_ANCHOR", "not-a-date")
    assert (
        usage.normalize_sgo_usage(payload)["billingCycleAnchor"]
        == "2026-04-08T00:00:00Z"
    )


def test_forced_refresh_within_floor_skips_upstream_call(monkeypatch):
    upstream_calls = []

    class CountingClient:
        async def get(self, *_args, **_kwargs):
            upstream_calls.append(1)
            raise AssertionError("upstream must not be called inside the floor")

    monkeypatch.setenv("SPORTS_GAME_ODDS_KEY", "test-key")
    monkeypatch.setattr(usage, "get_http_client", lambda: CountingClient())
    monkeypatch.setattr(usage, "_usage_cache", {"provider": "SportsGameOdds"})
    monkeypatch.setattr(usage, "_usage_cache_time", time.monotonic())

    result = asyncio.run(usage.sports_game_odds_usage(refresh=True))

    assert result["cached"] is True
    assert not upstream_calls


def test_usage_endpoint_returns_stale_safe_error(monkeypatch):
    class FailingClient:
        async def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("no network")

    monkeypatch.setenv("SPORTS_GAME_ODDS_KEY", "test-key")
    monkeypatch.setattr(usage, "get_http_client", lambda: FailingClient())
    usage._usage_cache = None

    try:
        asyncio.run(usage.sports_game_odds_usage(refresh=True))
    except Exception as exc:
        assert exc.status_code == 503
        assert "temporarily unavailable" in exc.detail
    else:
        raise AssertionError("Expected HTTPException")
