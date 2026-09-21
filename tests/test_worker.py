import asyncio
import contextlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import redis
from celery.contrib.testing.worker import start_worker
from sqlalchemy import delete, select

import agents.zoning_law as zoning_law_module
from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, InvestmentRequest, Report
from db.repositories import InvestmentRequestRepository
from db.session import dispose_engine, session_scope
from orchestration.run_recorder import RunOutcome
from worker.celery_app import celery_app
from worker.tasks import (
    SimulatedTaskFailure,
    _SYNC_KNOWLEDGE_BASE_LOCK_KEY,
    generate_report,
    ping,
    sync_knowledge_base,
)


async def _create_real_request_row() -> uuid.UUID:
    """Inserts a genuine investment_requests row against the real
    (non-isolated) default engine - the same engine every test in this
    file already touches via generate_report()'s own dispose_engine()
    calls, since nothing here uses the db_session fixture's rebound test
    connection. Callers must clean up with _delete_request_row().

    Disposes the engine before returning for the same reason
    generate_report() itself does (see worker/tasks.py's own docstring):
    each of these helpers is its own asyncio.run() call from a
    plain sync test function, a fresh event loop every time, while
    db/session.py's engine is a process-wide singleton. Leaving a live
    engine cached here would hand the *next* asyncio.run() call - whether
    that's another one of these helpers or generate_report()'s own -
    connections bound to this call's already-closed loop.
    """
    async with session_scope() as session:
        request, _ = await InvestmentRequestRepository(session).create_idempotent(
            user_id=DEMO_USER_ID,
            idempotency_key=f"worker-test-{uuid.uuid4()}",
            city="",
            budget="",
        )
        request_id = request.id
    await dispose_engine()
    return request_id


async def _delete_request_row(request_id: uuid.UUID) -> None:
    async with session_scope() as session:
        await session.execute(
            delete(InvestmentRequest).where(InvestmentRequest.id == request_id)
        )
    await dispose_engine()


async def _read_request_row(request_id: uuid.UUID) -> InvestmentRequest:
    async with session_scope() as session:
        row = await InvestmentRequestRepository(session).get_by_id(request_id)
    await dispose_engine()
    return row


@pytest.fixture(autouse=True)
def _dispose_stale_engine_between_tests():
    """This file's tests each drive their own asyncio.run() call (directly,
    via generate_report(), or via a real worker's background-thread task
    execution) against db/session.py's process-wide engine singleton - a
    fresh event loop every time. One test
    (test_generate_report_disposes_the_db_engine_after_each_run) mocks the
    real dispose_engine() away on purpose, to assert the call happens
    without needing a second real task to prove the crash it's a
    regression test for - which deliberately leaves the singleton holding
    connections bound to that test's own, by-then-closed loop. Disposing
    it here, immediately after every test in its own fresh loop, prevents
    that from leaking into whichever test happens to run next."""
    yield
    asyncio.run(dispose_engine())


def test_celery_app_configured_with_separate_broker_and_backend():
    """Broker and result backend point at the same Redis server but different
    logical databases - two separate concerns, not one."""
    assert celery_app.conf.broker_url == config.CELERY_BROKER_URL
    assert celery_app.conf.result_backend == config.CELERY_RESULT_BACKEND
    assert celery_app.conf.broker_url != celery_app.conf.result_backend


def test_celery_app_uses_json_serialization():
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_celery_app_acks_late_and_prefetch_one():
    """Long, expensive tasks must not be lost to a crashed worker (acks_late)
    and must not pile up on one worker while others sit idle (prefetch=1)."""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


@pytest.fixture(scope="module")
def celery_worker_process():
    """Starts a real embedded Celery worker (solo pool, in this process) so
    the round-trip test below exercises the actual broker and result backend
    over a real Redis connection - never a mock and never task_always_eager,
    which would skip the broker entirely and prove nothing about delivery."""
    with start_worker(celery_app, pool="solo", perform_ping_check=False) as worker:
        yield worker


def test_ping_task_round_trips_through_real_redis(celery_worker_process):
    result = ping.delay()
    assert result.get(timeout=10) == "pong"


def test_generate_report_task_wraps_run_claimed_request():
    """A plain synchronous test, deliberately - calling the task directly
    (not via .delay()) executes generate_report()'s body, which calls
    asyncio.run() internally. That's only safe outside an already-running
    event loop, which is exactly what a plain (non pytest-asyncio) test
    function gives: no loop is running yet when asyncio.run() starts one."""
    request_id = uuid.uuid4()
    fake_outcome = RunOutcome(
        request_id=request_id, status=JobStatus.COMPLETED, report=None, replayed=False
    )
    with patch(
        "worker.tasks.run_claimed_request", new_callable=AsyncMock, return_value=fake_outcome
    ) as mock_run:
        result = generate_report("Invest in Austin, TX", str(request_id), 20)

    mock_run.assert_awaited_once_with("Invest in Austin, TX", request_id, 20)
    assert result == "completed"


def test_generate_report_round_trips_through_real_redis(celery_worker_process):
    """Proves the task itself is correctly registered and its arguments
    survive real JSON serialization through a real broker/worker - the same
    mechanism proof as the ping test above, not a claim about database
    behavior (run_claimed_request() is mocked out here; its own correctness
    is covered directly, against a real Postgres, by
    tests/test_run_recorder.py). A real embedded worker runs task bodies on
    a background thread in this same process, so the patch below - applied
    in the main thread - is visible to it: they share the same module
    object, just not the same event loop."""
    request_id = uuid.uuid4()
    fake_outcome = RunOutcome(
        request_id=request_id, status=JobStatus.FAILED, report=None, replayed=False
    )
    with patch(
        "worker.tasks.run_claimed_request", new_callable=AsyncMock, return_value=fake_outcome
    ):
        result = generate_report.delay("Invest in Austin, TX", str(request_id), 20)
        assert result.get(timeout=10) == "failed"


def test_generate_report_disposes_the_db_engine_after_each_run():
    """Regression test for a real bug this phase found only via a manual,
    two-tasks-through-one-real-worker-process check (not reproducible
    inside pytest-asyncio's own already-running loop): Celery's solo pool
    reuses one process across many tasks, each wrapped in its own
    asyncio.run() call - a *fresh* event loop every time - while
    db/session.py's engine is a process-wide lazy singleton, built once and
    cached. Without disposing it before this task's loop closes, the next
    task's get_engine() would hand back this run's now-orphaned asyncpg
    pool - bound to a loop that no longer exists - and fail with "Future
    attached to a different loop" (confirmed by dropping the dispose call
    and running two real generate_report.delay() calls through one
    `celery worker --pool=solo` process by hand). Asserting the call here,
    rather than reproducing the crash itself, is what a plain pytest
    process can check quickly and deterministically."""
    request_id = uuid.uuid4()
    fake_outcome = RunOutcome(
        request_id=request_id, status=JobStatus.COMPLETED, report=None, replayed=False
    )
    with patch(
        "worker.tasks.run_claimed_request", new_callable=AsyncMock, return_value=fake_outcome
    ), patch("worker.tasks.dispose_engine", new_callable=AsyncMock) as mock_dispose:
        generate_report("Invest in Austin, TX", str(request_id), 20)

    mock_dispose.assert_awaited_once()


def test_generate_report_configured_with_retry_backoff_and_jitter():
    """The decorator arguments themselves - asserted directly on the task
    object rather than by observing a real retry (that's covered by the
    end-to-end tests below), since these five values are what actually
    drive Celery's autoretry machinery regardless of how a given attempt
    plays out."""
    assert generate_report.autoretry_for == (Exception,)
    assert generate_report.retry_backoff == config.TASK_RETRY_BACKOFF_BASE_SECONDS
    assert generate_report.retry_backoff_max == config.TASK_RETRY_BACKOFF_MAX_SECONDS
    assert generate_report.retry_jitter is True
    assert generate_report.max_retries == config.TASK_MAX_RETRIES


def test_generate_report_increments_attempt_count_on_every_physical_attempt():
    """increment_attempt_count() runs before run_claimed_request() is even
    reached, on every physical call to the task body - proven here by
    calling generate_report() directly three times for the same
    request_id, standing in for three physical attempts (the original try
    plus two retries), and reading the row's attempt_count back afterward."""
    request_id = asyncio.run(_create_real_request_row())
    fake_outcome = RunOutcome(
        request_id=request_id, status=JobStatus.COMPLETED, report=None, replayed=False
    )
    try:
        with patch(
            "worker.tasks.run_claimed_request", new_callable=AsyncMock, return_value=fake_outcome
        ):
            for _ in range(3):
                generate_report("Invest in Austin, TX", str(request_id), 20)

        row = asyncio.run(_read_request_row(request_id))
        assert row.attempt_count == 3
    finally:
        asyncio.run(_delete_request_row(request_id))


def test_generate_report_injected_failure_short_circuits_before_run_claimed_request():
    """config.TASK_FAILURE_INJECTION_COUNT is read fresh on every call (not
    bound as a decorator/parameter default - the same import-time-freezing
    trap the roadmap already documents elsewhere would otherwise silently
    ignore this patch), so setting it to 1 here makes a direct call (whose
    self.request.retries defaults to 0, an attempt that hasn't been
    retried yet - 0 < 1) raise before run_claimed_request is ever awaited."""
    request_id = asyncio.run(_create_real_request_row())
    try:
        with patch(
            "worker.tasks.run_claimed_request", new_callable=AsyncMock
        ) as mock_run, patch.object(config, "TASK_FAILURE_INJECTION_COUNT", 1):
            with pytest.raises(SimulatedTaskFailure):
                generate_report("Invest in Austin, TX", str(request_id), 20)
        mock_run.assert_not_awaited()
    finally:
        asyncio.run(_delete_request_row(request_id))


def test_on_failure_marks_request_failed_and_routes_to_dead_letter_queue():
    """Simulates Celery invoking on_failure() the way it would once
    max_retries is exhausted - args exactly as generate_report.delay()
    would have supplied them, kwargs empty since this task is always
    called positionally. worker.tasks._route_to_dead_letter_queue is
    mocked here, not celery_app.send_task directly: Task.delay()/
    apply_async() are themselves thin wrappers around self.app.send_task()
    under the hood, so mocking that attribute globally would silently
    break any real .delay() call sharing the same test - a dedicated
    wrapper function is what lets this test prove the DLQ routing call
    happens with the right arguments without that risk. The status update
    goes through the real engine so the assertion reads the row back
    rather than trusting a mock."""
    request_id = asyncio.run(_create_real_request_row())
    try:
        with patch("worker.tasks._route_to_dead_letter_queue") as mock_route:
            generate_report.on_failure(
                SimulatedTaskFailure("boom"),
                "task-abc",
                ("Invest in Austin, TX", str(request_id), 20),
                {},
                None,
            )

        mock_route.assert_called_once_with(
            "task-abc", "Invest in Austin, TX", str(request_id), "boom"
        )

        row = asyncio.run(_read_request_row(request_id))
        assert row.status == JobStatus.FAILED
    finally:
        asyncio.run(_delete_request_row(request_id))


def test_generate_report_retries_and_recovers_within_max_retries(celery_worker_process):
    """config.TASK_MAX_RETRIES is bound into the decorator at import time
    (Celery resolves retry options once, at task-definition time - a real
    constraint, not an oversight, so this test works within the actual
    configured value rather than trying to patch it away).
    TASK_FAILURE_INJECTION_COUNT=2 (below max_retries) makes the first two
    physical attempts raise; the third (self.request.retries == 2) gets
    through to run_claimed_request and succeeds - proving a task that
    fails partway through genuinely recovers via retry rather than only
    ever delaying an eventual failure."""
    request_id = asyncio.run(_create_real_request_row())
    fake_outcome = RunOutcome(
        request_id=request_id, status=JobStatus.COMPLETED, report=None, replayed=False
    )
    try:
        with patch(
            "worker.tasks.run_claimed_request", new_callable=AsyncMock, return_value=fake_outcome
        ), patch.object(config, "TASK_FAILURE_INJECTION_COUNT", 2):
            result = generate_report.delay("Invest in Austin, TX", str(request_id), 20)
            assert result.get(timeout=30) == "completed"

        row = asyncio.run(_read_request_row(request_id))
        assert row.attempt_count == 3  # 2 injected failures + the attempt that succeeded
    finally:
        asyncio.run(_delete_request_row(request_id))


@pytest.fixture
def offline_graph():
    """The same mock set test_run_recorder.py's own offline_graph fixture
    uses. Needed here too because the redelivery test below runs the real
    (unmocked) run_claimed_request() through generate_report() itself,
    rather than patching run_claimed_request out like every other test in
    this file does."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch("agents.ingest_input.MOCK_INGEST_INPUT_AGENT_OUTPUT", True))
        stack.enter_context(patch("agents.market_data.MOCK_MARKET_DATA_AGENT_OUTPUT", True))
        stack.enter_context(
            patch("agents.neighborhood_vibe.MOCK_NEIGHBORHOOD_VIBE_AGENT_OUTPUT", True)
        )
        stack.enter_context(patch("agents.zoning_law.MOCK_ZONING_LAW_AGENT_OUTPUT", True))
        stack.enter_context(
            patch("agents.financial_modeler.MOCK_FINANCIAL_MODELER_AGENT_OUTPUT", True)
        )
        stack.enter_context(patch("logger.lmm_translator.OFFLINE_MODE", True))
        yield


def test_generate_report_redelivery_short_circuits_without_rerunning_graph(offline_graph):
    """The end-to-end proof of this phase's guarantee, through the real
    task entrypoint rather than a mocked-out run_claimed_request(): two
    physical generate_report() calls for the same request_id - standing in
    for a Celery redelivery of the identical task message, e.g. an ack lost
    after the first call already committed COMPLETED - must produce exactly
    one graph run (one mock agent invocation, standing in for "no second
    LLM spend"), one report row, and six agent_runs rows, not twelve or a
    crash on reports' UNIQUE(request_id) constraint."""
    request_id = asyncio.run(_create_real_request_row())
    real_zoning_mock = zoning_law_module._get_zoning_law_mock_response
    try:
        with patch.object(
            zoning_law_module, "_get_zoning_law_mock_response", wraps=real_zoning_mock
        ) as mock_zoning:
            first_result = generate_report(
                "Invest in Austin, TX up to $900,000", str(request_id), 20
            )
            second_result = generate_report(
                "A completely different query", str(request_id), 20
            )

        assert first_result == "completed"
        assert second_result == "completed"
        assert mock_zoning.call_count == 1  # the redelivery never re-ran the graph

        async def _read_state():
            async with session_scope() as session:
                reports = (
                    await session.execute(select(Report).where(Report.request_id == request_id))
                ).scalars().all()
                agent_runs = (
                    await session.execute(select(AgentRun).where(AgentRun.request_id == request_id))
                ).scalars().all()
                request = await InvestmentRequestRepository(session).get_by_id(request_id)
            await dispose_engine()
            return reports, agent_runs, request

        reports, agent_runs, request = asyncio.run(_read_state())
        assert len(reports) == 1
        assert len(agent_runs) == 6
        assert request.status == JobStatus.COMPLETED
        assert request.attempt_count == 2  # two genuine physical attempts
    finally:
        asyncio.run(_delete_request_row(request_id))


# Deliberately not automated: a fourth test drove config.TASK_FAILURE_
# INJECTION_COUNT past TASK_MAX_RETRIES through the real embedded worker to
# prove retries actually exhaust and land a message on the dead-letter
# queue end to end. Every version of it hit the same wall - the embedded
# test worker's shutdown handshake does not tolerate a task still
# mid-retry-schedule, or whose on_failure() callback is still running in
# the background, at module teardown, and hung or errored unpredictably
# rather than failing cleanly. What that test would have proven is instead
# covered piecewise by the tests above (retries genuinely recover, and
# on_failure() genuinely marks FAILED and routes to the DLQ once Celery
# decides an attempt is final) plus a one-time manual verification against
# a real, standalone `celery worker` process - see this phase's learning
# document for the transcript.


def test_beat_schedule_registers_sync_knowledge_base():
    """Asserted directly on the config dict Celery Beat itself reads, the
    same style as test_generate_report_configured_with_retry_backoff_and_jitter
    above - proving the schedule is actually wired up regardless of whether
    a real Beat process ever runs in this test suite."""
    entry = celery_app.conf.beat_schedule["sync-knowledge-base"]
    assert entry["task"] == "worker.sync_knowledge_base"
    assert entry["schedule"] == config.KNOWLEDGE_BASE_SYNC_SCHEDULE_SECONDS


@pytest.fixture
def _real_redis_client():
    """A real Redis connection, never a mock - the same posture this project
    already takes for Postgres: a mocked client cannot verify a real SET NX
    EX race or a real Lua compare-and-delete."""
    client = redis.Redis.from_url(config.REDIS_URL)
    client.delete(_SYNC_KNOWLEDGE_BASE_LOCK_KEY)
    try:
        yield client
    finally:
        client.delete(_SYNC_KNOWLEDGE_BASE_LOCK_KEY)
        client.close()


def test_sync_knowledge_base_task_wraps_sync_azure_to_pinecone(_real_redis_client):
    fake_result = {"documents_scanned": 1, "chunks_synced": 3, "mocked": False}
    with patch(
        "worker.tasks.sync_azure_to_pinecone", return_value=fake_result
    ) as mock_sync:
        result = sync_knowledge_base()

    mock_sync.assert_called_once_with()
    assert result == fake_result


def test_sync_knowledge_base_skips_when_lock_already_held(_real_redis_client):
    """Simulates a Beat-fired invocation arriving while a prior run is still
    in flight: something else already holds the lock, so this call must not
    touch sync_azure_to_pinecone at all, and must not clear a lock it never
    acquired (the other run's lock survives this call untouched)."""
    _real_redis_client.set(_SYNC_KNOWLEDGE_BASE_LOCK_KEY, "someone-elses-token", ex=60)

    with patch("worker.tasks.sync_azure_to_pinecone") as mock_sync:
        result = sync_knowledge_base()

    mock_sync.assert_not_called()
    assert result == {"skipped": True, "reason": "lock held by another run"}
    assert _real_redis_client.get(_SYNC_KNOWLEDGE_BASE_LOCK_KEY) == b"someone-elses-token"


def test_sync_knowledge_base_releases_its_own_lock_after_a_successful_run(
    _real_redis_client,
):
    with patch(
        "worker.tasks.sync_azure_to_pinecone",
        return_value={"documents_scanned": 0, "chunks_synced": 0, "mocked": False},
    ):
        sync_knowledge_base()

    assert _real_redis_client.get(_SYNC_KNOWLEDGE_BASE_LOCK_KEY) is None


def test_sync_knowledge_base_releases_its_own_lock_even_if_the_sync_raises(
    _real_redis_client,
):
    """The lock must not survive a failed sync either, or every subsequent
    Beat firing would be skipped forever after one bad run."""
    with patch(
        "worker.tasks.sync_azure_to_pinecone", side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError, match="boom"):
            sync_knowledge_base()

    assert _real_redis_client.get(_SYNC_KNOWLEDGE_BASE_LOCK_KEY) is None


def test_sync_knowledge_base_round_trips_through_real_redis(celery_worker_process):
    """Same mechanism proof as the ping/generate_report round-trip tests
    above: the task is correctly registered under worker.sync_knowledge_base
    and runs to completion through a real broker and a real embedded
    worker. sync_azure_to_pinecone is left unmocked deliberately - in this
    sandbox MOCK_KNOWLEDGE_BASE_SYNC/OFFLINE_MODE are false by default
    (see .env.example), and this test does not assume either is set, so it
    patches the flag directly rather than relying on environment state."""
    with patch("scripts.sync_knowledge_base.MOCK_KNOWLEDGE_BASE_SYNC", True):
        result = sync_knowledge_base.delay()
        assert result.get(timeout=10) == {
            "documents_scanned": 2,
            "chunks_synced": 6,
            "mocked": True,
        }
