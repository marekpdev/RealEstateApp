import hashlib
import uuid
from typing import List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from db.enums import JobStatus
from db.models import InvestmentRequest
from db.repositories.base import BaseRepository


def hash_request_payload(raw_query: str) -> str:
    """SHA-256 hex digest of the free-text query a caller submitted.
    create_idempotent() stores this alongside the idempotency key it's
    claimed with, and a later caller reusing the same key can hash its own
    raw_query the same way to check whether it matches what the key was
    first used for - a fixed-size fingerprint rather than persisting the
    raw text a second time as its own column."""
    return hashlib.sha256(raw_query.encode("utf-8")).hexdigest()


class InvestmentRequestRepository(BaseRepository):
    async def get_by_id(self, request_id: uuid.UUID) -> Optional[InvestmentRequest]:
        return await self.session.get(InvestmentRequest, request_id)

    async def get_by_id_for_user(
        self, request_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[InvestmentRequest]:
        """Same lookup as get_by_id(), scoped to a specific owner. Used by
        the read-only API endpoints (GET /api/v1/reports/{id}) so a request
        id that belongs to a different user is indistinguishable from one
        that doesn't exist at all (a plain get_by_id() would leak existence
        via a 200-vs-404 response difference) - the same 404-not-403 choice
        a future per-user authorization check would need to make explicit
        once there's more than one real user to tell apart."""
        return await self.session.scalar(
            select(InvestmentRequest).where(
                InvestmentRequest.id == request_id,
                InvestmentRequest.user_id == user_id,
            )
        )

    async def list_by_user(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> List[InvestmentRequest]:
        """Newest-first, matching ix_investment_requests_user_id_created_at
        (db/models.py) - the composite index built for exactly this access
        pattern ("this user's requests, paginated, newest first")."""
        result = await self.session.execute(
            select(InvestmentRequest)
            .where(InvestmentRequest.user_id == user_id)
            .order_by(InvestmentRequest.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_user(self, user_id: uuid.UUID) -> int:
        """The full matching count regardless of limit/offset, so a client
        paginating GET /api/v1/reports can compute how many pages remain
        without walking every page first."""
        return await self.session.scalar(
            select(func.count()).select_from(InvestmentRequest).where(
                InvestmentRequest.user_id == user_id
            )
        )

    async def create_idempotent(
        self,
        *,
        user_id: uuid.UUID,
        idempotency_key: str,
        raw_query: str,
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

        raw_query is hashed (hash_request_payload()) and stored as
        request_payload_hash on insert - not compared against on a
        conflict here. Whether a replay's raw_query actually matches what
        the key was first used for is the caller's decision (see
        orchestration.run_recorder.claim_request()'s payload_conflict),
        not this method's: cli.py's own idempotency key (a fresh uuid4 per
        CLI invocation) calls claim_request() directly and ignores a
        mismatch, replaying regardless of payload by design. app.py's
        idempotency key (a Chainlit message id) and every other caller go
        through the HTTP API, which does act on a mismatch - though neither
        entrypoint ever deliberately reuses a key with a different payload,
        so this should never actually trigger for either.

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
            request_payload_hash=hash_request_payload(raw_query),
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

    async def try_claim_run(self, request_id: uuid.UUID) -> bool:
        """Atomically transitions request_id to RUNNING, but only if it is
        currently PENDING or FAILED - the two states from which re-running
        the graph is actually correct (see claim_request()'s own docstring
        in orchestration/run_recorder.py on why a stranded PENDING/FAILED
        row is resumed rather than rejected). A conditional
        UPDATE ... WHERE ... RETURNING, not a read-then-branch: Redis's
        at-least-once task delivery (the broker only drops a message once a
        worker acks it having finished, so a crashed or slow worker gets it
        redelivered) can hand the same Celery message to two workers while
        the first is still mid-run, not only after it has already finished
        - a plain SELECT of the current
        status takes no lock a second, genuinely concurrent caller is
        obliged to respect, the exact TOCTOU shape create_idempotent()'s own
        docstring already warns about. The database's row-level lock on the
        UPDATE is what actually makes this atomic: of two callers racing for
        the same request_id, only one can ever see this return True.

        Returns True if this call is the one that should run the graph.
        False means someone else already owns it (status is RUNNING) or it
        is already done (COMPLETED) - the caller must not touch the graph,
        agent_runs, or reports for this request_id in that case.
        """
        result = await self.session.execute(
            update(InvestmentRequest)
            .where(
                InvestmentRequest.id == request_id,
                InvestmentRequest.status.in_([JobStatus.PENDING, JobStatus.FAILED]),
            )
            .values(status=JobStatus.RUNNING)
            .returning(InvestmentRequest.id)
        )
        return result.first() is not None

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

    async def increment_attempt_count(self, request_id: uuid.UUID) -> None:
        """Called once per physical attempt a Celery task makes at this job -
        the original try and every retry, whether or not the attempt itself
        succeeds - so worker/tasks.py can tell "retried twice, then failed
        for good" apart from "failed on the first and only try". A plain
        atomic UPDATE ... SET attempt_count = attempt_count + 1 rather than
        a read-then-write: Celery guarantees only one worker owns a given
        task attempt at a time, so there's no concurrent writer to race
        against, but the atomic form is free and doesn't rely on that."""
        await self.session.execute(
            update(InvestmentRequest)
            .where(InvestmentRequest.id == request_id)
            .values(attempt_count=InvestmentRequest.attempt_count + 1)
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
