import uuid
from typing import Optional

from sqlalchemy import select

from db.models import Report
from db.repositories.base import BaseRepository


class ReportRepository(BaseRepository):
    async def get_by_request_id(self, request_id: uuid.UUID) -> Optional[Report]:
        return await self.session.scalar(
            select(Report).where(Report.request_id == request_id)
        )
