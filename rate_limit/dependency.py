import math

from fastapi import Depends, HTTPException, Response, status

from auth.api_keys import get_current_caller
from db.models import User
from rate_limit.redis_client import get_redis_client
from rate_limit.token_bucket import consume_token

RATE_LIMIT_LIMIT_HEADER = "X-RateLimit-Limit"
RATE_LIMIT_REMAINING_HEADER = "X-RateLimit-Remaining"
RATE_LIMIT_RESET_HEADER = "X-RateLimit-Reset"


async def enforce_rate_limit(
    response: Response,
    current_user: User = Depends(get_current_caller),
) -> None:
    """A FastAPI dependency, not a Starlette-level ASGI middleware: keying
    the bucket per authenticated identity needs the identity itself,
    which doesn't exist until get_current_caller has already run -
    decoded a JWT or matched an API key, then looked the caller up in
    Postgres. Starlette middleware runs ahead of, and independently of,
    FastAPI's own dependency-injection system; a raw ASGI middleware
    wanting this same behavior would have to re-implement (or import and
    call by hand) the exact JWT/API-key resolution logic auth/api_keys.py
    and auth/dependencies.py already own - a second, independent copy of
    "how do I know who this caller is" that could quietly drift from the
    first. Declaring this as a Depends() that itself depends on
    get_current_caller reuses that resolution unchanged instead: FastAPI
    caches a dependency's result per request by default, so a route that
    also takes its own `current_user: User = Depends(get_current_caller)`
    parameter (as every /api/v1/reports route already does) triggers only
    one real resolution, not two - see api/v1/reports.py's own routes,
    which declare this dependency after current_user for exactly that
    reason, and after _require_persistence, for the same left-to-right
    Depends() ordering already required for get_current_caller itself: an
    environment with DB_PERSISTENCE_ENABLED=false must still answer 503
    without this dependency's own get_current_caller sub-dependency ever
    touching the database.

    A failed Redis call here is never caught and turned into an "allow" -
    this app's own "fail fast, loudly" operational posture, already
    established for an unreachable database, applies exactly the same way
    to an unreachable rate limiter: a limiter that quietly
    lets every request through the moment its backing store has a problem
    provides no protection precisely when protection matters most, and
    Redis is already a hard dependency for every one of these routes today
    regardless (report creation enqueues onto a Celery broker that is
    itself Redis). An unhandled exception here surfaces as a 500, the same
    as any other unexpected failure in this dependency chain.
    """
    result = await consume_token(get_redis_client(), str(current_user.id))

    response.headers[RATE_LIMIT_LIMIT_HEADER] = str(result.limit)
    response.headers[RATE_LIMIT_REMAINING_HEADER] = str(result.remaining)
    # "Reset" here means "seconds until the bucket is back at full capacity"
    # - the honest equivalent for a continuously-refilling bucket, which
    # (unlike a fixed window) has no single moment a counter snaps back to
    # zero all at once. 0 when the bucket is already full.
    response.headers[RATE_LIMIT_RESET_HEADER] = str(math.ceil(result.seconds_until_full))

    if not result.allowed:
        # At least 1 second: a Retry-After of "0" would tell a well-behaved
        # client to retry immediately, which - against a bucket that has
        # already been drained to below 1 token - would almost always fail
        # again instantly, defeating the header's entire purpose.
        retry_after = max(1, math.ceil(result.retry_after_seconds))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded.",
            headers={
                "Retry-After": str(retry_after),
                RATE_LIMIT_LIMIT_HEADER: str(result.limit),
                RATE_LIMIT_REMAINING_HEADER: "0",
                RATE_LIMIT_RESET_HEADER: str(retry_after),
            },
        )
