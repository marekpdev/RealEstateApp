import asyncio
import uuid

import redis
from celery import Task
from celery.utils.log import get_task_logger

from config import config
from db.enums import JobStatus
from db.repositories import InvestmentRequestRepository
from db.session import dispose_engine, session_scope
from orchestration.run_recorder import run_claimed_request
from scripts.sync_knowledge_base import sync_azure_to_pinecone
from worker.celery_app import celery_app

logger = get_task_logger(__name__)

_SYNC_KNOWLEDGE_BASE_LOCK_KEY = "lock:sync_knowledge_base"
# Atomic compare-and-delete: only unlocks if the value still matches the
# token *this* call set. A plain DEL here would risk one run deleting a
# lock a different, later run went on to legitimately acquire after this
# run's own TTL already expired it first - the standard unsafe-unlock bug
# in a naively implemented Redis lock.
_RELEASE_LOCK_IF_OWNER_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""


class SimulatedTaskFailure(Exception):
    """Raised only by generate_report's own config.TASK_FAILURE_INJECTION_COUNT
    check - a deliberate stand-in for a genuine infrastructure failure (a
    dropped Postgres connection, a Redis blip) that this phase's retry/
    backoff/DLQ machinery needs to be exercised against on demand, without
    actually having to break a real service to prove any of it works."""


class ReportGenerationTask(Task):
    """Handles the one thing Celery's autoretry_for doesn't do for you:
    reacting to a task that has no retries left. Celery calls on_failure()
    exactly once per task id, and only for the attempt that's genuinely
    final - either the raised exception isn't in autoretry_for, or
    max_retries attempts are already spent. An attempt that's about to be
    retried never reaches this; it goes through on_retry() instead, which
    the default Task implementation already handles by just logging."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        raw_query = args[0] if len(args) > 0 else kwargs.get("raw_query")
        request_id = args[1] if len(args) > 1 else kwargs.get("request_id")
        logger.error(
            "worker.generate_report[%s] exhausted retries for request %s: %s",
            task_id, request_id, exc,
        )

        async def _mark_permanently_failed() -> None:
            try:
                async with session_scope() as session:
                    await InvestmentRequestRepository(session).update_status(
                        uuid.UUID(request_id), JobStatus.FAILED
                    )
            finally:
                await dispose_engine()

        asyncio.run(_mark_permanently_failed())
        _route_to_dead_letter_queue(task_id, raw_query, request_id, str(exc))


def _route_to_dead_letter_queue(
    task_id: str, raw_query: str, request_id: str, error_message: str
) -> None:
    """Celery/Redis has no first-class dead-letter concept (unlike
    RabbitMQ) - routing a plain task to a dedicated queue that nothing
    currently consumes is the standard stand-in. dead_letter itself never
    runs automatically; the message just sits in that queue (a Redis list)
    for a human to inspect or manually replay.

    A separate function, not inlined into on_failure(), specifically so
    tests can patch it without also patching celery_app.send_task itself -
    Task.delay()/apply_async() are themselves thin wrappers around
    self.app.send_task() under the hood, so mocking that attribute
    globally silently breaks every .delay() call in the same test, not
    just this one deliberate use of it."""
    celery_app.send_task(
        "worker.dead_letter",
        args=[task_id, raw_query, request_id, error_message],
        queue=config.DEAD_LETTER_QUEUE_NAME,
    )


@celery_app.task(name="worker.ping")
def ping() -> str:
    """Trivial round-trip task: proves a task can be enqueued through the
    broker, picked up by a worker process, executed, and have its result
    retrieved from the result backend - the full path any real task will use."""
    return "pong"


@celery_app.task(
    bind=True,
    base=ReportGenerationTask,
    name="worker.generate_report",
    # Anything that escapes the body below - not the agent-level failures
    # run_claimed_request()'s own _run_and_record() already catches and
    # records as a FAILED row internally, but a genuine infrastructure
    # failure outside that (a dropped database connection while marking
    # the row RUNNING, a Redis hiccup) or an injected one - is presumed
    # transient and worth retrying, up to max_retries.
    autoretry_for=(Exception,),
    retry_backoff=config.TASK_RETRY_BACKOFF_BASE_SECONDS,
    retry_backoff_max=config.TASK_RETRY_BACKOFF_MAX_SECONDS,
    # Full jitter (Celery's default when retry_jitter=True): each retry's
    # delay is chosen uniformly at random between 0 and the exponential
    # backoff ceiling, not the ceiling itself. Without it, every worker
    # instance that failed on the same broker outage would retry at
    # exactly 1s, 2s, 4s... in lockstep, re-creating the exact thundering
    # herd against Postgres/Redis that backoff is supposed to relieve.
    retry_jitter=True,
    max_retries=config.TASK_MAX_RETRIES,
)
def generate_report(self, raw_query: str, request_id: str, recursion_limit: int = 20) -> str:
    """Runs the multi-agent graph for an already-claimed request and records
    its outcome - the task cli.py and api/v1/reports.py's create_report()
    route (app.py's own path to this, over HTTP) enqueue instead of calling
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
    unlike cli.py and api/v1/reports.py's create_report() route (app.py's
    own path to enqueuing this task now, over HTTP - see
    services/report_api_client.py), which call into this module from
    inside their own already-running event loop and must never call
    asyncio.run() themselves. But asyncio.run() opens a *new* event loop
    every call and tears it down when it returns - while db/session.py's
    engine is a process-wide lazy singleton (deliberately, so each of
    those callers' own single long-lived event loop only ever creates it
    once - see db/session.py's module docstring).
    A worker process handles many tasks over its lifetime, each getting its
    own fresh loop from its own asyncio.run() call, but get_engine() would
    keep handing back the *first* task's engine - whose pooled asyncpg
    connections are bound to that first, by-then-closed loop - to every task
    after it, failing with "Future attached to a different loop" (the same
    asyncpg-connections-are-loop-bound constraint the test suite hits
    differently - see tests/test_app_wiring.py's _inline_worker()).
    dispose_engine() inside the same asyncio.run() call, right before this
    loop itself closes, is what makes the next task's get_engine() see no
    cached engine and lazily build a fresh one bound to *its* new loop. This
    now runs on every physical attempt (including ones that raise before
    reaching run_claimed_request), not just successful ones - a retried
    attempt gets its own fresh asyncio.run() call from Celery too, so it
    needs the same clean slate the very first attempt does.

    Retries and the dead-letter queue: see this task's autoretry_for/
    retry_backoff/retry_jitter/max_retries decorator arguments and
    ReportGenerationTask.on_failure() above. After max_retries attempts a
    still-failing task is genuinely "poison" - retrying it again would
    never succeed no matter how many more times it's redelivered - and
    on_failure() takes over: marks the job FAILED for good and routes the
    task to the dead-letter queue instead of Celery quietly dropping it.

    config.TASK_FAILURE_INJECTION_COUNT is the deliberate failure-injection
    knob this phase's own verification needs: set to N, the first N
    physical attempts raise SimulatedTaskFailure before touching
    run_claimed_request at all (self.request.retries counts *prior*
    retries, so the original attempt has retries=0). Left at 0 (the
    default) this check is never true and behavior is identical to Phase
    2.2's task.
    """

    async def _run() -> str:
        try:
            async with session_scope() as session:
                await InvestmentRequestRepository(session).increment_attempt_count(
                    uuid.UUID(request_id)
                )
            if self.request.retries < config.TASK_FAILURE_INJECTION_COUNT:
                raise SimulatedTaskFailure(
                    f"Injected failure on attempt {self.request.retries + 1} "
                    f"of {config.TASK_FAILURE_INJECTION_COUNT}"
                )
            outcome = await run_claimed_request(
                raw_query, uuid.UUID(request_id), recursion_limit
            )
            return outcome.status.value
        finally:
            await dispose_engine()

    return asyncio.run(_run())


@celery_app.task(name="worker.dead_letter")
def dead_letter(task_id: str, raw_query: str, request_id: str, error_message: str) -> dict:
    """The poison-message parking lot generate_report.on_failure() routes to,
    via a dedicated queue (config.DEAD_LETTER_QUEUE_NAME), once max_retries
    is exhausted. Nothing consumes this queue automatically - no worker here
    is ever subscribed to it - so a message landing on it just sits in
    Redis for a human to inspect or decide whether to manually replay.
    Keeping the original payload (raw_query, request_id), not just an error
    string, is what makes that replay decision possible at all: without
    them, a poison message would carry only the fact that something failed,
    not enough to act on it. This task exists mainly so the payload has a
    registered, named place to go; it isn't expected to actually execute in
    normal operation."""
    logger.error(
        "\U0001f480 Dead-lettered task=%s request_id=%s error=%s",
        task_id, request_id, error_message,
    )
    return {"task_id": task_id, "request_id": request_id, "error": error_message}


@celery_app.task(name="worker.sync_knowledge_base")
def sync_knowledge_base() -> dict:
    """Celery Beat's scheduled entrypoint (worker/celery_app.py's
    beat_schedule) for scripts.sync_knowledge_base.sync_azure_to_pinecone().
    Deliberately thin, the same shape as generate_report: the actual
    ingestion logic lives in the script, not here, so it stays independently
    testable and directly runnable (`uv run python -m scripts.sync_knowledge_base`)
    without a Celery worker at all - Celery orchestrates *when* this runs,
    the script defines *what* running it means.

    Guarded against overlapping runs by a Redis lock (SET NX EX - the
    standard single-instance distributed-lock pattern), not just "assume
    Beat only ever fires one instance": Beat's own schedule can legitimately
    fire again before a slow real sync finishes (a short interval, a stalled
    Azure download), and nothing about Celery/Redis prevents two worker
    processes from consuming two such deliveries concurrently. Without the
    lock, two concurrent syncs would each list/chunk/embed the same PDFs and
    double-upsert into Pinecone - wasted paid embedding calls, and, if Azure
    returns a different partial blob listing to each run, no straightforward
    way to reason about which run's data actually ended up in the vector
    store. A per-request idempotency key (generate_report's own tool for
    the report-generation path) doesn't apply here: there's no per-invocation
    identity to dedupe on, since every scheduled firing means the same
    thing ("resync everything currently in Azure") - the property this task
    actually needs is mutual exclusion between overlapping runs, which is
    exactly what a lock provides and idempotency doesn't.
    """
    client = redis.Redis.from_url(config.REDIS_URL)
    lock_token = uuid.uuid4().hex
    try:
        acquired = client.set(
            _SYNC_KNOWLEDGE_BASE_LOCK_KEY,
            lock_token,
            nx=True,
            ex=config.KNOWLEDGE_BASE_SYNC_LOCK_TTL_SECONDS,
        )
        if not acquired:
            logger.info(
                "worker.sync_knowledge_base skipped: a sync is already in progress"
            )
            return {"skipped": True, "reason": "lock held by another run"}

        try:
            result = sync_azure_to_pinecone()
            logger.info("worker.sync_knowledge_base finished: %s", result)
            return result
        finally:
            client.eval(_RELEASE_LOCK_IF_OWNER_SCRIPT, 1, _SYNC_KNOWLEDGE_BASE_LOCK_KEY, lock_token)
    finally:
        client.close()
