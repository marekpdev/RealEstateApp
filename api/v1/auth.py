import uuid

import jwt
from fastapi import APIRouter, HTTPException, status

from api.v1.schemas import (
    AccessTokenResponse,
    LoginRequest,
    RefreshRequest,
    TokenPair,
)
from auth.passwords import verify_password
from auth.tokens import TokenType, create_access_token, create_refresh_token, decode_token
from db.repositories import UserRepository
from db.session import session_scope

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Exchange email/password for an access + refresh token pair",
    responses={401: {"description": "Incorrect email or password"}},
)
async def login(payload: LoginRequest) -> TokenPair:
    async with session_scope() as session:
        user = await UserRepository(session).get_by_email(payload.email)
        # Same 401, same detail message, whether the email doesn't exist at
        # all or exists with a different password - a distinguishable error
        # for either case would let a caller enumerate which emails have
        # accounts (the same anti-enumeration principle
        # get_by_id_for_user()'s 404-not-403 choice already applies to
        # report ownership, applied here to login).
        if user is None or not verify_password(payload.password, user.hashed_password):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password."
            )
        user_id = user.id

    return TokenPair(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id),
    )


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Exchange a still-valid refresh token for a new access token",
    responses={401: {"description": "Refresh token missing, expired, or invalid"}},
)
async def refresh(payload: RefreshRequest) -> AccessTokenResponse:
    """Issues a new access token without re-prompting for a password, as
    long as the refresh token itself is still valid. Deliberately does not
    rotate the refresh token (return a new one and invalidate the old): this
    app's tokens are stateless (nothing server-side tracks which ones have
    been issued, see auth/tokens.py), so there is nothing to actually mark
    the old refresh token invalid with - handing back a "new" one while the
    old one keeps working too would just be theater. Real rotation-with-
    revocation needs a server-side record per refresh token (e.g. a Redis
    entry keyed by a jti claim, checked and burned on every use) - deliberately
    out of scope: a stolen refresh token remains valid until its own expiry,
    which is why it's short-lived relative to a session and why the access
    token it mints is itself short-lived too.
    """
    try:
        claims = decode_token(payload.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    if claims.get("type") != TokenType.REFRESH.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not a refresh token.")

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Malformed refresh token.")

    async with session_scope() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User no longer exists.")

    return AccessTokenResponse(access_token=create_access_token(user_id))
