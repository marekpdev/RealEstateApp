import asyncio

import pytest
import respx
import httpx
from unittest.mock import patch
from fastapi import HTTPException
from resilience.circuit_breaker import CircuitState, get_circuit_breaker
from services.base_api_client import BaseAPIClient
from services.market_data_gateway import RapidRealEstateMarketClient

# These tests exercise BaseAPIClient's real-network branch with respx intercepting
# every request, so no packet ever leaves the machine. They patch OFFLINE_MODE off
# for that reason: the offline-mode guard (config/safety.py) can't tell a
# respx-intercepted call from a genuine one, and would otherwise block this
# legitimate, cost-free test of the vendor-integration code path.
#
# Retry tests patch the backoff base/max down to ~0 so a test that exhausts
# every attempt doesn't actually sleep through tenacity's real backoff -
# only API_CLIENT_MAX_ATTEMPTS itself is left meaningful. patch.multiple lets
# both be set from a single with-item.
def _fast_backoff():
    return patch.multiple(
        "services.base_api_client.config",
        API_CLIENT_RETRY_BACKOFF_BASE_SECONDS=0.001,
        API_CLIENT_RETRY_BACKOFF_MAX_SECONDS=0.001,
    )


# Circuit breaker tests patch resilience.circuit_breaker.config directly -
# get_circuit_breaker() reads it once, lazily, the moment a given name's
# breaker is first constructed (unlike the retry policy above, a breaker
# has to keep its state across calls, so its config can't be re-read fresh
# on every call the way the stateless retry policy's can). Each test uses
# a base_url unique to itself so the per-test conftest breaker-registry
# reset never has to worry about ordering between tests.
def _breaker_config(failure_threshold=2, reset_timeout_seconds=999.0):
    return patch.multiple(
        "resilience.circuit_breaker.config",
        CIRCUIT_BREAKER_FAILURE_THRESHOLD=failure_threshold,
        CIRCUIT_BREAKER_RESET_TIMEOUT_SECONDS=reset_timeout_seconds,
    )


@pytest.mark.asyncio
async def test_base_api_client_error_handling():
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            respx.get("https://api.test/error").respond(status_code=500, text="Internal Server Error")
            with patch("config.config.OFFLINE_MODE", False), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 1):
                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/error")
                assert excinfo.value.status_code == 500

                respx.get("https://api.test/429").respond(status_code=429)
                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/429")
                assert excinfo.value.status_code == 429
                assert "Vendor API threshold exhausted" in excinfo.value.detail


@pytest.mark.asyncio
async def test_base_api_client_retries_5xx_then_succeeds():
    """A transient 500 followed by a 200 must recover, not fail the caller."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            route = respx.get("https://api.test/flaky")
            route.side_effect = [
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json={"status": "ok"}),
            ]
            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 3):
                result = await client._send_request("GET", "/flaky")

            assert result == {"status": "ok"}
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_base_api_client_retries_429_then_succeeds():
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            route = respx.get("https://api.test/throttled")
            route.side_effect = [
                httpx.Response(429),
                httpx.Response(200, json={"status": "ok"}),
            ]
            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 3):
                result = await client._send_request("GET", "/throttled")

            assert result == {"status": "ok"}
            assert route.call_count == 2


@pytest.mark.asyncio
async def test_base_api_client_does_not_retry_client_errors():
    """A non-429 4xx is the caller's fault - it must fail on the first attempt."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            route = respx.get("https://api.test/bad-request")
            route.respond(status_code=400, text="Bad Request")

            with patch("config.config.OFFLINE_MODE", False), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 5):
                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/bad-request")

            assert excinfo.value.status_code == 400
            assert route.call_count == 1


@pytest.mark.asyncio
async def test_base_api_client_exhausts_retry_budget_and_fails():
    """A vendor that's always down must eventually surface as a 5xx, not retry forever."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            route = respx.get("https://api.test/down")
            route.respond(status_code=503, text="Service Unavailable")

            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 3):
                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/down")

            assert excinfo.value.status_code == 503
            assert route.call_count == 3


@pytest.mark.asyncio
async def test_base_api_client_retries_connection_error_then_succeeds():
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.test")

        async with respx.mock:
            route = respx.get("https://api.test/flaky-conn")
            route.side_effect = [
                httpx.ConnectError("connection refused"),
                httpx.Response(200, json={"status": "ok"}),
            ]
            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 3):
                result = await client._send_request("GET", "/flaky-conn")

            assert result == {"status": "ok"}
            assert route.call_count == 2

@pytest.mark.asyncio
async def test_base_api_client_circuit_breaker_opens_and_skips_the_network():
    """A persistently-down vendor must trip the breaker after
    CIRCUIT_BREAKER_FAILURE_THRESHOLD calls, and every call made while it's
    open must fail immediately with the vendor never contacted again."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.breaker-open-test")

        async with respx.mock:
            route = respx.get("https://api.breaker-open-test/down")
            route.respond(status_code=503, text="Service Unavailable")

            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 1), \
                 _breaker_config(failure_threshold=2):
                for _ in range(2):
                    with pytest.raises(HTTPException) as excinfo:
                        await client._send_request("GET", "/down")
                    assert excinfo.value.status_code == 503
                assert route.call_count == 2

                breaker = get_circuit_breaker("https://api.breaker-open-test")
                assert breaker.state == CircuitState.OPEN

                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/down")
                assert excinfo.value.status_code == 503
                assert "Circuit breaker" in excinfo.value.detail
                assert route.call_count == 2  # unchanged - no new request was sent


@pytest.mark.asyncio
async def test_base_api_client_circuit_breaker_ignores_client_errors():
    """A non-429 4xx is the caller's own fault, not a sign the vendor is
    unhealthy - it must never count toward tripping the breaker, however
    many times it happens."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.breaker-4xx-test")

        async with respx.mock:
            route = respx.get("https://api.breaker-4xx-test/bad")
            route.respond(status_code=400, text="Bad Request")

            with patch("config.config.OFFLINE_MODE", False), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 5), \
                 _breaker_config(failure_threshold=1):
                for _ in range(3):
                    with pytest.raises(HTTPException) as excinfo:
                        await client._send_request("GET", "/bad")
                    assert excinfo.value.status_code == 400

                breaker = get_circuit_breaker("https://api.breaker-4xx-test")
                assert breaker.state == CircuitState.CLOSED
                assert route.call_count == 3


@pytest.mark.asyncio
async def test_base_api_client_circuit_breaker_recovers_via_half_open():
    """Once reset_timeout_seconds has passed, exactly one probe call must
    reach the vendor - and a successful probe must close the breaker again."""
    async with httpx.AsyncClient() as httpx_client:
        client = BaseAPIClient(client=httpx_client, base_url="https://api.breaker-recovery-test")

        async with respx.mock:
            route = respx.get("https://api.breaker-recovery-test/flaky")
            route.respond(status_code=503)

            with patch("config.config.OFFLINE_MODE", False), \
                 _fast_backoff(), \
                 patch("services.base_api_client.config.API_CLIENT_MAX_ATTEMPTS", 1), \
                 _breaker_config(failure_threshold=1, reset_timeout_seconds=0.05):
                with pytest.raises(HTTPException):
                    await client._send_request("GET", "/flaky")
                assert route.call_count == 1

                # Still within the reset window - refused without a new request.
                with pytest.raises(HTTPException) as excinfo:
                    await client._send_request("GET", "/flaky")
                assert "Circuit breaker" in excinfo.value.detail
                assert route.call_count == 1

                await asyncio.sleep(0.06)

                # Half-open: the one probe reaches the vendor, and it succeeds.
                route.respond(status_code=200, json={"status": "ok"})
                result = await client._send_request("GET", "/flaky")
                assert result == {"status": "ok"}
                assert route.call_count == 2

                # Closed again - normal calls keep flowing.
                result = await client._send_request("GET", "/flaky")
                assert result == {"status": "ok"}
                assert route.call_count == 3


@pytest.mark.asyncio
async def test_base_api_client_mock_fixture(tmp_path):
    fixture = tmp_path / "mock.json"
    fixture.write_text('{"status": "ok"}', encoding="utf-8")
    
    client = BaseAPIClient()
    result = await client._send_request("GET", "/any", fixture_path=fixture, mock_external_api=True)
    assert result == {"status": "ok"}

@pytest.mark.asyncio
async def test_rapid_real_estate_client_metrics():
    client = RapidRealEstateMarketClient()

    mock_data = {
        "data": [
            {"price": 100000},
            {"price": 200000},
            {"price": 300000}
        ]
    }

    async with respx.mock:
        respx.get("https://real-time-real-estate-data.p.rapidapi.com/search").respond(json=mock_data)

        # Ensure we don't skip due to missing API key in tests if MOCK is off
        original_api_key = client.api_key
        client.api_key = "test_key"

        with patch("services.market_data_gateway.MOCK_MARKET_DATA_API", False), \
             patch("config.config.OFFLINE_MODE", False):
            result = await client.fetch_market_metrics("Austin, TX")
            assert result.requested_location == "Austin, TX"
            assert result.total_listings == 3
            assert result.average_price == 200000.0
            assert result.median_price == 200000.0
            assert result.lowest_listing == 100000.0
            assert result.highest_listing == 300000.0

        client.api_key = original_api_key

@pytest.mark.asyncio
async def test_rapid_real_estate_client_empty_results():
    client = RapidRealEstateMarketClient()
    mock_data = {"data": []}

    async with respx.mock:
        respx.get("https://real-time-real-estate-data.p.rapidapi.com/search").respond(json=mock_data)
        client.api_key = "test_key"

        with patch("services.market_data_gateway.MOCK_MARKET_DATA_API", False), \
             patch("config.config.OFFLINE_MODE", False):
            with pytest.raises(HTTPException) as excinfo:
                await client.fetch_market_metrics("Unknown City")
            assert excinfo.value.status_code == 404
