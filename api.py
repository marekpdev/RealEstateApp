import httpx
from fastapi import FastAPI
from contextlib import asynccontextmanager
import chainlit as cl
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

app = cl.make_async(raw_app)
mount_chainlit(app=app, target="app.py", slot_path="")