import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import server

# Matches conftest.py's os.environ.setdefault("CORS_ALLOWED_ORIGINS", ...) -
# baked into server.raw_app's CORSMiddleware at import time, so these tests
# assert against that exact value rather than trying to reconfigure the
# already-constructed middleware per test.
ALLOWED_ORIGIN = "https://allowed.example.com"
DISALLOWED_ORIGIN = "https://not-allowed.example.com"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _preflight_headers(origin: str) -> dict:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization",
    }


@pytest.mark.asyncio
async def test_preflight_from_allowed_origin_succeeds(client):
    response = await client.options("/api/v1/reports", headers=_preflight_headers(ALLOWED_ORIGIN))

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_preflight_from_disallowed_origin_is_rejected(client):
    response = await client.options(
        "/api/v1/reports", headers=_preflight_headers(DISALLOWED_ORIGIN)
    )

    # Starlette's CORSMiddleware answers a disallowed preflight with 400
    # rather than silently omitting the header (see its own
    # preflight_response()) - more informative for debugging, though what
    # actually stops the browser acting on the response either way is the
    # missing Access-Control-Allow-Origin header, not this status code.
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.asyncio
async def test_non_preflight_request_from_disallowed_origin_gets_no_cors_header(client):
    # A plain (non-OPTIONS) cross-origin request still executes server-side
    # - CORS never blocks the request itself, only whether a browser lets
    # the calling page's JavaScript read the response. Confirms the actual
    # response also carries no Access-Control-Allow-Origin for a
    # disallowed origin, matching the preflight's own verdict.
    response = await client.get("/api/v1/reports", headers={"Origin": DISALLOWED_ORIGIN})

    assert "access-control-allow-origin" not in response.headers
