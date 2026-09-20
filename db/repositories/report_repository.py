import uuid
from typing import List, Optional

from sqlalchemy import select

from db.models import Report
from db.repositories.base import BaseRepository


class ReportRepository(BaseRepository):
    async def get_by_request_id(self, request_id: uuid.UUID) -> Optional[Report]:
        return await self.session.scalar(
            select(Report).where(Report.request_id == request_id)
        )

    async def create(
        self,
        *,
        request_id: uuid.UUID,
        content: str,
        total_listings: Optional[int] = None,
        average_price: Optional[float] = None,
        median_price: Optional[float] = None,
        highest_listing: Optional[float] = None,
        lowest_listing: Optional[float] = None,
        raw_properties: Optional[List[dict]] = None,
    ) -> Report:
        """A plain INSERT - unlike InvestmentRequest.create_idempotent(),
        there is no replay case to arbitrate here. A report is only ever
        created once, from run_recorder.execute_and_record()'s success path,
        after that same request has already been claimed idempotently; a
        second call for the same request_id would violate reports'
        UNIQUE(request_id) constraint and should fail loudly as a bug, not
        be caught and silently treated as a duplicate."""
        report = Report(
            request_id=request_id,
            content=content,
            total_listings=total_listings,
            average_price=average_price,
            median_price=median_price,
            highest_listing=highest_listing,
            lowest_listing=lowest_listing,
            raw_properties=raw_properties if raw_properties is not None else [],
        )
        self.session.add(report)
        await self.session.flush()
        return report
