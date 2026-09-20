import uuid
from typing import Optional

from sqlalchemy import select

from db.models import User
from db.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get_by_id(self, user_id: uuid.UUID) -> Optional[User]:
        return await self.session.get(User, user_id)
