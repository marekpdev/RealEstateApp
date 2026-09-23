import asyncio
import contextlib
import json
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import pytest_asyncio
import redis
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

import server
from api.v1.reports import _poll_for_terminal_status, _stream_progress_events
from auth.tokens import create_access_token
from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.repositories import InvestmentRequestRepository
from events.publisher import channel_name, publish_progress_event
from events.redis_client import get_events_redis_client
from orchestration.run_recorder import claim_request, run_claimed_request


def _unique_key(label: str = "stream") -> str:
    return f"{label}-{uuid.uuid4()}"


def _auth_headers(user_id=DEMO_USER_ID) -> dict:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


@pytest.fixture(autouse=True)
def fast_polling():
    with patch("orchestration.run_recorder.config.REPORT_POLL_INTERVAL_SECONDS", 0.01):
        yield


@pytest.fixture
def offline_graph():
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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _parse_sse_events(text_body: str) -> list:
    """Splits a full SSE response body (this file's HTTP-level tests always
    read the stream to completion, never mid-run) into (event, data) pairs -
    a bare heartbeat comment (no `event:` line) is skipped, matching what a
    real EventSource client does with it."""
    events = []
    for block in text_body.strip("\n").split("\n\n"):
        if not block or block.startswith(":"):
            continue
        lines = block.split("\n")
        event_line = next((l for l in lines if l.startswith("event: ")), None)
        data_line = next((l for l in lines if l.startswith("data: ")), None)
        if event_line and data_line:
            events.append((event_line[len("event: "):], json.loads(data_line[len("data: "):])))
    return events


async def _subscribed(request_id: uuid.UUID):
    """Mirrors api/v1/reports.py's own stream_report_progress(): subscribe,
    then drain the subscribe confirmation Redis sends back immediately -
    otherwise a test's first get_message() call consumes that confirmation
    instead of a real event and misreads it as "nothing happened yet"."""
    pubsub = get_events_redis_client().pubsub()
    await pubsub.subscribe(channel_name(request_id))
    await pubsub.get_message(timeout=1.0)
    return pubsub


class _FakeRequest:
    """A minimal stand-in for fastapi.Request, satisfying only the one
    method _stream_progress_events actually calls on it. Lets the
    generator-level tests below drive that function directly - without a
    real ASGI connection - while still exercising real behavior."""

    def __init__(self, disconnected_after: int = 10**9):
        self._calls = 0
        self._disconnected_after = disconnected_after

    async def is_disconnected(self) -> bool:
        self._calls += 1
        return self._calls > self._disconnected_after


# --- Access control (mirrors tests/test_api_reports.py's own GET coverage) --


@pytest.mark.asyncio
async def test_stream_requires_authentication_with_401(db_session, client):
    response = await client.get(f"/api/v1/reports/{uuid.uuid4()}/stream")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_stream_returns_404_for_unknown_id(db_session, client):
    response = await client.get(
        f"/api/v1/reports/{uuid.uuid4()}/stream", headers=_auth_headers()
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_stream_owned_by_a_different_user_returns_404(db_session, client):
    other_user_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO users (id, email, hashed_password) VALUES (:id, :email, :pw)"),
        {"id": other_user_id, "email": f"{other_user_id}@example.com", "pw": "unusable"},
    )
    request, _ = await InvestmentRequestRepository(db_session).create_idempotent(
        user_id=other_user_id,
        idempotency_key=_unique_key("owner"),
        raw_query="Invest in Seattle, WA",
        city="Seattle, WA",
        budget="$1",
        status=JobStatus.PENDING,
    )
    await db_session.commit()

    response = await client.get(
        f"/api/v1/reports/{request.id}/stream", headers=_auth_headers()
    )
    assert response.status_code == 404


# --- Already-terminal jobs: snapshot + status only, no live loop entered ---


@pytest.mark.asyncio
async def test_stream_of_a_completed_job_replays_snapshot_then_closes(
    db_session, offline_graph, client
):
    """'connecting after the job finished still yields a sane terminal
    response' - the phase's own verification bullet. Runs the job to
    completion first (sequentially, via claim_request + run_claimed_request,
    exactly like tests/test_run_recorder.py's own pattern), then connects to
    the stream - never concurrently, so this never risks the SAVEPOINT
    interleaving tests/test_api_reports.py's _inline_generate_report()
    docstring warns two coroutines sharing this fixture's one connection
    can hit."""
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    async with respx.mock:
        outcome = await run_claimed_request(
            "Invest in Austin, TX up to $900,000", claim.request_id, 20
        )
    assert outcome.status == JobStatus.COMPLETED

    response = await client.get(
        f"/api/v1/reports/{claim.request_id}/stream", headers=_auth_headers()
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"

    events = await _parse_sse_events(response.text)
    assert [name for name, _ in events] == ["snapshot", "status"]

    snapshot_event, status_event = events
    all_nodes = {
        "ingest_input_agent", "supervisor_agent", "market_data_agent",
        "neighborhood_vibe_agent", "zoning_law_agent", "financial_modeler_agent",
    }
    nodes_in_snapshot = {run["node"] for run in snapshot_event[1]["agent_runs"]}
    assert nodes_in_snapshot == all_nodes
    assert all(run["status"] == "completed" for run in snapshot_event[1]["agent_runs"])

    assert status_event[1] == {"id": str(claim.request_id), "status": "completed"}


@pytest.mark.asyncio
async def test_stream_of_a_failed_job_replays_snapshot_then_closes(db_session, offline_graph):
    """Same terminal-on-connect shape as the completed case, for the other
    terminal status - a job that failed mid-run, replayed from Postgres
    exactly as it was left."""
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    with patch(
        "agents.zoning_law._get_zoning_law_mock_response", side_effect=RuntimeError("boom")
    ):
        async with respx.mock:
            outcome = await run_claimed_request(
                "Invest in Austin, TX up to $900,000", claim.request_id, 20
            )
    assert outcome.status == JobStatus.FAILED

    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get(
            f"/api/v1/reports/{claim.request_id}/stream", headers=_auth_headers()
        )

    assert response.status_code == 200
    events = await _parse_sse_events(response.text)
    assert [name for name, _ in events] == ["snapshot", "status"]
    assert events[1][1] == {"id": str(claim.request_id), "status": "failed"}

    failed_nodes = {
        run["node"] for run in events[0][1]["agent_runs"] if run["status"] == "failed"
    }
    assert "zoning_law_agent" in failed_nodes


@pytest.mark.asyncio
async def test_stream_of_a_freshly_claimed_job_replays_an_empty_snapshot(db_session, client):
    """A job claimed but not yet picked up by any worker has no agent_runs
    rows at all yet - the snapshot must be an empty list, not an error, and
    the job isn't terminal, so this one *does* enter the live loop (proven
    separately, at the generator level, since nothing will ever actually be
    published in this test)."""
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")

    fake_request = _FakeRequest(disconnected_after=0)  # disconnect on the very first check
    pubsub = await _subscribed(claim.request_id)
    chunks = [
        chunk
        async for chunk in _stream_progress_events(fake_request, claim.request_id, pubsub)
    ]
    events = await _parse_sse_events("".join(chunks))
    assert len(events) == 1
    assert events[0] == ("snapshot", {"agent_runs": []})


# --- Live forwarding, heartbeat and cleanup (generator-level) --------------
#
# Driven directly against _stream_progress_events rather than through a real
# concurrent worker: tests/test_api_reports.py's own _inline_generate_report()
# docstring documents why two coroutines each opening their own
# session_scope() against this fixture's one shared test connection can
# interleave SAVEPOINTs out of order and raise. The pattern below (already
# established by tests/test_run_recorder.py's own progress-event test) keeps
# only one side of any concurrency ever touching Postgres: the generator is
# driven step-by-step from this one test coroutine (its own session_scope()
# calls are therefore always sequential, never racing anything), and the one
# background task involved only ever calls publish_progress_event() - Redis
# only, no Postgres at all.


@pytest.mark.asyncio
async def test_stream_forwards_a_live_event_then_closes_once_postgres_agrees_its_terminal(
    db_session,
):
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    request_id = claim.request_id
    await InvestmentRequestRepository(db_session).update_status(request_id, JobStatus.RUNNING)
    await db_session.commit()

    pubsub = await _subscribed(request_id)
    stream = _stream_progress_events(_FakeRequest(), request_id, pubsub)

    snapshot_chunk = await stream.__anext__()
    assert await _parse_sse_events(snapshot_chunk) == [("snapshot", {"agent_runs": []})]

    async def _publish_soon():
        await asyncio.sleep(0.05)
        await publish_progress_event(
            request_id=request_id,
            node="financial_modeler_agent",
            status=JobStatus.COMPLETED,
            sequence=1,
        )

    # A short heartbeat interval for the rest of this test - not to make it
    # faster, but so "nothing happened yet" can be proven by observing a
    # real heartbeat chunk come back from the generator's own internal
    # timeout expiring naturally. Reaching for asyncio.wait_for's own
    # timeout to do that instead - cancelling the generator's currently
    # suspended __anext__() call - throws a CancelledError into it; that
    # propagates straight through this generator's try/finally (closing the
    # pubsub, exactly as a real disconnect should), which leaves the
    # generator itself permanently exhausted - every later __anext__() call
    # on it then raises StopAsyncIteration immediately, same as it would
    # after any other unhandled exception. Confirmed by hitting exactly
    # that failure mode while first writing this test.
    with patch("api.v1.reports.config.SSE_HEARTBEAT_INTERVAL_SECONDS", 0.1):
        publisher = asyncio.create_task(_publish_soon())
        try:
            progress_chunk = await asyncio.wait_for(stream.__anext__(), timeout=2)
        finally:
            await publisher

        [(event_name, payload)] = await _parse_sse_events(progress_chunk)
        assert event_name == "progress"
        assert payload["node"] == "financial_modeler_agent"
        assert payload["status"] == "completed"

        # Postgres still says RUNNING (only this test coroutine ever writes
        # to it, and it hasn't yet) - so the generator, resumed with
        # nothing else pending, must go straight back to waiting rather
        # than ending the stream on this one node's own completion alone.
        # It proves that by actually reaching its own heartbeat timeout and
        # yielding one, not by being cancelled (see above).
        idle_chunk = await asyncio.wait_for(stream.__anext__(), timeout=2)
        assert idle_chunk == ": heartbeat\n\n"

        # Now the job itself finishes - the one write in this whole test
        # that touches Postgres, made sequentially from this same
        # coroutine.
        await InvestmentRequestRepository(db_session).update_status(
            request_id, JobStatus.COMPLETED
        )
        await db_session.commit()

        async def _publish_again():
            await asyncio.sleep(0.05)
            await publish_progress_event(
                request_id=request_id,
                node="financial_modeler_agent",
                status=JobStatus.COMPLETED,
                sequence=2,
            )

        publisher = asyncio.create_task(_publish_again())
        try:
            progress_chunk_2 = await asyncio.wait_for(stream.__anext__(), timeout=2)
        finally:
            await publisher
        assert (await _parse_sse_events(progress_chunk_2))[0][0] == "progress"

        status_chunk = await asyncio.wait_for(stream.__anext__(), timeout=2)
    [(event_name, payload)] = await _parse_sse_events(status_chunk)
    assert event_name == "status"
    assert payload == {"id": str(request_id), "status": "completed"}

    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()

    # The generator's own `finally` ran as part of that StopAsyncIteration -
    # confirm it actually released the subscription, not just returned.
    raw_client = redis.Redis.from_url(config.EVENTS_REDIS_URL)
    try:
        [(_, subscriber_count)] = raw_client.pubsub_numsub(channel_name(request_id))
        assert subscriber_count == 0
    finally:
        raw_client.close()


@pytest.mark.asyncio
async def test_poll_for_terminal_status_retries_until_postgres_catches_up(db_session):
    """Regression test for a real race found by manually running this
    endpoint against a live uvicorn + Celery worker process:
    orchestration/run_recorder.py's _persist_success() writes
    investment_requests.status=COMPLETED in a step that runs *after* the
    astream loop that publishes the run's last node event has already
    exited - see config.SSE_TERMINAL_POLL_ATTEMPTS's own comment for the
    full reasoning. A single read made the instant that last event arrives
    can genuinely still see the *previous* status; before this was found
    and fixed, that meant the stream silently hung until the next
    heartbeat (up to config.SSE_HEARTBEAT_INTERVAL_SECONDS later) instead
    of closing right away.

    Proven here by making the first two reads see the stale status and
    only the third see COMPLETED - deterministically, with no real sleep
    or concurrent task needed. Deliberately *not* modeled as two
    concurrently-running coroutines racing a real publish against a real
    DB write (unlike this file's other live-forwarding tests): that would
    mean this test's own "write Postgres" side and
    _poll_for_terminal_status()'s own polling both opening session_scope()
    calls against this fixture's one shared connection at the same time -
    exactly the SAVEPOINT-interleaving/concurrent-connection-use hazard
    tests/test_api_reports.py's _inline_generate_report() docstring warns
    about. Patching InvestmentRequestRepository.get_by_id() to answer
    stale-then-fresh proves the same retry behavior without going anywhere
    near that hazard."""
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    # The row is already genuinely COMPLETED in Postgres throughout this
    # whole test - the "staleness" being simulated is purely in what the
    # first two reads are made to *answer*, not in when the real write
    # happens (see this test's own docstring on why real timing isn't used
    # here).
    await InvestmentRequestRepository(db_session).update_status(claim.request_id, JobStatus.COMPLETED)
    await db_session.commit()

    real_get_by_id = InvestmentRequestRepository.get_by_id
    call_count = 0

    async def _stale_then_fresh_get_by_id(self, request_id):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return SimpleNamespace(status=JobStatus.RUNNING)
        return await real_get_by_id(self, request_id)

    with patch("api.v1.reports.config.SSE_TERMINAL_POLL_ATTEMPTS", 5), patch(
        "api.v1.reports.config.SSE_TERMINAL_POLL_INTERVAL_SECONDS", 0.01
    ), patch.object(InvestmentRequestRepository, "get_by_id", _stale_then_fresh_get_by_id):
        status = await _poll_for_terminal_status(claim.request_id)

    assert status == JobStatus.COMPLETED
    assert call_count == 3  # gave up trusting a stale read twice, then believed the third


@pytest.mark.asyncio
async def test_poll_for_terminal_status_gives_up_after_its_bounded_budget(db_session):
    """The other half of the same behavior: a node's own COMPLETED/FAILED
    status is usually *not* the run's last event (five of a normal run's
    six per-node COMPLETED events aren't), so this must not retry forever
    waiting for a terminal status that was never coming - it has to give up
    and let the live loop go back to listening instead."""
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    await InvestmentRequestRepository(db_session).update_status(claim.request_id, JobStatus.RUNNING)
    await db_session.commit()

    with patch("api.v1.reports.config.SSE_TERMINAL_POLL_ATTEMPTS", 3), patch(
        "api.v1.reports.config.SSE_TERMINAL_POLL_INTERVAL_SECONDS", 0.01
    ):
        status = await _poll_for_terminal_status(claim.request_id)

    assert status is None


@pytest.mark.asyncio
async def test_stream_sends_a_heartbeat_comment_when_nothing_is_published(db_session):
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    await InvestmentRequestRepository(db_session).update_status(claim.request_id, JobStatus.RUNNING)
    await db_session.commit()

    pubsub = await _subscribed(claim.request_id)
    stream = _stream_progress_events(_FakeRequest(), claim.request_id, pubsub)

    with patch("api.v1.reports.config.SSE_HEARTBEAT_INTERVAL_SECONDS", 0.05):
        await stream.__anext__()  # snapshot
        heartbeat_chunk = await asyncio.wait_for(stream.__anext__(), timeout=2)
    assert heartbeat_chunk == ": heartbeat\n\n"

    await stream.aclose()


@pytest.mark.asyncio
async def test_stream_stops_and_unsubscribes_when_the_client_disconnects(db_session):
    key = _unique_key()
    claim = await claim_request(DEMO_USER_ID, key, "Invest in Austin, TX up to $900,000")
    await InvestmentRequestRepository(db_session).update_status(claim.request_id, JobStatus.RUNNING)
    await db_session.commit()

    pubsub = await _subscribed(claim.request_id)
    # is_disconnected() is only ever called inside the live loop, never
    # while producing the snapshot - so its first call happens on the
    # *second* __anext__(), right after the snapshot has already been
    # yielded. disconnected_after=0 means that very first call reports
    # disconnected.
    stream = _stream_progress_events(_FakeRequest(disconnected_after=0), claim.request_id, pubsub)

    await stream.__anext__()  # snapshot
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()  # is_disconnected() now True -> clean return

    raw_client = redis.Redis.from_url(config.EVENTS_REDIS_URL)
    try:
        [(_, subscriber_count)] = raw_client.pubsub_numsub(channel_name(claim.request_id))
        assert subscriber_count == 0
    finally:
        raw_client.close()
