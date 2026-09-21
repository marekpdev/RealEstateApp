import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, InvestmentRequest, Report, User
from db.repositories import (
    AgentRunRepository,
    InvestmentRequestRepository,
    ReportRepository,
)
from db.session import session_scope


def _unique_key() -> str:
    return f"repo-test-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_create_idempotent_inserts_new_row(db_session):
    repo = InvestmentRequestRepository(db_session)
    key = _unique_key()

    row, created = await repo.create_idempotent(
        user_id=DEMO_USER_ID, idempotency_key=key, city="Austin", budget="$500k"
    )

    assert created is True
    assert row.city == "Austin"
    persisted = await db_session.get(InvestmentRequest, row.id)
    assert persisted is not None


@pytest.mark.asyncio
async def test_create_idempotent_duplicate_key_rejected_and_replayed(db_session):
    """The core idempotency behaviour: a second call with the same
    (user_id, idempotency_key) does not raise and does not insert a second
    row - it returns the first row with created=False.

    Each call runs in its own session_scope(), exactly as a real caller
    would (e.g. two separate API requests) - never two create_idempotent()
    calls sharing one still-open session. db_session's own fixture rebind
    makes session_scope() here join the same isolated transaction, but each
    call still gets its own SAVEPOINT and commit boundary. Calling
    create_idempotent() twice on the *same* uncommitted session instead
    would hit the trap create_idempotent()'s own docstring warns about: the
    second call's rollback() would discard the first call's still-uncommitted
    insert too, since both would share one savepoint.
    """
    key = _unique_key()

    async with session_scope() as session:
        first, first_created = await InvestmentRequestRepository(session).create_idempotent(
            user_id=DEMO_USER_ID, idempotency_key=key, city="Austin", budget="$500k"
        )

    async with session_scope() as session:
        second, second_created = await InvestmentRequestRepository(session).create_idempotent(
            user_id=DEMO_USER_ID, idempotency_key=key, city="A different city", budget="$1M"
        )

    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert second.city == "Austin"  # the replay's own (different) args never got written

    count = await db_session.scalar(
        select(func.count()).select_from(InvestmentRequest).where(
            InvestmentRequest.idempotency_key == key
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_create_idempotent_same_key_allowed_for_different_user(db_session):
    """The UNIQUE constraint is scoped to (user_id, idempotency_key), not the
    key alone - two different users reusing the same key independently must
    both succeed as fresh inserts."""
    other_user = User(email=f"{uuid.uuid4()}@example.com", hashed_password="x")
    db_session.add(other_user)
    await db_session.flush()

    repo = InvestmentRequestRepository(db_session)
    key = _unique_key()

    row_a, created_a = await repo.create_idempotent(
        user_id=DEMO_USER_ID, idempotency_key=key, city="Austin", budget="$500k"
    )
    row_b, created_b = await repo.create_idempotent(
        user_id=other_user.id, idempotency_key=key, city="Denver", budget="$250k"
    )

    assert created_a is True
    assert created_b is True
    assert row_a.id != row_b.id


@pytest.mark.asyncio
async def test_create_idempotent_concurrent_claims_yield_one_insert_one_replay(db_engine):
    """The real proof: two genuinely concurrent transactions, on separate
    connections, racing to claim the same idempotency key. Uses db_engine
    directly (not the db_session isolation fixture) with its own
    sessionmaker, because the isolation fixture is one connection with one
    session_scope()-rebound sessionmaker - it can't exercise two independent
    connections actually racing each other at the database level. Cleans up
    its own rows afterward since anything committed here is durable."""
    key = _unique_key()
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)

    async def claim() -> bool:
        async with sessionmaker() as session:
            repo = InvestmentRequestRepository(session)
            _, created = await repo.create_idempotent(
                user_id=DEMO_USER_ID, idempotency_key=key, city="Austin", budget="$500k"
            )
            await session.commit()
            return created

    try:
        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]

        async with sessionmaker() as session:
            count = await session.scalar(
                select(func.count()).select_from(InvestmentRequest).where(
                    InvestmentRequest.idempotency_key == key
                )
            )
        assert count == 1
    finally:
        async with sessionmaker() as cleanup_session:
            await cleanup_session.execute(
                delete(InvestmentRequest).where(InvestmentRequest.idempotency_key == key)
            )
            await cleanup_session.commit()


@pytest.mark.asyncio
async def test_cascade_delete_removes_report_and_agent_runs(db_session):
    request = InvestmentRequest(
        user_id=DEMO_USER_ID,
        idempotency_key=_unique_key(),
        status=JobStatus.COMPLETED,
        city="Austin",
        budget="$500k",
    )
    db_session.add(request)
    await db_session.flush()

    db_session.add(Report(request_id=request.id, content="report body"))
    db_session.add(
        AgentRun(request_id=request.id, node_name="market_data_agent", status=JobStatus.COMPLETED)
    )
    await db_session.flush()

    await db_session.delete(request)
    await db_session.flush()

    report_repo = ReportRepository(db_session)
    agent_run_repo = AgentRunRepository(db_session)
    assert await report_repo.get_by_request_id(request.id) is None
    assert await agent_run_repo.list_by_request_id(request.id) == []


@pytest.mark.asyncio
async def test_agent_run_upsert_inserts_then_updates_without_clobbering(db_session):
    request = InvestmentRequest(
        user_id=DEMO_USER_ID,
        idempotency_key=_unique_key(),
        status=JobStatus.RUNNING,
        city="Austin",
        budget="$500k",
    )
    db_session.add(request)
    await db_session.flush()

    repo = AgentRunRepository(db_session)

    first = await repo.upsert(
        request_id=request.id,
        node_name="market_data_agent",
        status=JobStatus.RUNNING,
        output={"partial": True},
    )
    assert first.status == JobStatus.RUNNING
    assert first.output == {"partial": True}

    second = await repo.upsert(
        request_id=request.id,
        node_name="market_data_agent",
        status=JobStatus.COMPLETED,
    )

    assert second.id == first.id
    assert second.status == JobStatus.COMPLETED
    # A partial upsert (status only) must not have wiped the output the
    # earlier, fuller call already set.
    assert second.output == {"partial": True}

    rows = await repo.list_by_request_id(request.id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_increment_attempt_count_is_a_plain_atomic_increment(db_session):
    """A bare UPDATE ... SET attempt_count = attempt_count + 1, not a
    read-then-write - calling it three times against the same row must
    land on 3, matching the row's default of 0 at creation."""
    request = InvestmentRequest(
        user_id=DEMO_USER_ID,
        idempotency_key=_unique_key(),
        status=JobStatus.PENDING,
        city="",
        budget="",
    )
    db_session.add(request)
    await db_session.flush()
    assert request.attempt_count == 0

    repo = InvestmentRequestRepository(db_session)
    for _ in range(3):
        await repo.increment_attempt_count(request.id)
    await db_session.flush()

    persisted = await db_session.get(InvestmentRequest, request.id, populate_existing=True)
    assert persisted.attempt_count == 3
