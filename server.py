import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
from chainlit.utils import mount_chainlit

from services.market_data_gateway import RapidRealEstateMarketClient

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manages centralized resource context boundaries.
    Initializes a shared HTTPX connection pool on boot and tears it down on close.
    """
    # Create single long-lived network socket pool instance
    async_client_pool = httpx.AsyncClient()

    # Instantiate child client and inject the shared pool
    app.state.real_estate_gateway = RapidRealEstateMarketClient(client=async_client_pool)

    yield
    # Server Tear Down Sequence
    await async_client_pool.aclose()


raw_app = FastAPI(title="Real Estate Agentic System API", lifespan=lifespan)

@raw_app.get("/health", tags=["Infrastructure Monitoring"])
async def health_check():
    return {"status": "healthy"}

try:
    mount_chainlit(app=raw_app, target="app.py", path="")
except Exception as e:
    print(f"⚠️ Chainlit mounting context deferred: {e}")

app = raw_app
