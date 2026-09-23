import math
from dataclasses import dataclass

from redis.asyncio import Redis

from config import config

# KEYS[1]: the bucket's Redis hash key ("ratelimit:<identity>").
# ARGV[1]: capacity - the maximum number of tokens the bucket can ever hold.
# ARGV[2]: refill_per_second - tokens added per second, continuously.
# ARGV[3]: requested - tokens this call wants to spend (always 1 today: one
#          request costs one token; see this module's own docstring for why
#          a per-route weight wasn't built).
# ARGV[4]: the key's TTL in seconds, refreshed on every call.
#
# Runs as one atomic step inside Redis - a Lua script executes to
# completion with no other command interleaved, the same "let the store
# itself arbitrate a read-then-write instead of doing it in application
# code" principle db/repositories.py's create_idempotent() (a unique-
# constraint INSERT) and try_claim_run() (a conditional UPDATE) already
# rely on, applied here to a check-then-decrement. Without that atomicity,
# two concurrent requests from the same identity could both read "1 token
# left", both decide to allow, and both decrement - letting the bucket go
# negative under real concurrency and defeating the entire point of a limit.
#
# Uses Redis's own TIME command for "now", not a timestamp computed in
# Python and passed in as an argument: every API replica sharing this one
# Redis instance then agrees on the same clock, so a clock skew between
# replicas (or between a replica and this process) can never make one
# replica's view of a bucket's fill level disagree with another's. That
# agreement is the entire reason this state lives in Redis instead of each
# replica's own process memory in the first place - a per-process in-memory
# counter is inherently un-shareable the moment there's a second replica.
#
# tokens/last_refill travel across the Lua/Python boundary as strings
# (tostring(...)), not raw Lua numbers: Redis's Lua-to-RESP conversion
# turns a Lua number into an *integer* reply, truncating any fractional
# part - and a continuously-refilling bucket's token count is fractional
# almost all the time (e.g. 0.3 seconds after a request, at 2 tokens/sec,
# there are exactly 0.6 more tokens than there were). Returning the raw
# number would silently truncate that fraction away on every single call.
_TOKEN_BUCKET_SCRIPT = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_per_second = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
local ttl_seconds = tonumber(ARGV[4])

local time = redis.call("TIME")
local now = tonumber(time[1]) + (tonumber(time[2]) / 1000000)

local state = redis.call("HMGET", key, "tokens", "last_refill")
local tokens = tonumber(state[1])
local last_refill = tonumber(state[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = now - last_refill
if elapsed > 0 then
    tokens = math.min(capacity, tokens + (elapsed * refill_per_second))
end

local allowed = 0
if tokens >= requested then
    tokens = tokens - requested
    allowed = 1
end

redis.call("HSET", key, "tokens", tostring(tokens), "last_refill", tostring(now))
redis.call("EXPIRE", key, ttl_seconds)

local retry_after = 0
if allowed == 0 then
    retry_after = (requested - tokens) / refill_per_second
end

return {allowed, tostring(tokens), tostring(retry_after)}
"""


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int  # floor()'d - fractional tokens aren't meaningful to show a caller
    retry_after_seconds: float  # only meaningful when allowed is False
    seconds_until_full: float


async def consume_token(client: Redis, identity: str) -> RateLimitResult:
    """Atomically checks and, if allowed, spends one token from `identity`'s
    bucket, returning enough detail to render both the informational
    X-RateLimit-* headers on every response and the Retry-After header on a
    429.

    Reads capacity/refill/TTL from config on every call, rather than
    freezing them into module-level constants at import time - the same
    "don't bake a config value into something built once" lesson already
    learned from server.py's own CORSMiddleware (whose policy freezes the
    moment server.py is first imported anywhere in a test session), applied
    here to keep this function's behavior genuinely reconfigurable per
    call, in both a real deployment and a test that wants a tighter bucket
    than the default.
    """
    capacity = config.RATE_LIMIT_BUCKET_CAPACITY
    refill_per_second = config.RATE_LIMIT_REFILL_PER_SECOND
    ttl_seconds = config.RATE_LIMIT_KEY_TTL_SECONDS

    allowed_raw, remaining_raw, retry_after_raw = await client.eval(
        _TOKEN_BUCKET_SCRIPT,
        1,
        f"ratelimit:{identity}",
        capacity,
        refill_per_second,
        1,
        ttl_seconds,
    )

    remaining_exact = max(0.0, float(remaining_raw))
    return RateLimitResult(
        allowed=bool(int(allowed_raw)),
        limit=capacity,
        remaining=math.floor(remaining_exact),
        retry_after_seconds=max(0.0, float(retry_after_raw)),
        seconds_until_full=max(0.0, (capacity - remaining_exact) / refill_per_second),
    )
