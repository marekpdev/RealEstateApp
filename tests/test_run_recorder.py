import contextlib
import uuid
from unittest.mock import patch

import pytest
import respx
from sqlalchemy import select

from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import AgentRun, InvestmentRequest, Report
from orchestration.run_recorder import execute_and_record


def _unique_key() -> str:
    return f"run-recorder-test-{uuid.uuid4()}"


@pytest.fixture
def offline_graph():
    """Every agent mock flag on, plus the UI log translator offline - the
    same set test_offline_graph_run.py patches to run the compiled graph
    with zero real network calls."""
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


async def _run(raw_query: str, key: str):
    async with respx.mock:
        return await execute_and_record(
            raw_query, user_id=DEMO_USER_ID, idempotency_key=key, recursion_limit=20
        )


@pytest.mark.asyncio
async def test_execute_and_record_full_offline_run(db_session, offline_graph):
    """The centrepiece verification: a full offline run records exactly six
    agent_runs rows (one per graph node), one report, a completed request,
    and gives the three parallel researchers one shared started_at rather
    than three slightly different ones."""
    key = _unique_key()

    outcome = await _run("Invest in Austin, TX up to $900,000", key)

    assert outcome.replayed is False
    assert outcome.status == JobStatus.COMPLETED
    assert outcome.report is not None
    assert outcome.report.content

    agent_runs = (
        await db_session.execute(select(AgentRun).where(AgentRun.request_id == outcome.request_id))
    ).scalars().all()
    assert len(agent_runs) == 6
    assert all(run.status == JobStatus.COMPLETED for run in agent_runs)

    runs_by_name = {run.node_name: run for run in agent_runs}
    researcher_names = ["market_data_agent", "neighborhood_vibe_agent", "zoning_law_agent"]
    researcher_starts = {runs_by_name[name].started_at for name in researcher_names}
    assert len(researcher_starts) == 1  # all three share exactly one started_at

    # A node with a domain output key gets it recorded; one with only
    # `messages` (supervisor has no domain key of its own) records None
    # rather than an empty dict.
    assert runs_by_name["ingest_input_agent"].output == {
        "ingest_input": {"city": "Los Angeles, CA", "budget": "$800,000"}
    }
    assert runs_by_name["supervisor_agent"].output is None

    reports = (
        await db_session.execute(select(Report).where(Report.request_id == outcome.request_id))
    ).scalars().all()
    assert len(reports) == 1

    request = await db_session.get(InvestmentRequest, outcome.request_id)
    assert request.status == JobStatus.COMPLETED
    # Backfilled from ingest_input_agent's output mid-run, not known at claim time.
    assert request.city == "Los Angeles, CA"
    assert request.budget == "$800,000"


@pytest.mark.asyncio
async def test_execute_and_record_replay_does_not_rerun_graph(db_session, offline_graph):
    """The idempotency guarantee this module exists to provide: a second
    call with the same key returns the stored report and never touches the
    compiled graph at all."""
    key = _unique_key()

    first = await _run("Invest in Austin, TX up to $900,000", key)
    assert first.replayed is False

    with patch("graph.compiledStateGraph.astream") as mock_astream:
        second = await _run("A completely different query", key)

    mock_astream.assert_not_called()
    assert second.replayed is True
    assert second.status == JobStatus.COMPLETED
    assert second.request_id == first.request_id
    assert second.report.id == first.report.id

    reports = (
        await db_session.execute(select(Report).where(Report.request_id == first.request_id))
    ).scalars().all()
    assert len(reports) == 1  # the replay did not insert a second report


@pytest.mark.asyncio
async def test_execute_and_record_resumes_a_stranded_pending_row(db_session, offline_graph):
    """A row left PENDING by a caller that claimed it but crashed before
    ever running the graph (or one left RUNNING/FAILED by a crashed/failed
    attempt) is resumed on that same request_id - only a COMPLETED row
    short-circuits the run. This is a deliberate design choice: the
    alternative (rejecting or stalling on a non-completed replay) would
    strand that row forever with nothing able to move it out of PENDING."""
    key = _unique_key()
    stranded = InvestmentRequest(
        user_id=DEMO_USER_ID,
        idempotency_key=key,
        status=JobStatus.PENDING,
        city="",
        budget="",
    )
    db_session.add(stranded)
    await db_session.flush()
    stranded_id = stranded.id

    outcome = await _run("Invest in Austin, TX up to $900,000", key)

    assert outcome.replayed is False
    assert outcome.status == JobStatus.COMPLETED
    assert outcome.request_id == stranded_id

    agent_runs = (
        await db_session.execute(select(AgentRun).where(AgentRun.request_id == stranded_id))
    ).scalars().all()
    assert len(agent_runs) == 6

    # populate_existing=True: `stranded` is already cached in db_session's
    # identity map from the insert above (same row id), and a plain .get()
    # would hand back that stale, pre-run copy instead of reloading it - the
    # same identity-map staleness AgentRunRepository.upsert() works around
    # for ON CONFLICT DO UPDATE ... RETURNING.
    request = await db_session.get(InvestmentRequest, stranded_id, populate_existing=True)
    assert request.status == JobStatus.COMPLETED
    assert request.city == "Los Angeles, CA"


@pytest.mark.asyncio
async def test_execute_and_record_marks_in_flight_node_and_job_failed_on_exception(
    db_session, offline_graph
):
    """A failed node emits no "updates" chunk at all, so the only record it
    ever started comes from the "debug" stream. Recording must fall back to
    that to mark the node itself failed, and the request row failed
    alongside it - and must never reach (or record) financial_modeler_agent,
    which the graph never dispatches once its only path in (the researcher
    fan-in) can't complete."""
    key = _unique_key()

    with patch(
        "agents.zoning_law._get_zoning_law_mock_response", side_effect=RuntimeError("boom")
    ):
        outcome = await _run("Invest in Austin, TX up to $900,000", key)

    assert outcome.status == JobStatus.FAILED
    assert outcome.report is None

    request = await db_session.get(InvestmentRequest, outcome.request_id)
    assert request.status == JobStatus.FAILED

    agent_runs = (
        await db_session.execute(select(AgentRun).where(AgentRun.request_id == outcome.request_id))
    ).scalars().all()
    runs_by_name = {run.node_name: run for run in agent_runs}

    assert runs_by_name["zoning_law_agent"].status == JobStatus.FAILED
    assert "boom" in runs_by_name["zoning_law_agent"].error_message
    assert "financial_modeler_agent" not in runs_by_name

    reports = (
        await db_session.execute(select(Report).where(Report.request_id == outcome.request_id))
    ).scalars().all()
    assert reports == []
