from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import server
from auth.api_keys import API_KEY_HEADER_NAME

# Matches conftest.py's os.environ.setdefault("SERVICE_API_KEYS", ...) -
# baked into config.SERVICE_API_KEYS at import time.
VALID_API_KEY = "test-service-key"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_valid_api_key_authenticates_a_report_list_without_any_jwt(db_session, client):
    response = await client.get("/api/v1/reports", headers={API_KEY_HEADER_NAME: VALID_API_KEY})

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


@pytest.mark.asyncio
async def test_invalid_api_key_is_rejected_with_401(db_session, client):
    response = await client.get(
        "/api/v1/reports", headers={API_KEY_HEADER_NAME: "not-a-real-key"}
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid API key."


@pytest.mark.asyncio
async def test_no_api_key_and_no_jwt_falls_through_to_the_usual_401(db_session, client):
    response = await client.get("/api/v1/reports")

    assert response.status_code == 401
    assert response.json()["detail"] == "Not authenticated."


@pytest.mark.asyncio
async def test_valid_api_key_can_create_and_then_fetch_a_report(db_session, client):
    with patch("api.v1.reports.generate_report"):
        create_response = await client.post(
            "/api/v1/reports",
            json={"query": "Invest in Austin, TX up to $750,000"},
            headers={
                API_KEY_HEADER_NAME: VALID_API_KEY,
                "Idempotency-Key": "api-key-created-report",
            },
        )
    assert create_response.status_code == 202
    request_id = create_response.json()["id"]

    get_response = await client.get(
        f"/api/v1/reports/{request_id}", headers={API_KEY_HEADER_NAME: VALID_API_KEY}
    )
    assert get_response.status_code == 200
    # An API key authenticates as the same seeded demo user a JWT would -
    # there's only one account in this system, and an API key is a second
    # way for a caller to prove it's acting as that account, not a
    # separate identity with its own data.
    assert get_response.json()["id"] == request_id
