import httpx
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
from chainlit.utils import mount_chainlit
from sqlalchemy import text

from config import config
from db.session import dispose_engine, get_engine
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


raw_app = FastAPI(title="Real Estate Agentic System API", lifespan=lifespan)

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

try:
    mount_chainlit(app=raw_app, target="app.py", path="")
except Exception as e:
    print(f"⚠️ Chainlit mounting context deferred: {e}")

app = raw_app
