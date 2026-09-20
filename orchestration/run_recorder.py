import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Set, Tuple

from langchain_core.messages import HumanMessage

from config import config
from db.enums import JobStatus
from db.models import Report
from db.repositories import (
    AgentRunRepository,
    InvestmentRequestRepository,
    ReportRepository,
)
from db.session import session_scope
from graph import NODE_REGISTRY, compiledStateGraph


@dataclass
class RunOutcome:
    """What execute_and_record() hands back to its caller. `report` is a
    detached SQLAlchemy Report instance - safe to read scalar columns off
    of, since the session that produced it is already closed and
    expire_on_commit=False keeps its loaded attributes in place - or None
    when the run failed. `replayed=True` means the graph never ran: the
    idempotency key already belonged to a completed job."""

    request_id: uuid.UUID
    status: JobStatus
    report: Optional[Report]
    replayed: bool


async def execute_and_record(
    raw_query: str,
    *,
    user_id: uuid.UUID,
    idempotency_key: str,
    recursion_limit: int = 20,
) -> RunOutcome:
    """Wraps a full run of the compiled graph from the outside: claims a job
    for (user_id, idempotency_key), then either replays a stored report
    (already completed - the graph does not run at all) or runs the graph,
    recording one agent_runs row per node as the graph streams and the
    final report/status once it finishes.

    A plain composition of claim_request() + run_claimed_request()/
    get_replayed_outcome() - kept as one call for callers that run the
    graph inline and don't need the id before the run finishes (tests,
    and the DB_PERSISTENCE_ENABLED escape hatch's synchronous sibling
    path). app.py/cli.py call the two halves separately instead (see
    claim_request()'s docstring) so they can hand the id to a Celery task
    before the run even starts.

    LangGraph orchestrates *within* a run; this module orchestrates the
    run's lifecycle in Postgres around it. Nothing here reaches into
    agents/ or graph.py's node bodies - db/ must never be imported by
    agents/, so persistence stays a concern this module owns from outside.
    """
    request_id, should_run = await claim_request(user_id, idempotency_key)
    if not should_run:
        return await get_replayed_outcome(request_id)
    return await run_claimed_request(raw_query, request_id, recursion_limit)


async def claim_request(user_id: uuid.UUID, idempotency_key: str) -> Tuple[uuid.UUID, bool]:
    """One short transaction, entirely separate from the run that may
    follow. city/budget aren't known yet - the graph hasn't run a single
    node - so the claiming insert writes them as empty strings;
    _maybe_backfill_city_budget() fills in the real values once
    ingest_input_agent's output appears in a stream_mode="values" snapshot.

    A pre-existing COMPLETED row short-circuits the run entirely - the whole
    point of claiming idempotently: a retried request must not re-run six
    agents and re-spend LLM calls. A pre-existing PENDING/RUNNING/FAILED
    row - a previous attempt that never finished, e.g. the process crashed
    mid-run - is instead resumed on that same request_id rather than left
    stranded forever. There is deliberately no "already in flight, reject
    this one" state here; arbitrating two truly concurrent runs of the same
    key is a future task queue's concern, not this module's.

    Public (not execute_and_record()'s private implementation detail)
    because app.py/cli.py call it directly: InvestmentRequest.id is a
    client-generated UUID (see db/models.py), assigned in Python and known
    the moment this claim's INSERT flushes - well before the graph itself
    has run a single node. That's what lets a caller learn the request_id
    immediately, hand it to a Celery task as that task's argument (see
    worker/tasks.py's generate_report), and start polling for it, all
    without waiting for the run itself to even begin.

    Returns (request_id, should_run).
    """
    async with session_scope() as session:
        request, created = await InvestmentRequestRepository(session).create_idempotent(
            user_id=user_id, idempotency_key=idempotency_key, city="", budget=""
        )
        request_id = request.id
        already_completed = (not created) and request.status == JobStatus.COMPLETED
    return request_id, not already_completed


async def get_replayed_outcome(request_id: uuid.UUID) -> RunOutcome:
    """Builds the RunOutcome for a request_id claim_request() already
    determined is COMPLETED (should_run=False): fetches the stored report
    so the caller can show it without running anything."""
    async with session_scope() as session:
        report = await ReportRepository(session).get_by_request_id(request_id)
    return RunOutcome(
        request_id=request_id, status=JobStatus.COMPLETED, report=report, replayed=True
    )


async def run_claimed_request(
    raw_query: str, request_id: uuid.UUID, recursion_limit: int = 20
) -> RunOutcome:
    """Runs the graph for a request_id that claim_request() has already
    claimed (should_run=True) - the continuation execute_and_record() calls
    right after its own claim, and the same continuation worker/tasks.py's
    generate_report task calls after app.py/cli.py have claimed on its
    behalf. Marks the row RUNNING, then streams and records exactly as
    _run_and_record() always has."""
    async with session_scope() as session:
        await InvestmentRequestRepository(session).update_status(request_id, JobStatus.RUNNING)
    return await _run_and_record(raw_query, request_id, recursion_limit)


async def poll_until_terminal(
    request_id: uuid.UUID,
    *,
    poll_interval: Optional[float] = None,
    timeout: Optional[float] = None,
) -> RunOutcome:
    """Watches the investment_requests row for request_id until the Celery
    worker running the graph (worker/tasks.py's generate_report) writes a
    terminal status, or `timeout` elapses. Postgres is the only channel the
    two processes share right now - this is a stand-in for the real push
    mechanism a later phase adds (SSE over Redis pub/sub); until then, the
    only way the caller can know the work finished is to keep asking the
    durable record both processes already agree on. This is what "eventual
    consistency" concretely means here: the row is not COMPLETED the
    instant the task is enqueued, only eventually, and this function is the
    one place that gap is bridged.

    A non-terminal RunOutcome (status still PENDING/RUNNING) on return means
    the timeout elapsed, not that the job failed - the worker may still be
    running it; this call has just stopped watching.

    poll_interval/timeout default to None, not directly to the config
    values, and are resolved from config.* inside the function body rather
    than bound as parameter defaults - a parameter default is evaluated
    once, at import time, so binding it directly would freeze whatever
    config.REPORT_POLL_INTERVAL_SECONDS held the moment this module was
    first imported. Tests patch those config values to make polling fast;
    a frozen default would silently ignore that patch, the same shape of
    import-time-freezing trap already hit with POSTGRES_DSN (see
    tests/conftest.py's _run_alembic_upgrade()).
    """
    loop = asyncio.get_running_loop()
    poll_interval = poll_interval if poll_interval is not None else config.REPORT_POLL_INTERVAL_SECONDS
    timeout = timeout if timeout is not None else config.REPORT_POLL_TIMEOUT_SECONDS
    deadline = loop.time() + timeout
    while True:
        async with session_scope() as session:
            request = await InvestmentRequestRepository(session).get_by_id(request_id)
            if request is None:
                raise RuntimeError(
                    f"investment_requests row {request_id} vanished while polling"
                )
            status = request.status
            if status in (JobStatus.COMPLETED, JobStatus.FAILED):
                report = None
                if status == JobStatus.COMPLETED:
                    report = await ReportRepository(session).get_by_request_id(request_id)
                return RunOutcome(
                    request_id=request_id, status=status, report=report, replayed=False
                )
        if loop.time() >= deadline:
            return RunOutcome(request_id=request_id, status=status, report=None, replayed=False)
        await asyncio.sleep(poll_interval)


async def _run_and_record(
    raw_query: str, request_id: uuid.UUID, recursion_limit: int
) -> RunOutcome:
    """Holds no transaction open across this loop: every write below is its
    own session_scope() call. The graph makes multi-minute LLM calls - a
    transaction pinned open for the whole run would hold a connection idle
    for that entire time and block autovacuum on every table it touched.
    """
    inputs = {"messages": [HumanMessage(content=raw_query)]}
    run_config = {"recursion_limit": recursion_limit}

    step_started_at: Dict[int, datetime] = {}
    started_nodes: Dict[str, datetime] = {}
    completed_nodes: Set[str] = set()
    city_budget_persisted = False
    final_values: Optional[dict] = None

    try:
        async for mode, chunk in compiledStateGraph.astream(
            inputs, config=run_config, stream_mode=["debug", "updates", "values"]
        ):
            if mode == "debug":
                await _handle_debug_chunk(chunk, request_id, step_started_at, started_nodes)
            elif mode == "updates":
                completed_nodes |= await _handle_updates_chunk(chunk, request_id)
            elif mode == "values":
                final_values = chunk
                city_budget_persisted = await _maybe_backfill_city_budget(
                    chunk, request_id, city_budget_persisted
                )
    except Exception as exc:
        await _mark_failed(request_id, started_nodes, completed_nodes, str(exc))
        return RunOutcome(request_id=request_id, status=JobStatus.FAILED, report=None, replayed=False)

    status, report = await _persist_success(request_id, final_values or {})
    return RunOutcome(request_id=request_id, status=status, report=report, replayed=False)


async def _handle_debug_chunk(
    chunk: dict,
    request_id: uuid.UUID,
    step_started_at: Dict[int, datetime],
    started_nodes: Dict[str, datetime],
) -> None:
    """"tasks" events fire at superstep dispatch, before any node's async
    body actually runs - correct to treat as "started". But LangGraph calls
    datetime.now() separately for each task within one dispatch (verified
    empirically: the three parallel researchers' own "task" events carry
    three distinct microsecond-apart timestamps, not one shared value), so
    deriving started_at from each individual chunk's own timestamp would
    give the three parallel researchers three different values. Keying the
    captured timestamp by `step` instead, and reusing it for every node
    dispatched in that same step, is what actually gives them one shared,
    correct started_at.
    """
    if chunk.get("type") != "task":
        return
    node_name = chunk["payload"]["name"]
    if node_name not in NODE_REGISTRY:
        return

    step = chunk["step"]
    if step not in step_started_at:
        step_started_at[step] = datetime.fromisoformat(chunk["timestamp"])
    started_at = step_started_at[step]
    started_nodes[node_name] = started_at

    async with session_scope() as session:
        await AgentRunRepository(session).upsert(
            request_id=request_id,
            node_name=node_name,
            status=JobStatus.RUNNING,
            started_at=started_at,
        )


async def _handle_updates_chunk(chunk: dict, request_id: uuid.UUID) -> Set[str]:
    """stream_mode="updates" emits once per task as it completes, so a
    chunk here carries exactly one node key in the common case - except the
    three parallel researchers, which arrive as three separate chunks, each
    handled by its own call to this function. Iterating .items() anyway,
    and skipping "__"-prefixed keys, means a future internal channel added
    by a LangGraph version bump can't silently get recorded as a node run.
    """
    completed: Set[str] = set()
    completed_at = datetime.now(timezone.utc)
    for node_name, payload in chunk.items():
        if node_name.startswith("__"):
            continue
        async with session_scope() as session:
            await AgentRunRepository(session).upsert(
                request_id=request_id,
                node_name=node_name,
                status=JobStatus.COMPLETED,
                completed_at=completed_at,
                output=_serialize_node_output(payload),
            )
        completed.add(node_name)
    return completed


async def _maybe_backfill_city_budget(
    chunk: dict, request_id: uuid.UUID, already_persisted: bool
) -> bool:
    """stream_mode="values" uses read_channels(skip_empty=True) - a key
    never written is absent entirely, not None - so .get() is mandatory
    here. The first snapshot where "ingest_input" appears is the first
    point city/budget are known; every snapshot after that would also
    carry it, so a flag avoids repeating the UPDATE on every later chunk.
    """
    if already_persisted:
        return True
    ingest_input = chunk.get("ingest_input")
    if ingest_input is None:
        return False
    async with session_scope() as session:
        await InvestmentRequestRepository(session).update_extracted_details(
            request_id, city=ingest_input.city, budget=ingest_input.budget
        )
    return True


async def _mark_failed(
    request_id: uuid.UUID,
    started_nodes: Dict[str, datetime],
    completed_nodes: Set[str],
    error_message: str,
) -> None:
    """A failed node emits no "updates" chunk at all - the exception
    propagates straight out of the astream() loop instead. So the only way
    to know which node(s) were mid-flight when the graph gave up is the gap
    between what "debug"/"task" said had started and what "updates" said
    had actually finished; everything in that gap gets marked failed here
    (normally one node, but every parallel researcher still running at the
    moment one of its siblings raised would land in this set too)."""
    now = datetime.now(timezone.utc)
    unfinished = started_nodes.keys() - completed_nodes
    async with session_scope() as session:
        agent_repo = AgentRunRepository(session)
        for node_name in unfinished:
            await agent_repo.upsert(
                request_id=request_id,
                node_name=node_name,
                status=JobStatus.FAILED,
                completed_at=now,
                error_message=error_message,
            )
        await InvestmentRequestRepository(session).update_status(request_id, JobStatus.FAILED)


async def _persist_success(
    request_id: uuid.UUID, final_values: dict
) -> Tuple[JobStatus, Optional[Report]]:
    """Report content and the request's terminal status are written in one
    transaction: a report without its request ever reaching `completed`
    (or vice versa) would be a state no caller should be able to observe.

    Defensive fallback: if the stream ended without raising but somehow
    never produced a financial_report (e.g. a future topology change routes
    around the last node), a NOT NULL violation on reports.content would
    otherwise surface as a raw IntegrityError instead of a clean failure -
    mark the job failed instead of writing a report with no content.
    """
    financial_report = final_values.get("financial_report")
    if financial_report is None:
        async with session_scope() as session:
            await InvestmentRequestRepository(session).update_status(request_id, JobStatus.FAILED)
        return JobStatus.FAILED, None

    market_data = final_values.get("market_data")
    telemetry = market_data.telemetry if market_data else None

    async with session_scope() as session:
        report = await ReportRepository(session).create(
            request_id=request_id,
            content=financial_report.financial_report,
            total_listings=telemetry.total_listings if telemetry else None,
            average_price=telemetry.average_price if telemetry else None,
            median_price=telemetry.median_price if telemetry else None,
            highest_listing=telemetry.highest_listing if telemetry else None,
            lowest_listing=telemetry.lowest_listing if telemetry else None,
            raw_properties=(
                [prop.model_dump(mode="json") for prop in telemetry.raw_properties]
                if telemetry
                else []
            ),
        )
        await InvestmentRequestRepository(session).update_status(request_id, JobStatus.COMPLETED)
    return JobStatus.COMPLETED, report


def _serialize_node_output(payload: dict) -> Optional[dict]:
    """Turns one node's partial-state return dict into a JSONB-safe payload
    for agent_runs.output: drops "messages" (conversation history, not
    audit data, and not directly JSON-serializable) and model_dump()s
    whatever Pydantic model the node returned under its own domain key.
    Generic across all six agents rather than switching on node_name -
    every agent's return shape is "one optional domain key plus messages".
    """
    serialized = {
        key: value.model_dump(mode="json") if hasattr(value, "model_dump") else value
        for key, value in payload.items()
        if key != "messages"
    }
    return serialized or None
