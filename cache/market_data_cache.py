"""Cache-aside for RapidAPI market-data lookups (see
services/market_data_gateway.py's RapidRealEstateMarketClient.fetch_market_metrics).

Cache-aside (a.k.a. lazy loading): the caller, not the cache, is responsible
for both directions - read the cache first, and on a miss, go to the real
source of truth (RapidAPI) and write the result back before returning it.
Contrast read-through (the cache layer itself owns fetching on a miss,
transparent to the caller - usually a feature of the caching library/proxy
sitting in front of the call, which this app doesn't have) and write-through
(every write goes to the cache and the source of truth together,
synchronously - a pattern for keeping a cache in sync with data *this app*
writes). Neither fits here: RapidAPI is a vendor this app only ever reads
from, so there's no write path to keep in sync, and cache-aside's plain
"check, miss, fetch, populate" is the whole mechanism read-through would
otherwise hide behind a library.
"""

import asyncio
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from redis.asyncio import Redis

from config import config
from schema.market_data import RealEstateGatewayModel

logger = logging.getLogger(__name__)

_CACHE_KEY_PREFIX = "market_data_cache:entry:"
_LOCK_KEY_PREFIX = "market_data_cache:lock:"
_HIT_COUNTER_KEY = "market_data_cache:hits"
_MISS_COUNTER_KEY = "market_data_cache:misses"

# Same atomic compare-and-delete shape worker/tasks.py's
# _RELEASE_LOCK_IF_OWNER_SCRIPT already uses for sync_knowledge_base's own
# overlapping-run lock: only release the lock if it still holds *this*
# caller's own token, so a caller that overran
# MARKET_DATA_CACHE_LOCK_TTL_SECONDS (and had its lock already expire and
# get reacquired by a different caller) can never delete a lock it no
# longer owns.
_RELEASE_LOCK_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


def _normalize_query(location_query: str) -> str:
    """Collapses meaningless variation (case, surrounding/repeated
    whitespace) so "Austin, TX", " austin, tx ", and "Austin,  TX" all hit
    the same cache entry instead of each independently paying for its own
    vendor call."""
    return " ".join(location_query.strip().lower().split())


def _digest(location_query: str) -> str:
    return hashlib.sha256(_normalize_query(location_query).encode("utf-8")).hexdigest()


def _cache_key(location_query: str) -> str:
    """Keyed by a hash of the normalized query, not the normalized string
    itself: location_query is arbitrary, user-influenced text (it comes
    from ingest_input's own city extraction), and hashing it keeps the
    Redis key a fixed, predictable shape regardless of length or embedded
    characters."""
    return f"{_CACHE_KEY_PREFIX}{_digest(location_query)}"


def _lock_key(location_query: str) -> str:
    return f"{_LOCK_KEY_PREFIX}{_digest(location_query)}"


@dataclass(frozen=True)
class CacheAsideOutcome:
    """What fetch_market_metrics_cache_aside actually did, not just the
    value it produced - so a caller can record/log the hit-or-miss outcome
    itself, exactly like rate_limit.token_bucket.RateLimitResult carries
    more than a bare allow/deny bool."""
    metrics: RealEstateGatewayModel
    cache_hit: bool


async def get_cached_market_metrics(
    client: Redis, location_query: str
) -> Optional[RealEstateGatewayModel]:
    raw = await client.get(_cache_key(location_query))
    if raw is None:
        return None
    return RealEstateGatewayModel.model_validate_json(raw)


async def set_cached_market_metrics(
    client: Redis, location_query: str, metrics: RealEstateGatewayModel
) -> None:
    await client.set(
        _cache_key(location_query),
        metrics.model_dump_json(),
        ex=config.MARKET_DATA_CACHE_TTL_SECONDS,
    )


async def get_cache_counters(client: Redis) -> dict:
    """The process-wide hit/miss counts incremented below - concrete,
    inspectable evidence that cache-aside is doing something (redis-cli GET
    market_data_cache:hits/misses, or this helper), rather than something
    only assertable from inside a test."""
    hits, misses = await asyncio.gather(
        client.get(_HIT_COUNTER_KEY), client.get(_MISS_COUNTER_KEY)
    )
    return {"hits": int(hits or 0), "misses": int(misses or 0)}


async def fetch_market_metrics_cache_aside(
    client: Redis,
    location_query: str,
    fetch_fn: Callable[[], Awaitable[RealEstateGatewayModel]],
) -> CacheAsideOutcome:
    """The cache-aside read path: check Redis, and only call fetch_fn (the
    real, paid RapidAPI call) on a genuine miss, writing the result back
    with a TTL before returning it. TTL is the entire invalidation strategy
    here, deliberately - see this module's own docstring for why that's an
    honest choice rather than a shortcut: nothing in this app ever writes
    fresh market data on a schedule independent of a read, so there is no
    write-side event to invalidate on, only staleness to bound.

    A failed fetch_fn is never cached - only a genuinely successful result
    is worth remembering, and the exception still propagates to the caller
    exactly as it would without a cache in front at all.

    Cache stampede prevention: without it, N requests racing in on the same
    now-expired (or never-cached) location
    would all independently miss and all independently call the paid
    vendor at once - the exact "thundering herd" a cache is supposed to
    prevent, just deferred to the TTL boundary instead of happening on
    every call. A single-flight lock (SET NX EX) fixes this: the first
    caller to miss claims the lock and does the real fetch; every other
    concurrent miss for the *same* key finds the lock already held and
    polls the cache instead of also calling the vendor, picking up the
    first caller's result the moment it lands. A caller that loses the race
    and then exhausts its bounded poll (the lock holder is unusually slow,
    or died mid-fetch without releasing) falls back to fetching directly
    itself - an extra vendor call in that rare case is a better outcome
    than a caller that waits forever.
    """
    cached = await get_cached_market_metrics(client, location_query)
    if cached is not None:
        await client.incr(_HIT_COUNTER_KEY)
        return CacheAsideOutcome(metrics=cached, cache_hit=True)

    await client.incr(_MISS_COUNTER_KEY)

    lock_key = _lock_key(location_query)
    lock_token = uuid.uuid4().hex
    acquired = await client.set(
        lock_key, lock_token, nx=True, ex=config.MARKET_DATA_CACHE_LOCK_TTL_SECONDS
    )

    if not acquired:
        for _ in range(config.MARKET_DATA_CACHE_LOCK_WAIT_ATTEMPTS):
            await asyncio.sleep(config.MARKET_DATA_CACHE_LOCK_WAIT_INTERVAL_SECONDS)
            cached = await get_cached_market_metrics(client, location_query)
            if cached is not None:
                return CacheAsideOutcome(metrics=cached, cache_hit=True)
        logger.info(
            "market data cache: gave up waiting for the single-flight lock "
            "holder to populate '%s' - fetching directly instead",
            location_query,
        )
        metrics = await fetch_fn()
        # Not holding the lock doesn't disqualify this result from being
        # cached - the lock only ever gated *who calls the vendor*, not who
        # may write the cache. Populating it here means a caller that had
        # to give up still leaves the entry in a good state for whoever
        # asks next, instead of leaving the cache empty until the
        # (probably stale/abandoned) lock this caller couldn't get finally
        # expires on its own.
        await set_cached_market_metrics(client, location_query, metrics)
        return CacheAsideOutcome(metrics=metrics, cache_hit=False)

    try:
        metrics = await fetch_fn()
        await set_cached_market_metrics(client, location_query, metrics)
        return CacheAsideOutcome(metrics=metrics, cache_hit=False)
    finally:
        await client.eval(_RELEASE_LOCK_IF_OWNER_SCRIPT, 1, lock_key, lock_token)
