import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict

import jwt

from config import config


class TokenType(str, Enum):
    """Embedded as the token's own "type" claim so a refresh token can never
    be used where an access token is expected, or vice versa - both are
    signed with the same secret and would otherwise decode and verify
    identically. See get_current_user() (auth/dependencies.py) and
    api/v1/auth.py's refresh() for where this is checked."""

    ACCESS = "access"
    REFRESH = "refresh"


def _create_token(user_id: uuid.UUID, token_type: TokenType, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "type": token_type.value,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)


def create_access_token(user_id: uuid.UUID) -> str:
    return _create_token(
        user_id, TokenType.ACCESS, timedelta(minutes=config.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )


def create_refresh_token(user_id: uuid.UUID) -> str:
    return _create_token(
        user_id, TokenType.REFRESH, timedelta(minutes=config.JWT_REFRESH_TOKEN_EXPIRE_MINUTES)
    )


def decode_token(token: str) -> Dict:
    """Verifies the signature and expiry and returns the claims dict.
    Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError (its own
    subclasses cover a bad signature, malformed token, etc.) unmodified -
    callers decide how to turn those into an HTTP response, since a login
    dependency and a refresh endpoint want different error messages for the
    same underlying failures."""
    return jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
