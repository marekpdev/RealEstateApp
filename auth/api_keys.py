import secrets
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from fastapi.security.http import HTTPAuthorizationCredentials

from auth.dependencies import bearer_scheme, get_current_user
from config import config
from db.constants import DEMO_USER_ID
from db.models import User
from db.repositories import UserRepository
from db.session import session_scope

# X-API-Key, not a second Authorization scheme: Authorization is already
# spoken for by the Bearer JWT this app issues, and mixing two credential
# kinds into one header would need its own ad-hoc parsing. A dedicated
# header is also what most real API-key schemes (Stripe, GitHub PATs-via-
# curl, etc.) do in practice.
API_KEY_HEADER_NAME = "X-API-Key"
_api_key_scheme = APIKeyHeader(name=API_KEY_HEADER_NAME, auto_error=False)


async def get_current_caller(
    api_key: Optional[str] = Depends(_api_key_scheme),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    """Protects the same routes get_current_user does, but accepts either
    credential: an X-API-Key header (a machine caller - a script, a cron
    job, a partner integration, authenticating as the one seeded demo
    account) or an Authorization: Bearer JWT (a human, via the login flow).

    An API key, if present, is checked first - it's the cheaper,
    self-contained check (a constant-time string comparison against a
    fixed set; no signature verification, no expiry math, no "is this a
    refresh token used where an access token belongs" check) - and a
    request presenting a *valid* one never touches JWT handling at all.
    A request presenting no key, or an invalid one, falls through to
    get_current_user unchanged, including its exact 401s: an API key is
    an alternative credential, not a security boundary lower than JWT's,
    so a wrong key must not leak "there is a key scheme and yours just
    didn't match it" any more precisely than a wrong JWT does.
    """
    if api_key is not None:
        # secrets.compare_digest, not `in`/`==`: a plain membership or
        # equality check short-circuits on the first differing byte, so
        # how long the comparison takes leaks how many leading characters
        # of a guess were correct - the same timing side-channel
        # bcrypt.checkpw() (auth/passwords.py) and JWT signature
        # verification are already immune to by construction. Checked
        # against every configured key, not just the first, so the total
        # time doesn't itself reveal which key (if any) a guess is closest
        # to.
        is_valid = any(
            secrets.compare_digest(api_key, valid_key) for valid_key in config.SERVICE_API_KEYS
        )
        if not is_valid:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")

        async with session_scope() as session:
            user = await UserRepository(session).get_by_id(DEMO_USER_ID)
            if user is None:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, detail="Service account no longer exists."
                )
            return user

    return await get_current_user(credentials)
