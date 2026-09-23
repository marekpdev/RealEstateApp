from typing import Optional

from redis.asyncio import Redis

from config import config

# Module-level lazy singleton, not app.state - the same reasoning
# db/session.py's/rate_limit/redis_client.py's own already establish:
# reachable from anywhere that imports this module, including Chainlit's own
# mounted sub-app, which never sees the top-level FastAPI app's state.
#
# Unlike rate_limit/redis_client.py (built once per long-lived API process
# and never touched again), this client is used from
# orchestration/run_recorder.py, which a Celery worker process reaches via
# worker/tasks.py's generate_report - and that task body wraps its work in a
# fresh asyncio.run() call every single time (see generate_report's own
# docstring), a brand-new event loop each call. That's exactly the shape of
# bug db/session.py's engine singleton has to guard against already: a client
# built during one asyncio.run() call is bound to that call's loop, so the
# next task's own asyncio.run() call would hand back a client whose
# connections belong to an already-closed loop and fail with "Future
# attached to a different loop". generate_report() disposes this client in
# the same finally block, inside the same asyncio.run() call, that already
# disposes db/session.py's engine for the identical reason - see
# worker/tasks.py.
_redis_client: Optional[Redis] = None


def get_events_redis_client() -> Redis:
    """Returns the process-wide async Redis client backing progress-event
    pub/sub, creating it on first use."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(config.EVENTS_REDIS_URL, decode_responses=True)
    return _redis_client


async def dispose_events_redis_client() -> None:
    """Closes the process-wide client and clears the singleton, so a later
    get_events_redis_client() call builds a fresh one rather than reusing a
    closed connection pool. Mirrors db/session.py's dispose_engine() and
    rate_limit/redis_client.py's dispose_redis_client()."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None
