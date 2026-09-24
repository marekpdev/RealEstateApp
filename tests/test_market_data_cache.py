import asyncio
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from cache.market_data_cache import (
    fetch_market_metrics_cache_aside,
    get_cache_counters,
    get_cached_market_metrics,
    set_cached_market_metrics,
)
from cache.redis_client import get_cache_redis_client
from schema.market_data import RealEstateGatewayModel


def _metrics(location: str = "Austin, TX", total_listings: int = 3) -> RealEstateGatewayModel:
    return RealEstateGatewayModel(
        requested_location=location,
        total_listings=total_listings,
        average_price=200000.0,
        median_price=200000.0,
        highest_listing=300000.0,
        lowest_listing=100000.0,
        raw_properties=[],
    )


def _counting_fetch_fn(calls: list, result: RealEstateGatewayModel, delay: float = 0.0):
    async def _fetch():
        calls.append(1)
        if delay:
            await asyncio.sleep(delay)
        return result

    return _fetch


@pytest.mark.asyncio
async def test_cache_miss_then_hit_avoids_a_second_fetch():
    client = get_cache_redis_client()
    calls: list = []
    result = _metrics()

    first = await fetch_market_metrics_cache_aside(
        client, "Austin, TX", _counting_fetch_fn(calls, result)
    )
    assert first.cache_hit is False
    assert len(calls) == 1

    second = await fetch_market_metrics_cache_aside(
        client, "Austin, TX", _counting_fetch_fn(calls, result)
    )
    assert second.cache_hit is True
    assert len(calls) == 1  # the vendor was never called again
    assert second.metrics.requested_location == "Austin, TX"


@pytest.mark.asyncio
async def test_query_normalization_shares_one_cache_entry():
    """Case and whitespace variation on the same location must hit the same
    cache entry, not each pay for their own vendor call."""
    client = get_cache_redis_client()
    await set_cached_market_metrics(client, "Austin, TX", _metrics())

    cached = await get_cached_market_metrics(client, "  austin,  TX ")
    assert cached is not None
    assert cached.requested_location == "Austin, TX"


@pytest.mark.asyncio
async def test_cache_entry_ttl_matches_configured_value():
    client = get_cache_redis_client()
    with patch("cache.market_data_cache.config.MARKET_DATA_CACHE_TTL_SECONDS", 120):
        await set_cached_market_metrics(client, "Denver, CO", _metrics("Denver, CO"))

    from cache.market_data_cache import _cache_key

    ttl = await client.ttl(_cache_key("Denver, CO"))
    assert 0 < ttl <= 120


@pytest.mark.asyncio
async def test_a_failed_fetch_is_never_cached_and_still_propagates():
    client = get_cache_redis_client()

    async def _always_fails():
        raise HTTPException(status_code=404, detail="no listings")

    with pytest.raises(HTTPException):
        await fetch_market_metrics_cache_aside(client, "Nowhere, XX", _always_fails)

    assert await get_cached_market_metrics(client, "Nowhere, XX") is None


@pytest.mark.asyncio
async def test_concurrent_misses_for_the_same_location_make_one_vendor_call():
    """The cache-stampede stretch goal: N callers racing in on the same
    never-cached location must produce exactly one fetch_fn call, not N -
    every other caller picks up the winner's result via the single-flight
    lock's bounded poll instead."""
    client = get_cache_redis_client()
    calls: list = []
    result = _metrics("Miami, FL")

    async def _slow_fetch():
        calls.append(1)
        await asyncio.sleep(0.2)
        return result

    outcomes = await asyncio.gather(
        *[
            fetch_market_metrics_cache_aside(client, "Miami, FL", _slow_fetch)
            for _ in range(5)
        ]
    )

    assert len(calls) == 1
    assert all(o.metrics.requested_location == "Miami, FL" for o in outcomes)
    # Exactly one caller actually did the work; the rest observed a hit via
    # the single-flight wait.
    assert sum(1 for o in outcomes if o.cache_hit) == 4


@pytest.mark.asyncio
async def test_a_caller_falls_back_to_fetching_directly_if_the_lock_holder_never_populates():
    """If the lock holder dies mid-fetch (or is just unusually slow) without
    ever writing the cache entry, a waiter must not hang forever - it gives
    up after its bounded poll and fetches for itself."""
    client = get_cache_redis_client()
    from cache.market_data_cache import _lock_key

    # Simulate a lock holder that claimed the lock and then vanished.
    await client.set(_lock_key("Boise, ID"), "someone-elses-token", nx=True, ex=60)

    calls: list = []
    with patch("cache.market_data_cache.config.MARKET_DATA_CACHE_LOCK_WAIT_ATTEMPTS", 2), \
         patch("cache.market_data_cache.config.MARKET_DATA_CACHE_LOCK_WAIT_INTERVAL_SECONDS", 0.01):
        outcome = await fetch_market_metrics_cache_aside(
            client, "Boise, ID", _counting_fetch_fn(calls, _metrics("Boise, ID"))
        )

    assert len(calls) == 1
    assert outcome.cache_hit is False


@pytest.mark.asyncio
async def test_hit_and_miss_counters_are_recorded():
    client = get_cache_redis_client()
    calls: list = []
    result = _metrics("Reno, NV")

    before = await get_cache_counters(client)

    await fetch_market_metrics_cache_aside(client, "Reno, NV", _counting_fetch_fn(calls, result))
    await fetch_market_metrics_cache_aside(client, "Reno, NV", _counting_fetch_fn(calls, result))

    after = await get_cache_counters(client)
    assert after["misses"] == before["misses"] + 1
    assert after["hits"] == before["hits"] + 1
