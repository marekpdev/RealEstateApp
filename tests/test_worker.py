import uuid
from unittest.mock import AsyncMock, patch

import pytest
from celery.contrib.testing.worker import start_worker

from config import config
from db.enums import JobStatus
from orchestration.run_recorder import RunOutcome
from worker.celery_app import celery_app
from worker.tasks import generate_report, ping


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
