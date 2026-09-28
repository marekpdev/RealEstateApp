import asyncio
import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, DocumentChunk, InvestmentRequest, Report, User
from db.repositories import (
    AgentRunRepository,
    DocumentChunkRepository,
    InvestmentRequestRepository,
    ReportRepository,
    hash_request_payload,
)
from db.session import session_scope


def _unique_key() -> str:
    return f"repo-test-{uuid.uuid4()}"


@pytest.mark.asyncio
async def test_create_idempotent_inserts_new_row(db_session):
    repo = InvestmentRequestRepository(db_session)
    key = _unique_key()

    row, created = await repo.create_idempotent(
        user_id=DEMO_USER_ID, idempotency_key=key, raw_query="Invest in Austin, TX",
        city="Austin", budget="$500k",
    )

    assert created is True
    assert row.city == "Austin"
    assert row.request_payload_hash == hash_request_payload("Invest in Austin, TX")
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
            user_id=DEMO_USER_ID, idempotency_key=key, raw_query="Invest in Austin, TX",
            city="Austin", budget="$500k",
        )

    async with session_scope() as session:
        second, second_created = await InvestmentRequestRepository(session).create_idempotent(
            user_id=DEMO_USER_ID, idempotency_key=key, raw_query="Invest in Austin, TX",
            city="A different city", budget="$1M",
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
        user_id=DEMO_USER_ID, idempotency_key=key, raw_query="Invest in Austin, TX",
        city="Austin", budget="$500k",
    )
    row_b, created_b = await repo.create_idempotent(
        user_id=other_user.id, idempotency_key=key, raw_query="Invest in Denver, CO",
        city="Denver", budget="$250k",
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
                user_id=DEMO_USER_ID, idempotency_key=key, raw_query="Invest in Austin, TX",
                city="Austin", budget="$500k",
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
async def test_try_claim_run_succeeds_from_pending_and_failed(db_session):
    """The two states a redelivered or resumed attempt is actually allowed
    to run the graph from - a fresh claim that hasn't started yet, and a
    prior attempt that failed and is being retried."""
    repo = InvestmentRequestRepository(db_session)

    pending = InvestmentRequest(
        user_id=DEMO_USER_ID, idempotency_key=_unique_key(), status=JobStatus.PENDING,
        city="", budget="",
    )
    failed = InvestmentRequest(
        user_id=DEMO_USER_ID, idempotency_key=_unique_key(), status=JobStatus.FAILED,
        city="", budget="",
    )
    db_session.add_all([pending, failed])
    await db_session.flush()

    assert await repo.try_claim_run(pending.id) is True
    assert await repo.try_claim_run(failed.id) is True

    assert (await db_session.get(InvestmentRequest, pending.id, populate_existing=True)).status == JobStatus.RUNNING
    assert (await db_session.get(InvestmentRequest, failed.id, populate_existing=True)).status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_try_claim_run_refuses_completed_and_running(db_session):
    """The redelivery-safety guarantee itself: a row already COMPLETED (a
    prior attempt already finished) or already RUNNING (a different
    physical attempt currently owns it) must not be handed to a second
    caller - and must not have its status touched by the refusal."""
    repo = InvestmentRequestRepository(db_session)

    completed = InvestmentRequest(
        user_id=DEMO_USER_ID, idempotency_key=_unique_key(), status=JobStatus.COMPLETED,
        city="Austin", budget="$500k",
    )
    running = InvestmentRequest(
        user_id=DEMO_USER_ID, idempotency_key=_unique_key(), status=JobStatus.RUNNING,
        city="", budget="",
    )
    db_session.add_all([completed, running])
    await db_session.flush()

    assert await repo.try_claim_run(completed.id) is False
    assert await repo.try_claim_run(running.id) is False

    assert (await db_session.get(InvestmentRequest, completed.id, populate_existing=True)).status == JobStatus.COMPLETED
    assert (await db_session.get(InvestmentRequest, running.id, populate_existing=True)).status == JobStatus.RUNNING


@pytest.mark.asyncio
async def test_try_claim_run_concurrent_redelivery_yields_exactly_one_winner(db_engine):
    """The real proof, on separate connections exactly like
    test_create_idempotent_concurrent_claims_yield_one_insert_one_replay
    above: two genuinely concurrent callers racing try_claim_run() for the
    same request_id - standing in for Redis's visibility-timeout
    redelivery handing the same task to two workers while the first is
    still mid-run - must yield exactly one True. A plain read-then-branch
    could let both see PENDING and both proceed; only the database's own
    row lock on the conditional UPDATE can arbitrate this atomically."""
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    key = _unique_key()

    async with sessionmaker() as setup_session:
        request = InvestmentRequest(
            user_id=DEMO_USER_ID, idempotency_key=key, status=JobStatus.PENDING,
            city="", budget="",
        )
        setup_session.add(request)
        await setup_session.commit()
        request_id = request.id

    async def claim() -> bool:
        async with sessionmaker() as session:
            won = await InvestmentRequestRepository(session).try_claim_run(request_id)
            await session.commit()
            return won

    try:
        results = await asyncio.gather(claim(), claim())
        assert sorted(results) == [False, True]

        async with sessionmaker() as session:
            row = await session.get(InvestmentRequest, request_id)
        assert row.status == JobStatus.RUNNING
    finally:
        async with sessionmaker() as cleanup_session:
            await cleanup_session.execute(
                delete(InvestmentRequest).where(InvestmentRequest.id == request_id)
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


@pytest.mark.asyncio
async def test_replace_all_inserts_the_given_chunks(db_session):
    repo = DocumentChunkRepository(db_session)

    count = await repo.replace_all([
        {
            "source_origin": "austin-zoning.pdf",
            "page": 0,
            "chunk_index": 0,
            "content": "Ordinance 12-345 sets setback requirements for R-2 districts.",
        },
        {
            "source_origin": "austin-zoning.pdf",
            "page": 1,
            "chunk_index": 1,
            "content": "General discussion of neighborhood character.",
        },
    ])

    assert count == 2
    total = await db_session.scalar(select(func.count()).select_from(DocumentChunk))
    assert total == 2


@pytest.mark.asyncio
async def test_replace_all_wipes_previously_synced_chunks(db_session):
    """A second call must fully replace the first's rows, not append to
    them - a resync always means "this is everything currently in Azure"
    (see DocumentChunkRepository.replace_all's own docstring)."""
    repo = DocumentChunkRepository(db_session)
    await repo.replace_all([
        {"source_origin": "stale.pdf", "page": 0, "chunk_index": 0, "content": "Old content."},
    ])

    count = await repo.replace_all([
        {"source_origin": "fresh.pdf", "page": 0, "chunk_index": 0, "content": "New content."},
    ])
    await db_session.flush()

    assert count == 1
    results = await repo.search_lexical("content", k=10)
    sources = {chunk.source_origin for chunk, _ in results}
    assert sources == {"fresh.pdf"}


@pytest.mark.asyncio
async def test_search_lexical_ranks_the_best_match_first_and_respects_k(db_session):
    repo = DocumentChunkRepository(db_session)
    await repo.replace_all([
        {
            "source_origin": "austin-zoning.pdf",
            "page": 0,
            "chunk_index": 0,
            "content": "setback setback setback requirements for residential districts.",
        },
        {
            "source_origin": "dallas-zoning.pdf",
            "page": 0,
            "chunk_index": 0,
            "content": "A single mention of setback requirements among many other zoning topics.",
        },
        {
            "source_origin": "unrelated.pdf",
            "page": 0,
            "chunk_index": 0,
            "content": "Discussion of parking minimums with no relevant term at all.",
        },
    ])

    results = await repo.search_lexical("setback requirements", k=2)

    assert len(results) == 2
    top_chunk, top_rank = results[0]
    assert top_chunk.source_origin == "austin-zoning.pdf"
    assert top_rank >= results[1][1]
    assert "unrelated.pdf" not in {chunk.source_origin for chunk, _ in results}


@pytest.mark.asyncio
async def test_search_lexical_matches_exact_hyphenated_ordinance_numbers(db_session):
    """The exact-statutory-term case lexical search exists for: dense vector
    retrieval handles rare tokens like an ordinance number poorly, but
    Postgres full-text search finds it directly."""
    repo = DocumentChunkRepository(db_session)
    await repo.replace_all([
        {
            "source_origin": "austin-zoning.pdf",
            "page": 4,
            "chunk_index": 0,
            "content": "Ordinance 12-345 regulates setback requirements citywide.",
        },
        {
            "source_origin": "austin-zoning.pdf",
            "page": 5,
            "chunk_index": 1,
            "content": "Ordinance 99-000 covers an entirely unrelated topic.",
        },
    ])

    results = await repo.search_lexical("12-345", k=5)

    assert len(results) == 1
    assert results[0][0].page == 4


@pytest.mark.asyncio
async def test_search_lexical_returns_nothing_for_a_non_matching_query(db_session):
    repo = DocumentChunkRepository(db_session)
    await repo.replace_all([
        {"source_origin": "austin-zoning.pdf", "page": 0, "chunk_index": 0, "content": "Zoning text."},
    ])

    results = await repo.search_lexical("nonexistent-term-xyz", k=5)

    assert results == []
