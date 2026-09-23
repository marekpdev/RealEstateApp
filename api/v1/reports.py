import asyncio
import uuid
from typing import AsyncIterator, Optional, Tuple

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from redis.asyncio.client import PubSub

from api.v1.schemas import (
    AgentRunSnapshot,
    ReportAccepted,
    ReportContent,
    ReportCreateRequest,
    ReportDetail,
    ReportListResponse,
    ReportStreamSnapshot,
    ReportStreamStatus,
    ReportSummary,
)
from auth.api_keys import get_current_caller
from config import config
from db.enums import JobStatus
from db.models import User
from db.repositories import AgentRunRepository, InvestmentRequestRepository, ReportRepository
from db.session import session_scope
from events.publisher import channel_name
from events.redis_client import get_events_redis_client
from events.schemas import ProgressEvent
from orchestration.run_recorder import claim_request
from rate_limit import enforce_rate_limit
from worker.tasks import generate_report

router = APIRouter(prefix="/reports", tags=["Reports"])

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

# The only two JobStatus members that end a request's (or one node's) own
# lifecycle - shared by the "is the whole job over yet" check below and the
# "is this one node's own status a job-ending kind of status" check that
# gates it, so the two can never silently drift out of sync with each
# other.
_TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


def _require_persistence() -> None:
    """A dependency, not a plain function call inside each route body, and
    deliberately declared *before* get_current_caller in every route's
    signature below: FastAPI resolves a function's Depends() parameters
    left-to-right, and an exception from an earlier one skips every later
    one (including get_current_caller's own database lookup, on either the
    API-key or the JWT path - see auth/api_keys.py). That matters because
    this check must still work,
    and still return 503 rather than something else, in an environment
    with no database at all - the same environment get_current_caller
    cannot function in (both of its paths depend on session_scope() to
    resolve the caller).

    Every route here deals in a request_id that has to mean the same
    thing across two separate processes (this one and the Celery worker
    that will eventually run the graph) - unlike cli.py,
    DB_PERSISTENCE_ENABLED=false has no equivalent synchronous fallback
    here, since there is no HTTP response that could hand back a finished
    report on the spot. app.py has no fallback of its own for this either
    any more - it always reaches this exact route over HTTP (see
    services/report_api_client.py), so a caller of app.py's own handle_query()
    just sees this 503 surface as a plain API error. 503, not 500: the
    server itself is healthy, this one capability is deliberately turned
    off."""
    if not config.DB_PERSISTENCE_ENABLED:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Report generation requires DB_PERSISTENCE_ENABLED=true.",
        )


@router.post(
    "",
    response_model=ReportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an investment analysis request",
    description=(
        "Enqueues a new report-generation job. Requires an `Idempotency-Key` "
        "header, scoped per user: reusing a key with the *same* request body "
        "is a safe replay - no new work is done, and the response is `200` "
        "with the original job's current status. Reusing a key with a "
        "*different* request body is rejected with `409`, since the key no "
        "longer unambiguously identifies one request. A key not seen before "
        "always creates a new job and returns `202` with its job id and a "
        "status URL to poll."
    ),
    responses={
        200: {
            "model": ReportAccepted,
            "description": (
                "Replay: this Idempotency-Key was already used with the same "
                "request body. No new job was created or enqueued."
            ),
        },
        409: {
            "description": (
                "Idempotency-Key already used with a different request body"
            )
        },
    },
)
async def create_report(
    payload: ReportCreateRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias=IDEMPOTENCY_KEY_HEADER,
        min_length=1,
        max_length=255,
        description=(
            "A client-generated key unique to this logical request. Reuse it "
            "unchanged to safely retry the same request; a fresh UUID per "
            "genuinely new request is the usual choice."
        ),
    ),
    _persistence: None = Depends(_require_persistence),
    current_user: User = Depends(get_current_caller),
    _rate_limit: None = Depends(enforce_rate_limit),
) -> ReportAccepted:
    claim = await claim_request(current_user.id, idempotency_key, payload.query)
    if claim.payload_conflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Idempotency-Key already used with a different request body.",
        )
    if claim.should_run:
        generate_report.delay(payload.query, str(claim.request_id), 20)
    status_url = str(request.url_for("get_report", request_id=claim.request_id))
    # A Location header, not just the same URL in the body: standard
    # practice for a 202/201 pointing at where to check on the accepted
    # work, and free to set alongside a body a client may prefer to parse
    # instead.
    response.headers["Location"] = status_url
    # created=True means this call's own INSERT won the claim (a genuinely
    # new job, 202); created=False means an existing row for this key was
    # found and this response replays it - no new work was done, 200.
    response.status_code = (
        status.HTTP_202_ACCEPTED if claim.created else status.HTTP_200_OK
    )
    return ReportAccepted(id=claim.request_id, status=claim.status, status_url=status_url)


@router.get(
    "/{request_id}",
    response_model=ReportDetail,
    responses={404: {"description": "No report request with this id exists"}},
    summary="Fetch one report request's current status, and its content once complete",
    name="get_report",
)
async def get_report(
    request_id: uuid.UUID,
    _persistence: None = Depends(_require_persistence),
    current_user: User = Depends(get_current_caller),
    _rate_limit: None = Depends(enforce_rate_limit),
) -> ReportDetail:
    async with session_scope() as session:
        investment_request = await InvestmentRequestRepository(session).get_by_id_for_user(
            request_id, current_user.id
        )
        if investment_request is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report request not found.")

        report_content: Optional[ReportContent] = None
        if investment_request.status == JobStatus.COMPLETED:
            report = await ReportRepository(session).get_by_request_id(request_id)
            if report is not None:
                report_content = ReportContent.model_validate(report)

        return ReportDetail(
            id=investment_request.id,
            status=investment_request.status,
            city=investment_request.city,
            budget=investment_request.budget,
            created_at=investment_request.created_at,
            updated_at=investment_request.updated_at,
            report=report_content,
        )


@router.get(
    "",
    response_model=ReportListResponse,
    summary="List report requests, newest first",
)
async def list_reports(
    limit: int = Query(20, ge=1, le=100, description="Max rows to return."),
    offset: int = Query(0, ge=0, description="Rows to skip, for paging."),
    _persistence: None = Depends(_require_persistence),
    current_user: User = Depends(get_current_caller),
    _rate_limit: None = Depends(enforce_rate_limit),
) -> ReportListResponse:
    async with session_scope() as session:
        repo = InvestmentRequestRepository(session)
        rows = await repo.list_by_user(current_user.id, limit=limit, offset=offset)
        total = await repo.count_by_user(current_user.id)

    return ReportListResponse(
        items=[ReportSummary.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _sse(event: str, data: str) -> str:
    """One Server-Sent Event: an `event:` line naming the type (so a client
    can `addEventListener(event, ...)` per-type instead of every consumer
    parsing every message to figure out its own shape), one `data:` line,
    then the blank line the SSE wire format uses to mark an event's end.
    `data` is always already-serialized single-line JSON here
    (`model_dump_json()`), so one `data:` line is safe - a payload that
    could itself contain a newline would need one `data:` line per source
    line instead, per the spec."""
    return f"event: {event}\ndata: {data}\n\n"


async def _report_snapshot(request_id: uuid.UUID) -> Tuple[JobStatus, str]:
    """Reads the current, durable state of one request straight from
    Postgres: the job's own status, plus every node's own latest known
    status from agent_runs (ordered by when it started, so a client renders
    them in the order the graph actually dispatched them). Returns the
    already-serialized `event: snapshot` payload alongside the job's status,
    so the caller can decide whether anything is left to stream at all."""
    async with session_scope() as session:
        investment_request = await InvestmentRequestRepository(session).get_by_id(request_id)
        agent_runs = await AgentRunRepository(session).list_by_request_id(request_id)

    ordered_runs = sorted(agent_runs, key=lambda run: run.started_at or investment_request.created_at)
    snapshot = ReportStreamSnapshot(
        agent_runs=[
            AgentRunSnapshot(
                node=run.node_name,
                status=run.status,
                started_at=run.started_at,
                completed_at=run.completed_at,
                error_message=run.error_message,
            )
            for run in ordered_runs
        ]
    )
    return investment_request.status, snapshot.model_dump_json()


async def _poll_for_terminal_status(request_id: uuid.UUID) -> Optional[JobStatus]:
    """Bridges the real, small eventual-consistency gap between "a node's
    own per-node event just arrived saying COMPLETED/FAILED" and
    "investment_requests.status itself has actually been updated to match
    yet" - see config.SSE_TERMINAL_POLL_ATTEMPTS's own comment for exactly
    why that gap exists. Returns the terminal status once Postgres agrees,
    or None if it still doesn't after the full bounded budget - which
    genuinely happens, and isn't an error: a normal six-node run publishes
    six per-node COMPLETED events (one per agent), and only the last one to
    arrive is ever actually the run's last event - the other five correctly
    exhaust this budget and return None quickly, so the live loop goes
    straight back to listening for the node after them instead of ever
    blocking on a check that was never going to succeed.
    """
    for _ in range(config.SSE_TERMINAL_POLL_ATTEMPTS):
        async with session_scope() as session:
            investment_request = await InvestmentRequestRepository(session).get_by_id(request_id)
        if investment_request.status in _TERMINAL_STATUSES:
            return investment_request.status
        await asyncio.sleep(config.SSE_TERMINAL_POLL_INTERVAL_SECONDS)
    return None


async def _stream_progress_events(
    request: Request, request_id: uuid.UUID, pubsub: PubSub
) -> AsyncIterator[str]:
    """The SSE body for GET /api/v1/reports/{id}/stream. `pubsub` arrives
    already subscribed to job:{request_id} - the route handler below
    subscribes it *before* returning the StreamingResponse this generator
    backs, deliberately before this function ever reads the Postgres
    snapshot: reading the snapshot first and subscribing second would leave
    a gap where a node event published in between the two is missed
    entirely - events/publisher.py's progress events are ephemeral pub/sub
    with no buffering, so a message published to nobody is simply gone (see
    events/publisher.py's own docstring). Subscribing first can only ever
    produce a harmless duplicate instead - a live message repeating
    something the snapshot already reflects - never a silent gap. (This
    also means the route handler's own subscribe() call is what surfaces an
    unreachable events Redis as a plain 500 before any response bytes go
    out at all, rather than this generator discovering it mid-stream.)

    Client disconnect is handled two ways at once, deliberately: this loop
    proactively checks request.is_disconnected() every heartbeat interval
    (that check itself returning True `return`s out of this generator, so
    the `finally` block below runs immediately, synchronously, in this same
    task - no reliance on garbage collection timing); Starlette's own
    StreamingResponse *also* races consuming this generator against
    listening for the ASGI "http.disconnect" message and cancels the
    former if the latter wins, which throws the cancellation into whichever
    `await` this generator is currently suspended at - also caught by the
    same `finally`. Belt and suspenders: either path alone would eventually
    release the subscription, but the proactive check is what makes it
    prompt rather than "whenever this generator object next gets garbage
    collected".
    """
    channel = channel_name(request_id)
    try:
        job_status, snapshot_payload = await _report_snapshot(request_id)
        yield _sse("snapshot", snapshot_payload)

        if job_status in _TERMINAL_STATUSES:
            # Already finished before this client even connected - a
            # completed/failed job never publishes again, so there is
            # nothing left to subscribe for, ever. The snapshot above
            # already carries every node's final state; only the job-level
            # outcome itself was still missing.
            yield _sse("status", ReportStreamStatus(id=request_id, status=job_status).model_dump_json())
            return

        while True:
            if await request.is_disconnected():
                return
            message = await pubsub.get_message(
                timeout=config.SSE_HEARTBEAT_INTERVAL_SECONDS, ignore_subscribe_messages=True
            )
            if message is None:
                # A bare comment line, not a named event: SSE's own
                # keep-alive idiom (any line starting with ":" is ignored
                # by EventSource entirely, never surfaced to a listener) -
                # defeats an idle proxy/load-balancer timeout during the
                # real gaps between agent steps (one parallel-researcher
                # step alone can run tens of seconds) without the client
                # ever seeing a fake application event.
                yield ": heartbeat\n\n"
                continue

            data: str = message["data"]
            yield _sse("progress", data)

            event = ProgressEvent.model_validate_json(data)
            if event.status in _TERMINAL_STATUSES:
                # One node reaching COMPLETED/FAILED does not, by itself,
                # mean the *job* is done - only that one node is. Reading
                # investment_requests.status back out of Postgres (a short
                # bounded poll, not a single read - see
                # _poll_for_terminal_status()'s own docstring for exactly
                # why a single read can genuinely still see the *previous*
                # status even for the run's actual last event) is what
                # actually answers "is the whole run over", without this
                # endpoint needing to know anything about graph.py's
                # topology - e.g. which node happens to run last, which is
                # exactly the kind of thing that's free to change as the
                # graph evolves.
                terminal_status = await _poll_for_terminal_status(request_id)
                if terminal_status is not None:
                    # This node's own event was not necessarily the run's
                    # *last* publish - on a fast (e.g. fully offline/mocked)
                    # run, several more nodes can finish and publish while
                    # the polling above was busy sleeping between attempts.
                    # Redis already delivered those bytes onto this
                    # connection; they are sitting in this process's own
                    # socket buffer, unread, not lost - draining every
                    # already-buffered message with a zero-timeout read
                    # (never blocking: nothing left to drain answers
                    # immediately with None) before closing is what stops
                    # them from being silently skipped just because this
                    # generator happened to be elsewhere when they arrived.
                    # Found by manually running this endpoint end-to-end:
                    # the offline demo graph's own six nodes finish faster
                    # than one poll-and-retry cycle, several times over.
                    while True:
                        buffered = await pubsub.get_message(
                            timeout=0, ignore_subscribe_messages=True
                        )
                        if buffered is None:
                            break
                        yield _sse("progress", buffered["data"])
                    yield _sse(
                        "status",
                        ReportStreamStatus(
                            id=request_id, status=terminal_status
                        ).model_dump_json(),
                    )
                    return
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()


@router.get(
    "/{request_id}/stream",
    summary="Stream one report request's progress live, over Server-Sent Events",
    description=(
        "`text/event-stream`: an `event: snapshot` carrying every node's "
        "currently known state, then zero or more `event: progress` "
        "messages as the worker actually runs each node, then exactly one "
        "`event: status` with the job's final outcome, after which the "
        "stream ends. A connection with nothing to report for a while "
        "receives a bare `: heartbeat` comment line instead, to keep an "
        "idle proxy from timing it out."
    ),
    responses={404: {"description": "No report request with this id exists"}},
)
async def stream_report_progress(
    request_id: uuid.UUID,
    request: Request,
    _persistence: None = Depends(_require_persistence),
    current_user: User = Depends(get_current_caller),
    _rate_limit: None = Depends(enforce_rate_limit),
) -> StreamingResponse:
    async with session_scope() as session:
        investment_request = await InvestmentRequestRepository(session).get_by_id_for_user(
            request_id, current_user.id
        )
    if investment_request is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Report request not found.")

    # Subscribing here, before the response is returned, rather than inside
    # the generator: an unreachable events Redis must fail this request
    # loudly with a plain 500 before any bytes go out, the same "fail fast"
    # posture rate_limit/dependency.py already takes on its own Redis
    # dependency - not silently start a 200 response that can then never
    # actually stream anything. The generator receives this same
    # already-subscribed pubsub and owns unsubscribing/closing it once the
    # stream ends, for the exact ordering reasons its own docstring
    # explains.
    pubsub = get_events_redis_client().pubsub()
    await pubsub.subscribe(channel_name(request_id))
    # Redis answers a SUBSCRIBE command with its own confirmation message on
    # the same connection, immediately - draining it here (rather than
    # leaving it for the generator's first get_message() call to find)
    # keeps that first live-loop read from mistaking a subscribe
    # confirmation for "nothing happened yet, send a heartbeat" the instant
    # a client connects. Mirrors tests/test_run_recorder.py's own
    # subscribe-then-drain pattern.
    await pubsub.get_message(timeout=1.0)

    return StreamingResponse(
        _stream_progress_events(request, request_id, pubsub),
        media_type="text/event-stream",
        headers={
            # A cache or an intermediary treating a live stream's bytes as
            # cacheable content would be actively wrong - text/event-stream
            # is never meant to be replayed from a cache.
            "Cache-Control": "no-cache",
        },
    )
