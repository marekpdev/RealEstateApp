import uuid
from typing import Optional

from sqlalchemy import select

from db.models import User
from db.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> Optional[User]:
        """Used by POST /api/v1/auth/login - the only place a user is
        looked up by anything other than their id, since there's no
        self-registration flow to have created a login-by-email habit
        anywhere else."""
        return await self.session.scalar(select(User).where(User.email == email))
