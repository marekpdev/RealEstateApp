import contextlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest
import respx
from sqlalchemy import select

import app
import cli
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, InvestmentRequest, Report
from orchestration.run_recorder import RunOutcome


def _unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4()}"


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


@pytest.mark.asyncio
@pytest.mark.parametrize("module", [app, cli])
async def test_handle_query_persists_full_run(db_session, offline_graph, module):
    """app.handle_query() and cli.handle_query() both delegate to
    execute_and_record() and land the same rows in the database: one
    completed request, six completed agent_runs, one report."""
    key = _unique_key("wiring-full-run")

    async with respx.mock:
        await module.handle_query(
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
async def test_app_handle_query_replay_renders_report_explicitly(db_session, offline_graph):
    """A replayed outcome (the graph never runs a second time) must still
    show the user the report - the financial_modeler node's own render-as-
    side-effect only happens on a fresh run, so app.py must render it itself
    for a replay, exactly once."""
    key = _unique_key("wiring-app-replay")

    async with respx.mock:
        await app.handle_query("Invest in Austin, TX up to $900,000", idempotency_key=key)

    with patch("app.render_financial_report", new_callable=AsyncMock) as mock_render, \
         patch("graph.compiledStateGraph.astream") as mock_astream:
        await app.handle_query("A completely different query", idempotency_key=key)

    mock_astream.assert_not_called()
    mock_render.assert_awaited_once()


@pytest.mark.asyncio
async def test_app_handle_query_respects_db_persistence_disabled():
    """DB_PERSISTENCE_ENABLED=false is the documented escape hatch: the graph
    still runs via the old plain ainvoke() path, and execute_and_record() -
    the database-aware path - must never be called at all."""
    with patch("app.config.DB_PERSISTENCE_ENABLED", False), \
         patch("app.compiledStateGraph.ainvoke", new_callable=AsyncMock) as mock_ainvoke, \
         patch("app.execute_and_record", new_callable=AsyncMock) as mock_execute:
        await app.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_ainvoke.assert_awaited_once()
    mock_execute.assert_not_called()


@pytest.mark.asyncio
async def test_cli_handle_query_respects_db_persistence_disabled():
    with patch("cli.config.DB_PERSISTENCE_ENABLED", False), \
         patch("cli.compiledStateGraph.ainvoke", new_callable=AsyncMock) as mock_ainvoke, \
         patch("cli.execute_and_record", new_callable=AsyncMock) as mock_execute:
        await cli.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_ainvoke.assert_awaited_once()
    mock_execute.assert_not_called()


@pytest.mark.asyncio
async def test_app_handle_query_surfaces_database_failure_instead_of_swallowing_it():
    """A database error (e.g. the connection itself failing, not an
    IntegrityError create_idempotent() already handles) must be surfaced to
    the user rather than silently doing nothing - and must not crash the
    Chainlit process either."""
    with patch("app.execute_and_record", new_callable=AsyncMock) as mock_execute, \
         patch("app.log_message", new_callable=AsyncMock) as mock_log:
        mock_execute.side_effect = RuntimeError("connection refused")
        await app.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_log.assert_awaited_once()
    assert "connection refused" in mock_log.await_args.args[0]


@pytest.mark.asyncio
async def test_app_handle_query_surfaces_a_failed_run_without_rendering_a_report():
    """A structurally FAILED RunOutcome (the graph raised partway through)
    must tell the user something went wrong, and must never call
    render_financial_report - there is no report to show."""
    failed_outcome = RunOutcome(
        request_id=uuid.uuid4(), status=JobStatus.FAILED, report=None, replayed=False
    )
    with patch("app.execute_and_record", new_callable=AsyncMock, return_value=failed_outcome), \
         patch("app.log_message", new_callable=AsyncMock) as mock_log, \
         patch("app.render_financial_report", new_callable=AsyncMock) as mock_render:
        await app.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_log.assert_awaited_once()
    mock_render.assert_not_called()
