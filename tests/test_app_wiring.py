import contextlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import respx
from sqlalchemy import select

import cli
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, InvestmentRequest, Report
from orchestration.run_recorder import run_claimed_request

# app.py no longer calls any of orchestration/run_recorder.py directly - it
# talks to this same process's own /api/v1 HTTP surface instead (see
# services/report_api_client.py). Its own wiring tests live in
# tests/test_app_api_client.py now; this file covers cli.py, which is
# unaffected and still wired exactly as before.


def _unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def fast_polling():
    """poll_until_terminal()'s default interval (1s, see config.py) is fine
    for a real deployment but would make every test in this file slower
    than it needs to be - these tests resolve almost immediately since
    _inline_worker() runs the "worker" cooperatively on the same loop."""
    with patch("orchestration.run_recorder.config.REPORT_POLL_INTERVAL_SECONDS", 0.01):
        yield


@pytest.fixture
def offline_graph():
    """Every agent mock flag on, plus the UI log translator offline - the
    same set test_run_recorder.py and test_offline_graph_run.py patch to run
    the compiled graph with zero real network calls."""
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


@contextlib.contextmanager
def _inline_worker(module):
    """Stands in for a live Celery worker: runs the exact same continuation
    (run_claimed_request()) the real worker/tasks.py's generate_report task
    calls, on the test's own event loop instead of one dispatched through a
    real broker to a separate process.

    Deliberately sequential, not concurrent with the poll loop that
    follows, even though production code enqueues and polls concurrently:
    two coroutines each opening their own session_scope() against the
    *same* underlying connection (which db_session rebinds every
    session_scope() call to, for the whole test) would each try to open
    and release their own SAVEPOINT on that one connection. If the event
    loop switches between them at an await point, their savepoints stop
    nesting in strict LIFO order and Postgres raises
    InvalidSavepointSpecificationError - confirmed empirically switching
    this fixture from asyncio.ensure_future() (truly concurrent, on this
    same loop) to the sequential await below. Running the worker to
    completion first means the poll loop's very first check already finds
    a terminal status, so no interleaving - concurrent or otherwise -ever
    happens on the shared connection. (A *real* embedded Celery worker
    would dodge this differently and just as fatally: it runs on a
    background thread with its own asyncio.run() call, and asyncpg
    connections can't be used from a different event loop/thread than the
    one that opened them.)

    Patches generate_report.delay and poll_until_terminal on the given
    module (cli, the only caller left that still imports either directly)
    so handle_query()'s real enqueue-then-poll call sites are what trigger
    this, exactly as production would - just without a real broker/worker
    in between."""
    pending_run: dict = {}

    def _fake_delay(raw_query: str, request_id: str, recursion_limit: int):
        pending_run["coro"] = run_claimed_request(
            raw_query, uuid.UUID(request_id), recursion_limit
        )

    real_poll_until_terminal = getattr(module, "poll_until_terminal")

    async def _run_worker_then_poll(request_id, **kwargs):
        coro = pending_run.pop("coro", None)
        if coro is not None:
            await coro
        return await real_poll_until_terminal(request_id, **kwargs)

    with patch.object(module, "generate_report") as mock_task, \
         patch.object(module, "poll_until_terminal", side_effect=_run_worker_then_poll):
        mock_task.delay.side_effect = _fake_delay
        yield mock_task


@pytest.mark.asyncio
async def test_handle_query_persists_full_run(db_session, offline_graph):
    """cli.handle_query() claims the request, enqueues it onto the Celery
    worker, and polls until it lands the same rows in the database: one
    completed request, six completed agent_runs, one report."""
    key = _unique_key("wiring-full-run")

    with _inline_worker(cli):
        async with respx.mock:
            await cli.handle_query(
                "Invest in Austin, TX up to $900,000", idempotency_key=key
            )

    request = await db_session.scalar(
        select(InvestmentRequest).where(InvestmentRequest.idempotency_key == key)
    )
    assert request is not None
    assert request.user_id == DEMO_USER_ID
    assert request.status == JobStatus.COMPLETED
    assert request.city == "Los Angeles, CA"  # from the mocked ingest_input output

    agent_runs = (
        await db_session.execute(select(AgentRun).where(AgentRun.request_id == request.id))
    ).scalars().all()
    assert len(agent_runs) == 6
    assert all(run.status == JobStatus.COMPLETED for run in agent_runs)

    reports = (
        await db_session.execute(select(Report).where(Report.request_id == request.id))
    ).scalars().all()
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_handle_query_enqueues_with_the_claimed_request_id(db_session, offline_graph):
    """generate_report.delay() must be called with the exact same
    request_id claim_request() already wrote to the row - client-generated
    UUID primary keys are what make handing that id to Celery before the
    run even starts possible at all (see db/models.py, claim_request())."""
    key = _unique_key("wiring-enqueue-args")

    with _inline_worker(cli) as mock_task:
        async with respx.mock:
            await cli.handle_query("Invest in Austin, TX", idempotency_key=key)

    request = await db_session.scalar(
        select(InvestmentRequest).where(InvestmentRequest.idempotency_key == key)
    )
    mock_task.delay.assert_called_once_with("Invest in Austin, TX", str(request.id), 20)


@pytest.mark.asyncio
async def test_cli_handle_query_respects_db_persistence_disabled():
    """DB_PERSISTENCE_ENABLED=false is cli.py's documented escape hatch: the
    graph still runs via the old plain ainvoke() path, and
    _enqueue_and_await() - the database-aware, worker-dispatching path -
    must never be called at all. app.py no longer has an equivalent of its
    own (see tests/test_app_api_client.py's own persistence-disabled test,
    which now exercises the API's 503 instead)."""
    with patch("cli.config.DB_PERSISTENCE_ENABLED", False), \
         patch("cli.compiledStateGraph.ainvoke", new_callable=AsyncMock) as mock_ainvoke, \
         patch("cli._enqueue_and_await", new_callable=AsyncMock) as mock_enqueue:
        await cli.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_ainvoke.assert_awaited_once()
    mock_enqueue.assert_not_called()
