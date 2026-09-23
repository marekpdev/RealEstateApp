import asyncio
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import server
from auth.tokens import create_access_token
from db.constants import DEMO_USER_ID
from rate_limit import (
    RATE_LIMIT_LIMIT_HEADER,
    RATE_LIMIT_REMAINING_HEADER,
    RATE_LIMIT_RESET_HEADER,
)


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth_headers(user_id=DEMO_USER_ID) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture
def small_bucket():
    """A capacity of 3, with a refill rate slow enough that none of it
    refills mid-test (real time, not mocked - the token bucket script reads
    Redis's own TIME command, not anything Python-side patchable) - small
    enough to exhaust deterministically in a handful of requests."""
    with (
        patch("rate_limit.token_bucket.config.RATE_LIMIT_BUCKET_CAPACITY", 3),
        patch("rate_limit.token_bucket.config.RATE_LIMIT_REFILL_PER_SECOND", 0.01),
    ):
        yield


@pytest.mark.asyncio
async def test_requests_within_capacity_succeed_with_informational_headers(
    db_session, small_bucket, client
):
    response = await client.get("/api/v1/reports", headers=_auth_headers())

    assert response.status_code == 200
    assert response.headers[RATE_LIMIT_LIMIT_HEADER] == "3"
    assert response.headers[RATE_LIMIT_REMAINING_HEADER] == "2"
    assert int(response.headers[RATE_LIMIT_RESET_HEADER]) >= 0


@pytest.mark.asyncio
async def test_exceeding_capacity_returns_429_with_retry_after_and_rate_limit_headers(
    db_session, small_bucket, client
):
    headers = _auth_headers()

    for _ in range(3):
        response = await client.get("/api/v1/reports", headers=headers)
        assert response.status_code == 200

    response = await client.get("/api/v1/reports", headers=headers)

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded."
    assert response.headers[RATE_LIMIT_REMAINING_HEADER] == "0"
    assert response.headers[RATE_LIMIT_LIMIT_HEADER] == "3"
    assert int(response.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_bucket_refills_over_time(db_session, client):
    """A real elapsed-time test, not a mocked clock: the token bucket's
    Lua script reads Redis's own TIME command (see rate_limit/token_bucket.py's
    own docstring on why), so there is no Python-side clock to patch -
    proving a genuine refill means genuinely waiting."""
    with (
        patch("rate_limit.token_bucket.config.RATE_LIMIT_BUCKET_CAPACITY", 1),
        patch("rate_limit.token_bucket.config.RATE_LIMIT_REFILL_PER_SECOND", 20.0),
    ):
        headers = _auth_headers()

        first = await client.get("/api/v1/reports", headers=headers)
        assert first.status_code == 200

        immediately_after = await client.get("/api/v1/reports", headers=headers)
        assert immediately_after.status_code == 429

        await asyncio.sleep(0.1)  # 20 tokens/sec * 0.1s = ~2 tokens refilled

        after_waiting = await client.get("/api/v1/reports", headers=headers)
        assert after_waiting.status_code == 200


@pytest.mark.asyncio
async def test_rate_limit_is_scoped_per_identity(db_session, small_bucket, client):
    """Exhausting one identity's bucket must not affect a different
    identity's - each gets its own key (rate_limit/token_bucket.py's
    f"ratelimit:{identity}"), the same per-caller isolation
    config.SERVICE_API_KEYS/JWT-based ownership scoping already guarantees
    elsewhere in this API."""
    first_user = DEMO_USER_ID
    second_user = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, hashed_password) VALUES (:id, :email, :pw)"),
        {"id": second_user, "email": f"{second_user}@example.com", "pw": "unusable"},
    )
    await db_session.commit()

    for _ in range(3):
        response = await client.get("/api/v1/reports", headers=_auth_headers(first_user))
        assert response.status_code == 200
    exhausted = await client.get("/api/v1/reports", headers=_auth_headers(first_user))
    assert exhausted.status_code == 429

    still_fresh = await client.get("/api/v1/reports", headers=_auth_headers(second_user))
    assert still_fresh.status_code == 200


@pytest.mark.asyncio
async def test_unauthenticated_requests_are_never_rate_limited_themselves(
    db_session, small_bucket, client
):
    """Auth resolves before rate limiting in the Depends() chain (see
    rate_limit/dependency.py's own docstring) - hammering the endpoint with
    no credentials at all must keep answering 401, never 429, since there
    is no identity yet to key a bucket on."""
    for _ in range(5):
        response = await client.get("/api/v1/reports")
        assert response.status_code == 401
