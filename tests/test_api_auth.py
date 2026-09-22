from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import server
from auth.tokens import TokenType, create_access_token, create_refresh_token
from config import config
from db.constants import DEMO_USER_ID

# The plaintext credential alembic/versions/45ed0d458cdf_seed_demo_user_login_password.py
# hashes and stores for DEMO_USER_ID - see that migration for why this can't be
# imported from application code instead.
DEMO_USER_EMAIL = "demo@realestateapp.local"
DEMO_USER_PASSWORD = "demo-password-123"


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _expired_token(user_id=DEMO_USER_ID, token_type: TokenType = TokenType.ACCESS) -> str:
    """A token that jwt.decode() will reject on `exp` alone - everything
    else about it (signature, claim shape) is otherwise valid, so this
    isolates the expiry check from every other way a token can be
    rejected."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": token_type.value,
        "iat": now - timedelta(minutes=30),
        "exp": now - timedelta(minutes=1),
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


@pytest.mark.asyncio
async def test_login_with_valid_credentials_returns_token_pair(db_session, client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_USER_EMAIL, "password": DEMO_USER_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"

    access_claims = jwt.decode(
        body["access_token"], config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    assert access_claims["sub"] == str(DEMO_USER_ID)
    assert access_claims["type"] == "access"

    refresh_claims = jwt.decode(
        body["refresh_token"], config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    assert refresh_claims["type"] == "refresh"


@pytest.mark.asyncio
async def test_login_with_wrong_password_returns_401(db_session, client):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_USER_EMAIL, "password": "not-the-real-password"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_with_unknown_email_returns_401_with_same_message_as_wrong_password(
    db_session, client
):
    """The two failure modes (no such account vs. wrong password) must be
    indistinguishable to the caller - a different message or status code
    for either one would let a caller enumerate which emails have accounts
    at all."""
    unknown_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "whatever"},
    )
    wrong_password_response = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_USER_EMAIL, "password": "not-the-real-password"},
    )
    assert unknown_response.status_code == 401
    assert wrong_password_response.status_code == 401
    assert unknown_response.json()["detail"] == wrong_password_response.json()["detail"]


@pytest.mark.asyncio
async def test_login_rejects_missing_password_with_422(db_session, client):
    response = await client.post("/api/v1/auth/login", json={"email": DEMO_USER_EMAIL})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_refresh_with_valid_refresh_token_returns_new_access_token(db_session, client):
    refresh_token = create_refresh_token(DEMO_USER_ID)
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"

    claims = jwt.decode(
        body["access_token"], config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    assert claims["sub"] == str(DEMO_USER_ID)
    assert claims["type"] == "access"
    # Refresh deliberately does not rotate/return a new refresh token - see
    # api/v1/auth.py's refresh() docstring.
    assert "refresh_token" not in body


@pytest.mark.asyncio
async def test_refresh_rejects_an_access_token_with_401(db_session, client):
    """An access token is structurally a valid, signed JWT too - only the
    `type` claim tells the two apart. Without that check, an access token
    could be used to mint a fresh one of itself forever, defeating its own
    short expiry."""
    access_token = create_access_token(DEMO_USER_ID)
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": access_token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_rejects_an_expired_refresh_token_with_401(db_session, client):
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": _expired_token(token_type=TokenType.REFRESH)}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_expired_access_token_rejected_on_a_protected_route_with_401(db_session, client):
    response = await client.get(
        "/api/v1/reports",
        headers={"Authorization": f"Bearer {_expired_token()}"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_then_use_access_token_end_to_end(db_session, client):
    """The full loop a real client goes through: log in, then use the
    returned access token against a protected route with no other setup."""
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": DEMO_USER_EMAIL, "password": DEMO_USER_PASSWORD},
    )
    access_token = login_response.json()["access_token"]

    reports_response = await client.get(
        "/api/v1/reports", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert reports_response.status_code == 200


@pytest.mark.asyncio
async def test_swagger_openapi_includes_auth_routes(client):
    openapi_response = await client.get("/openapi.json")
    paths = openapi_response.json()["paths"]
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/refresh" in paths
