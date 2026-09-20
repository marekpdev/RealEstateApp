import asyncio
import uuid

from db.session import dispose_engine
from orchestration.run_recorder import run_claimed_request
from worker.celery_app import celery_app


@celery_app.task(name="worker.ping")
def ping() -> str:
    """Trivial round-trip task: proves a task can be enqueued through the
    broker, picked up by a worker process, executed, and have its result
    retrieved from the result backend - the full path any real task will use."""
    return "pong"


@celery_app.task(name="worker.generate_report")
def generate_report(raw_query: str, request_id: str, recursion_limit: int = 20) -> str:
    """Runs the multi-agent graph for an already-claimed request and records
    its outcome - the task app.py/cli.py enqueue instead of calling
    orchestration.run_recorder.execute_and_record() inline.

    Deliberately thin: it does no claiming of its own. The caller (see
    orchestration.run_recorder.claim_request()'s docstring) has already
    claimed request_id before this task was even enqueued, which is only
    possible because InvestmentRequest.id is a client-generated UUID - the
    id exists in Python before the claiming INSERT completes, so the caller
    can hand it to this task as a plain argument rather than needing the
    task to create (and hand back) it. This task's only job is the slow
    part: run the graph, record one agent_runs row per node, and record the
    final report/status - via run_claimed_request(), the exact continuation
    execute_and_record() itself calls after its own in-process claim.

    request_id arrives as a str, not a uuid.UUID - Celery's task_serializer
    is "json" (see worker/celery_app.py), which can only carry plain data
    types, never a UUID object.

    asyncio.run() is correct here specifically because a Celery worker
    process has no event loop already running when a task body executes -
    unlike app.py/cli.py, which call into this module from inside their own
    already-running event loop and must never call asyncio.run() themselves.
    But asyncio.run() opens a *new* event loop every call and tears it down
    when it returns - while db/session.py's engine is a process-wide lazy
    singleton (deliberately, so app.py/cli.py's single long-lived event
    loop only ever creates it once - see db/session.py's module docstring).
    A worker process handles many tasks over its lifetime, each getting its
    own fresh loop from its own asyncio.run() call, but get_engine() would
    keep handing back the *first* task's engine - whose pooled asyncpg
    connections are bound to that first, by-then-closed loop - to every task
    after it, failing with "Future attached to a different loop" (the same
    asyncpg-connections-are-loop-bound constraint the test suite hits
    differently - see tests/test_app_wiring.py's _inline_worker()).
    dispose_engine() inside the same asyncio.run() call, right before this
    loop itself closes, is what makes the next task's get_engine() see no
    cached engine and lazily build a fresh one bound to *its* new loop.
    """

    async def _run() -> str:
        try:
            outcome = await run_claimed_request(
                raw_query, uuid.UUID(request_id), recursion_limit
            )
            return outcome.status.value
        finally:
            await dispose_engine()

    return asyncio.run(_run())
