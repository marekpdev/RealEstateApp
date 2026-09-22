import uuid
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from auth.tokens import TokenType, decode_token
from db.models import User
from db.repositories import UserRepository
from db.session import session_scope

# auto_error=False: FastAPI's own HTTPBearer, left at its default
# auto_error=True, answers a *missing* Authorization header with 403
# Forbidden - a "not authenticated" case is not what 403 means (403 is
# "authenticated, but not allowed"; the correct code here is 401, matching
# every other way this dependency can fail below). Disabling auto_error and
# checking for None ourselves is what makes "no header at all" answer with
# the same 401 as "expired token" / "malformed token" / etc., rather than a
# fourth, inconsistent status code.
# Not module-private: auth/api_keys.py's get_current_caller() reuses this
# exact instance so a request already screened there for an API key falls
# through to the same bearer-parsing behaviour, rather than a second,
# independently-configured HTTPBearer that could drift from this one.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    """FastAPI dependency protecting every report route. Resolves the
    Authorization: Bearer <token> header into the User row it names, or
    raises 401 - never 403 or 404 - for every way that can fail (missing,
    expired, malformed, wrong token type, unknown user), matching this app's
    existing anti-enumeration posture (see InvestmentRequestRepository.
    get_by_id_for_user()'s own docstring): none of these responses should
    tell an attacker *why* a token didn't work.
    """
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated.")

    try:
        claims = decode_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Access token expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid access token.")

    if claims.get("type") != TokenType.ACCESS.value:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not an access token.")

    try:
        user_id = uuid.UUID(claims["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Malformed access token.")

    async with session_scope() as session:
        user = await UserRepository(session).get_by_id(user_id)
        if user is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User no longer exists.")
        return user
