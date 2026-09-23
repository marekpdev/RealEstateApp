from typing import Optional

from redis.asyncio import Redis

from config import config

# Module-level lazy singleton, not app.state - the same reasoning
# db/session.py's own get_engine()/dispose_engine() already establish (see
# its module docstring): reachable from anywhere that imports this module,
# including Chainlit's own mounted sub-app, which never sees the top-level
# FastAPI app's state.
#
# Unlike db/session.py, there is no per-task dispose-and-rebuild dance to
# worry about here: that pattern exists because a Celery task body calls
# asyncio.run() once per task (a brand-new event loop every time - see
# worker/tasks.py's generate_report docstring), while this client is only
# ever used from the API process, which owns one long-lived event loop for
# its entire lifetime, exactly like db/session.py's engine from app.py/
# cli.py's point of view. A lazy singleton built once and reused for every
# request is correct here for the same reason it's correct there.
_redis_client: Optional[Redis] = None


def get_redis_client() -> Redis:
    """Returns the process-wide async Redis client backing the rate
    limiter, creating it on first use."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(config.RATE_LIMIT_REDIS_URL, decode_responses=True)
    return _redis_client


async def dispose_redis_client() -> None:
    """Closes the process-wide client and clears the singleton, so a later
    get_redis_client() call builds a fresh one rather than reusing a closed
    connection pool. Mirrors db/session.py's dispose_engine()."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
