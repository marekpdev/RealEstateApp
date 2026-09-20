import uuid
from typing import Optional, Tuple

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from db.enums import JobStatus
from db.models import InvestmentRequest
from db.repositories.base import BaseRepository


class InvestmentRequestRepository(BaseRepository):
    async def get_by_id(self, request_id: uuid.UUID) -> Optional[InvestmentRequest]:
        return await self.session.get(InvestmentRequest, request_id)

    async def create_idempotent(
        self,
        *,
        user_id: uuid.UUID,
        idempotency_key: str,
        city: str,
        budget: str,
        status: JobStatus = JobStatus.PENDING,
    ) -> Tuple[InvestmentRequest, bool]:
        """Attempts the INSERT and lets the UNIQUE(user_id, idempotency_key)
        constraint arbitrate. A SELECT-then-INSERT check-then-act sequence is
        a TOCTOU race under concurrency: two callers can both see "no
        existing row" for the same key and both insert, since a plain SELECT
        takes no lock a concurrent transaction is obliged to respect. Only
        the database itself, via the constraint it already enforces on every
        write, can arbitrate atomically across two genuinely concurrent
        transactions.

        Returns (row, created) - created=False means a row for this
        (user_id, idempotency_key) already existed and this call is a
        replay: the row returned is the pre-existing one, and no write
        happened.

        Must be the only thing this session's transaction does (see
        BaseRepository's docstring): on a conflict this issues
        session.rollback(), which discards every pending change on the
        session, not just this insert.
        """
        request = InvestmentRequest(
            user_id=user_id,
            idempotency_key=idempotency_key,
            status=status,
            city=city,
            budget=budget,
        )
        self.session.add(request)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(InvestmentRequest).where(
                    InvestmentRequest.user_id == user_id,
                    InvestmentRequest.idempotency_key == idempotency_key,
                )
            )
            return existing, False
        return request, True

    async def update_status(self, request_id: uuid.UUID, status: JobStatus) -> None:
        """A plain UPDATE, not a SELECT-then-mutate-then-flush - the caller
        (orchestration/run_recorder.py) only has the id, and this is called
        as its own short transaction rather than alongside other pending
        writes, so there's nothing to gain from loading the row first."""
        await self.session.execute(
            update(InvestmentRequest)
            .where(InvestmentRequest.id == request_id)
            .values(status=status)
        )

    async def update_extracted_details(
        self, request_id: uuid.UUID, *, city: str, budget: str
    ) -> None:
        """Backfills city/budget once ingest_input_agent has extracted them.
        These columns are NOT NULL but the job is claimed before
        the graph runs a single node, so the claiming insert writes them as
        empty strings and this call fills in the real values the first time
        they appear in a stream_mode="values" snapshot."""
        await self.session.execute(
            update(InvestmentRequest)
            .where(InvestmentRequest.id == request_id)
            .values(city=city, budget=budget)
        )
