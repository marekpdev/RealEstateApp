import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from chainlit.utils import mount_chainlit
from sqlalchemy import text

from api.v1 import api_router
from api.v1.reports import IDEMPOTENCY_KEY_HEADER
from auth.api_keys import API_KEY_HEADER_NAME
from config import config
from db.session import dispose_engine, get_engine
from rate_limit import (
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMIT_RESET_HEADER,
)
from rate_limit.redis_client import dispose_redis_client, get_redis_client
from services.market_data_gateway import RapidRealEstateMarketClient

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages centralized resource context boundaries.
    Initializes a shared HTTPX connection pool and warms up the database engine on
    boot, then tears both down on close.
    """
    # Create single long-lived network socket pool instance
    async_client_pool = httpx.AsyncClient()

    # Instantiate child client and inject the shared pool
    app.state.real_estate_gateway = RapidRealEstateMarketClient(client=async_client_pool)

    if config.DB_PERSISTENCE_ENABLED:
        # Fail fast and loudly if the database is unreachable at boot - a process
        # that starts healthy but can't actually persist anything is worse than a
        # crash loop, because nothing signals the problem until the first write.
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))

    # Same fail-fast posture, extended to the rate limiter's own backing
    # store: every /api/v1/reports route depends on it now, so a process
    # that boots healthy while Redis is unreachable would only discover
    # that on the first real request, exactly the failure mode the
    # database check above already exists to avoid.
    await get_redis_client().ping()

    yield
    # Server Tear Down Sequence. Reached on SIGTERM/SIGINT, not just a
    # normal exit: uvicorn's own signal handlers stop it accepting new
    # connections, wait for in-flight ones (including a Chainlit
    # websocket session mid poll_until_terminal()) to close on their own -
    # indefinitely, since timeout_graceful_shutdown defaults to None - and
    # only then run this code. No SIGTERM handling of our own is needed
    # here; the actual ceiling on how long uvicorn will wait lives one
    # level up, in whatever sends the signal (k8s's
    # terminationGracePeriodSeconds / Compose's stop_grace_period - see
    # k8s/deployment.yaml and docker-compose.yml), which SIGKILLs the
    # process outright once its grace period elapses, graceful or not.
    await async_client_pool.aclose()
    if config.DB_PERSISTENCE_ENABLED:
        await dispose_engine()
    await dispose_redis_client()


raw_app = FastAPI(title="Real Estate Agentic System API", lifespan=lifespan)

# CORS is a browser-enforced rule, not a server-side access control: curl,
# httpx, and Postman ignore it entirely, and a stolen token is exactly as
# usable through them with or without this middleware. What it actually
# protects against is a *different* site's JavaScript, running in a
# logged-in user's own browser, silently reading this API's responses on
# their behalf. Only origins named in config.CORS_ALLOWED_ORIGINS get a
# response that lets a browser's fetch()/XHR see the result at all;
# allow_credentials=True (this API's tokens travel in an Authorization
# header, which counts) is exactly why that list can never contain "*" -
# config.py enforces that at import time, not here, so a misconfiguration
# fails at boot, loudly, rather than on the first cross-origin request.
# expose_headers: without it, CORSMiddleware only lets cross-origin
# JavaScript read the handful of headers the Fetch spec calls "simple"
# (Content-Type, Content-Length, etc) - curl/Postman can see every header
# regardless (CORS is enforced by the browser, not the server), but a real
# browser-based client trying to implement backoff against the rate limiter
# would silently be unable to read X-RateLimit-*/Retry-After via fetch()'s
# Response.headers without this.
raw_app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", IDEMPOTENCY_KEY_HEADER, API_KEY_HEADER_NAME],
    expose_headers=[
        RATE_LIMIT_LIMIT_HEADER,
        RATE_LIMIT_REMAINING_HEADER,
        RATE_LIMIT_RESET_HEADER,
        "Retry-After",
    ],
)

@raw_app.get("/health", tags=["Infrastructure Monitoring"])
async def health_check():
    """Liveness probe: is the process up and able to serve requests at all?"""
    return {"status": "healthy"}


@raw_app.get("/health/ready", tags=["Infrastructure Monitoring"])
async def readiness_check():
    """
    Readiness probe: can this instance actually do its job right now? Unlike
    /health, this checks the database on every call - a process can be alive
    (liveness) while unable to serve traffic that needs the database (readiness).
    """
    if not config.DB_PERSISTENCE_ENABLED:
        return {"status": "ready", "database": "disabled"}

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"database unreachable: {e}")

    return {"status": "ready", "database": "reachable"}

# Registered before mount_chainlit(): Starlette matches routes in
# registration order, and mount_chainlit()'s Mount(path="") below matches
# every path as a catch-all. Routes added to raw_app after that mount (the
# health checks above are already safe, since they're defined first too)
# would silently never be reached - the Mount would swallow them first.
raw_app.include_router(api_router)

try:
    mount_chainlit(app=raw_app, target="app.py", path="")
except Exception as e:
    print(f"⚠️ Chainlit mounting context deferred: {e}")

app = raw_app
