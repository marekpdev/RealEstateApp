from typing import Optional

from redis.asyncio import Redis

from config import config

# Module-level lazy singleton, not app.state - the same reasoning
# db/session.py's/rate_limit/redis_client.py's/events/redis_client.py's own
# already establish: reachable from anywhere that imports this module,
# including Chainlit's own mounted sub-app, which never sees the top-level
# FastAPI app's state.
#
# Like events/redis_client.py (and unlike rate_limit/redis_client.py), this
# client is reached from services/market_data_gateway.py, which runs inside
# the graph - and the graph now runs inside a Celery worker process, whose
# task body wraps every call in a fresh asyncio.run() (see
# worker/tasks.py's generate_report docstring), a brand-new event loop each
# time. A client built during one asyncio.run() call is bound to that
# call's loop, so the next task's asyncio.run() call would otherwise hand
# back a client whose connections belong to an already-closed loop and fail
# with "Future attached to a different loop" - the same db/session.py-shaped
# bug events/redis_client.py already guards against. generate_report()
# disposes this client in the same finally block, inside the same
# asyncio.run() call, that already disposes db/session.py's engine and
# events/redis_client.py's client for the identical reason.
_redis_client: Optional[Redis] = None


def get_cache_redis_client() -> Redis:
    """Returns the process-wide async Redis client backing the market-data
    cache-aside layer, creating it on first use."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(config.CACHE_REDIS_URL, decode_responses=True)
    return _redis_client


async def dispose_cache_redis_client() -> None:
    """Closes the process-wide client and clears the singleton, so a later
    get_cache_redis_client() call builds a fresh one rather than reusing a
    closed connection pool. Mirrors db/session.py's dispose_engine() and
    events/redis_client.py's dispose_events_redis_client()."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
