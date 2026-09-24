import contextlib
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app
import server
import services.report_api_client as report_api_client
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.models import InvestmentRequest, Report
from orchestration.run_recorder import run_claimed_request
from services.report_api_client import ReportAPIClient, ReportAPIError

# app.py talks to this app's own /api/v1 surface over real HTTP now (see
# services/report_api_client.py) instead of importing graph.py/
# orchestration/run_recorder.py directly - these tests exercise that real
# HTTP path end to end, through the same ASGI app tests/test_api_reports.py
# already tests directly, rather than mocking the client away. The lower,
# ReportAPIClient-only tests at the bottom of this file cover the
# login/refresh/retry mechanics in isolation, against a fake base_url.


def _unique_key(label: str) -> str:
    return f"{label}-{uuid.uuid4()}"


@pytest.fixture(autouse=True)
def fast_polling():
    """The inlined "worker" (run_claimed_request(), patched exactly like
    tests/test_app_wiring.py's own fixture of this name) still needs a fast
    poll interval for cli.py-style callers; app.py itself no longer polls at
    all (see services/report_api_client.py's stream_report()), but still
    needs a short overall deadline - REPORT_POLL_TIMEOUT_SECONDS is now the
    budget app.py's asyncio.wait_for() gives the whole stream before giving
    up - or these tests would run at real time (config.py's 1s/300s
    production defaults)."""
    with patch("orchestration.run_recorder.config.REPORT_POLL_INTERVAL_SECONDS", 0.01), \
         patch("app.config.REPORT_POLL_TIMEOUT_SECONDS", 2.0):
        yield


@pytest.fixture
def offline_graph():
    """Every agent mock flag on, plus the UI log translator offline - the
    same set tests/test_app_wiring.py and tests/test_api_reports.py patch to
    run the compiled graph with zero real network calls."""
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
async def bound_report_api_client():
    """Points services.report_api_client's module-level singleton at the
    real FastAPI app over an in-process ASGI transport - exactly like
    tests/test_api_reports.py's own `client` fixture, just wrapped in the
    same ReportAPIClient app.py itself uses, so app.handle_query() exercises
    the real routes (auth, idempotency, rate limiting) instead of a real TCP
    connection to config.API_BASE_URL, which nothing listens on in tests."""
    transport = ASGITransport(app=server.raw_app)
    http_client = AsyncClient(transport=transport, base_url="http://test")
    test_client = ReportAPIClient(http_client)
    with patch.object(report_api_client, "_client", test_client):
        yield test_client
    await http_client.aclose()


@contextlib.contextmanager
def _inline_worker():
    """Stands in for a live Celery worker, the same idea as
    tests/test_app_wiring.py's _inline_worker() and
    tests/test_api_reports.py's _inline_generate_report(): captures (not
    schedules) the exact continuation a real worker/tasks.py's
    generate_report task calls, then runs it to completion synchronously
    before app.py's own stream connects - never concurrently with it, since
    both would otherwise open competing SAVEPOINTs on the one connection
    tests/conftest.py's db_session fixture rebinds every session_scope()
    call to (see either sibling fixture's own docstring for the full
    argument). A side effect worth naming: by the time app._await_report()
    actually opens GET .../stream below, the job is already fully
    COMPLETED/FAILED in Postgres, so the real SSE endpoint answers with its
    already-terminal snapshot+status shortcut (api/v1/reports.py's own
    `if job_status in _TERMINAL_STATUSES` branch) rather than ever entering
    its live per-node loop - correctness here (the right rows land in
    Postgres, the right message reaches the user) is what these tests prove;
    genuinely live, progressive rendering was verified manually instead,
    against real separate uvicorn and Celery worker processes - the same
    split tests/test_api_reports_stream.py already draws for the identical
    reason."""
    pending_run: dict = {}

    def _fake_delay(raw_query: str, request_id: str, recursion_limit: int):
        pending_run["coro"] = run_claimed_request(
            raw_query, uuid.UUID(request_id), recursion_limit
        )

    real_await_report = app._await_report

    async def _run_worker_then_await(client, request_id):
        coro = pending_run.pop("coro", None)
        if coro is not None:
            await coro
        return await real_await_report(client, request_id)

    with patch("api.v1.reports.generate_report") as mock_task, \
         patch.object(app, "_await_report", side_effect=_run_worker_then_await):
        mock_task.delay.side_effect = _fake_delay
        yield mock_task


@pytest.mark.asyncio
async def test_handle_query_persists_full_run_through_the_real_api(
    db_session, offline_graph, bound_report_api_client
):
    """app.handle_query() logs in, POSTs to /api/v1/reports, and polls GET
    .../{id} - all real HTTP calls against the real routes - landing the
    same rows in the database a direct graph run always has."""
    key = _unique_key("api-client-full-run")

    with _inline_worker():
        async with respx.mock:
            await app.handle_query(
                "Invest in Austin, TX up to $900,000", idempotency_key=key
            )

    request = await db_session.scalar(
        select(InvestmentRequest).where(InvestmentRequest.idempotency_key == key)
    )
    assert request is not None
    assert request.user_id == DEMO_USER_ID
    assert request.status == JobStatus.COMPLETED
    assert request.city == "Los Angeles, CA"  # from the mocked ingest_input output

    reports = (
        await db_session.execute(select(Report).where(Report.request_id == request.id))
    ).scalars().all()
    assert len(reports) == 1


@pytest.mark.asyncio
async def test_handle_query_full_run_renders_one_step_per_agent_node(
    db_session, offline_graph, bound_report_api_client
):
    """app.py no longer polls GET /api/v1/reports/{id} in a loop - it
    watches GET /api/v1/reports/{id}/stream and renders each node's
    progress as a Chainlit step (see app._render_node_state()). Under
    _inline_worker() the six-node graph has already finished by the time
    the stream connects (see that fixture's own docstring for why), so this
    proves the stream's `event: snapshot` path - reconstructed from
    Postgres - drives the same rendering a genuinely live `event: progress`
    would; that live path itself is exercised directly by
    test_await_report_renders_progress_events_as_they_arrive below, and end
    to end only by manual verification against real separate uvicorn and
    Celery worker processes."""
    key = _unique_key("api-client-step-rendering")

    with patch("app.log_agent_header", new_callable=AsyncMock) as mock_header, \
         patch("app.log_agent_footer", new_callable=AsyncMock) as mock_footer, \
         _inline_worker():
        async with respx.mock:
            await app.handle_query(
                "Invest in Austin, TX up to $900,000", idempotency_key=key
            )

    rendered_nodes = {call.args[0] for call in mock_header.await_args_list}
    assert rendered_nodes == {
        "ingest_input_agent",
        "supervisor_agent",
        "market_data_agent",
        "neighborhood_vibe_agent",
        "zoning_law_agent",
        "financial_modeler_agent",
    }
    assert mock_footer.await_count == len(rendered_nodes)


@pytest.mark.asyncio
async def test_handle_query_reuses_one_login_across_submit_and_every_poll(
    db_session, offline_graph, bound_report_api_client
):
    """One handle_query() call makes several HTTP requests (create + at
    least one poll); a second, independent query reuses the same client.
    Across all of it, exactly one login should happen - spying on the real
    _login() (via wraps=, so it still runs normally) is what actually proves
    that, rather than trusting that two tokens happen to differ."""
    with patch.object(
        bound_report_api_client, "_login", wraps=bound_report_api_client._login
    ) as spy_login:
        with _inline_worker():
            async with respx.mock:
                await app.handle_query(
                    "Invest in Austin, TX", idempotency_key=_unique_key("api-client-single-login")
                )
        with _inline_worker():
            async with respx.mock:
                await app.handle_query(
                    "Invest in Austin, TX",
                    idempotency_key=_unique_key("api-client-single-login-2"),
                )

    assert spy_login.call_count == 1


@pytest.mark.asyncio
async def test_handle_query_replay_renders_report_explicitly(
    db_session, offline_graph, bound_report_api_client
):
    """A replayed job (the same Idempotency-Key *and* the same query text
    reused, the API answers 200 with an already-COMPLETED status) must
    still show the user the report - app.py renders it itself, exactly
    once, since there is no worker run whose side effect could show it this
    time either. The query text must match: claim_request() compares the
    stored request_payload_hash against every non-created replay regardless
    of status (see orchestration/run_recorder.py's own claim_request()), so
    a genuinely different query on the same key now correctly gets a 409
    from the real API - something app.py's own private path used to ignore
    entirely (see the payload-conflict test below)."""
    key = _unique_key("api-client-replay")
    query = "Invest in Austin, TX up to $900,000"

    with _inline_worker():
        async with respx.mock:
            await app.handle_query(query, idempotency_key=key)

    with patch("app.render_financial_report", new_callable=AsyncMock) as mock_render, \
         patch("api.v1.reports.generate_report") as mock_task:
        await app.handle_query(query, idempotency_key=key)

    mock_task.delay.assert_not_called()
    mock_render.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_query_surfaces_a_payload_conflict_as_an_api_error(
    db_session, offline_graph, bound_report_api_client
):
    """Reusing an Idempotency-Key with genuinely different query text is a
    409 from the real API (api/v1/reports.py's create_report(), via
    claim_request()'s own payload_conflict) - a case app.py's old private
    path silently never checked at all, since it called claim_request()
    directly and never inspected that field. Going through the same HTTP
    API every other caller uses is what surfaces this now."""
    key = _unique_key("api-client-payload-conflict")

    with _inline_worker():
        async with respx.mock:
            await app.handle_query("Invest in Austin, TX up to $900,000", idempotency_key=key)

    with patch("app.log_message", new_callable=AsyncMock) as mock_log, \
         patch("app.render_financial_report", new_callable=AsyncMock) as mock_render:
        await app.handle_query("A completely different query", idempotency_key=key)

    mock_log.assert_awaited_once()
    assert "different request body" in mock_log.await_args.args[0]
    mock_render.assert_not_called()


@pytest.mark.asyncio
async def test_handle_query_surfaces_a_failed_run_without_rendering_a_report(
    db_session, offline_graph, bound_report_api_client
):
    """A structurally FAILED job (a node raised partway through the graph)
    must tell the user something went wrong, and must never call
    render_financial_report - there is no report to show. Forces the
    failure by making the (otherwise-mocked, thanks to offline_graph) zoning
    law agent raise, the same technique tests/test_run_recorder.py already
    uses for the same purpose."""
    key = _unique_key("api-client-failed-run")

    with patch(
        "agents.zoning_law._get_zoning_law_mock_response", side_effect=RuntimeError("boom")
    ), patch("app.render_financial_report", new_callable=AsyncMock) as mock_render, \
         patch("app.log_message", new_callable=AsyncMock) as mock_log, \
         _inline_worker():
        async with respx.mock:
            await app.handle_query("Invest in Austin, TX up to $900,000", idempotency_key=key)

    mock_log.assert_awaited_once()
    assert "failed" in mock_log.await_args.args[0].lower()
    mock_render.assert_not_called()


@pytest.mark.asyncio
async def test_handle_query_tells_user_when_polling_times_out(
    db_session, offline_graph, bound_report_api_client
):
    """A job that's still PENDING/RUNNING when REPORT_POLL_TIMEOUT_SECONDS
    elapses must tell the user it's still working, not claim failure - the
    worker never ran at all here (generate_report.delay() is mocked out and
    never inlined), so the job simply never leaves PENDING."""
    key = _unique_key("api-client-timeout")

    with patch("api.v1.reports.generate_report") as mock_task, \
         patch("app.config.REPORT_POLL_TIMEOUT_SECONDS", 0.05), \
         patch("app.render_financial_report", new_callable=AsyncMock) as mock_render, \
         patch("app.log_message", new_callable=AsyncMock) as mock_log:
        mock_task.delay.return_value = None
        async with respx.mock:
            await app.handle_query("Invest in Austin, TX", idempotency_key=key)

    mock_log.assert_awaited_once()
    assert "longer than expected" in mock_log.await_args.args[0]
    mock_render.assert_not_called()


@pytest.mark.asyncio
async def test_handle_query_surfaces_persistence_disabled_as_an_api_error(
    db_session, bound_report_api_client
):
    """DB_PERSISTENCE_ENABLED=false is enforced by the API itself now
    (api/v1/reports.py's _require_persistence dependency) - app.py has no
    synchronous fallback of its own any more (it no longer imports graph.py
    at all), so this must surface as a plain API error, not a silent no-op
    or a crash."""
    key = _unique_key("api-client-persistence-disabled")

    with patch("api.v1.reports.config.DB_PERSISTENCE_ENABLED", False), \
         patch("app.log_message", new_callable=AsyncMock) as mock_log:
        async with respx.mock:
            await app.handle_query("Invest in Austin, TX", idempotency_key=key)

    mock_log.assert_awaited_once()
    message = mock_log.await_args.args[0]
    assert "API rejected this request" in message
    assert "DB_PERSISTENCE_ENABLED" in message


@pytest.mark.asyncio
async def test_handle_query_surfaces_a_connection_failure_instead_of_crashing():
    """A transport-level failure (the API unreachable at all, not just
    answering with an error status) must be surfaced to the user rather than
    propagating out of on_message() and killing the Chainlit session.
    _submit_and_await() is mocked directly here, not the client - nothing in
    this test touches the real API, so the module-level singleton
    bound_report_api_client would otherwise patch is irrelevant."""
    with patch("app._submit_and_await", new_callable=AsyncMock) as mock_submit, \
         patch("app.log_message", new_callable=AsyncMock) as mock_log:
        mock_submit.side_effect = httpx.ConnectError("Connection refused")
        await app.handle_query("Invest in Austin, TX", idempotency_key="irrelevant")

    mock_log.assert_awaited_once()
    assert "Couldn't reach the API" in mock_log.await_args.args[0]


# --- app.py's SSE consumer, in isolation ------------------------------------
# These exercise app._await_report()/app._render_node_state() directly
# against a fake stream_report() async generator, rather than a real
# GET .../stream connection - precise control over the exact event sequence
# (including a genuinely *live* progress event, arriving after the snapshot)
# that would otherwise need a real concurrent worker publishing while this
# same process reads, which tests/conftest.py's shared-connection db_session
# fixture can't safely do inside one test (see _inline_worker()'s own
# docstring above for the full argument).


async def _fake_stream(events):
    for event_name, data in events:
        yield event_name, data


@pytest.mark.asyncio
async def test_await_report_renders_progress_events_as_they_arrive():
    """A fresh job's stream: an empty snapshot (nothing has run yet), then
    one node's own RUNNING followed by COMPLETED - exactly the live shape a
    real worker publishes while this generator is still connected, not a
    replayed snapshot. _await_report() must render each one as it arrives
    and stop the moment `status` ends the run, without needing a final GET
    itself (that's _submit_and_await()'s job, one layer up)."""
    events = [
        ("snapshot", '{"agent_runs": []}'),
        (
            "progress",
            '{"node": "ingest_input_agent", "status": "running"}',
        ),
        (
            "progress",
            '{"node": "ingest_input_agent", "status": "completed"}',
        ),
        ("status", '{"id": "abc", "status": "completed"}'),
    ]
    fake_client = AsyncMock()
    fake_client.stream_report = lambda request_id: _fake_stream(events)

    with patch("app.log_agent_header", new_callable=AsyncMock) as mock_header, \
         patch("app.log_agent_footer", new_callable=AsyncMock) as mock_footer:
        await app._await_report(fake_client, "abc")

    # log_agent_header() is called once per progress event for this node
    # (RUNNING, then COMPLETED) - it's the real function's own job (see
    # logger/logger.py's active_agent_steps dict) to no-op the second call
    # since the step is already open; _render_node_state() doesn't dedupe
    # this itself, so both calls land on the mock here.
    assert mock_header.await_count == 2
    mock_header.assert_awaited_with("ingest_input_agent", "⚙️ Node: Ingest Input Agent")
    mock_footer.assert_awaited_once_with("ingest_input_agent")


@pytest.mark.asyncio
async def test_render_node_state_opens_header_only_while_running():
    with patch("app.log_agent_header", new_callable=AsyncMock) as mock_header, \
         patch("app.log_agent_footer", new_callable=AsyncMock) as mock_footer:
        await app._render_node_state("market_data_agent", "running", None)

    mock_header.assert_awaited_once_with("market_data_agent", "⚙️ Node: Market Data Agent")
    mock_footer.assert_not_awaited()


@pytest.mark.asyncio
async def test_render_node_state_closes_the_step_on_completion():
    with patch("app.log_agent_header", new_callable=AsyncMock) as mock_header, \
         patch("app.log_agent_footer", new_callable=AsyncMock) as mock_footer:
        await app._render_node_state("market_data_agent", "completed", None)

    mock_header.assert_awaited_once()
    mock_footer.assert_awaited_once_with("market_data_agent")


@pytest.mark.asyncio
async def test_render_node_state_shows_the_error_before_closing_on_failure():
    with patch("app.log_agent_header", new_callable=AsyncMock), \
         patch("app.log_agent_content", new_callable=AsyncMock) as mock_content, \
         patch("app.log_agent_footer", new_callable=AsyncMock) as mock_footer:
        await app._render_node_state("zoning_law_agent", "failed", "boom")

    mock_content.assert_awaited_once_with("zoning_law_agent", "❌ boom")
    mock_footer.assert_awaited_once_with("zoning_law_agent")


# --- ReportAPIClient: login/refresh/retry mechanics, in isolation ----------
# These don't go through the real FastAPI app at all - a fake base_url with
# respx intercepting every call gives precise control over the exact
# response sequence (a 401 followed by a success) that's awkward to force
# through the real login/expiry machinery.

_FAKE_BASE_URL = "http://fake-api.test"


@pytest.mark.asyncio
async def test_client_logs_in_once_and_reuses_the_access_token():
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        async with respx.mock:
            login_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login").respond(
                json={"access_token": "at-1", "refresh_token": "rt-1", "token_type": "bearer"}
            )
            respx.get(f"{_FAKE_BASE_URL}/api/v1/reports/abc").respond(
                json={"id": "abc", "status": "completed", "report": None}
            )

            await client.get_report("abc")
            await client.get_report("abc")

    assert login_route.call_count == 1


@pytest.mark.asyncio
async def test_client_refreshes_an_expired_access_token_and_retries():
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        client._access_token = "stale-token"
        client._refresh_token = "rt-1"

        async with respx.mock:
            refresh_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/refresh").respond(
                json={"access_token": "fresh-token", "token_type": "bearer"}
            )
            login_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login")
            report_route = respx.get(f"{_FAKE_BASE_URL}/api/v1/reports/abc")
            report_route.side_effect = [
                httpx.Response(401, json={"detail": "Token expired."}),
                httpx.Response(200, json={"id": "abc", "status": "completed", "report": None}),
            ]

            result = await client.get_report("abc")

    assert result["status"] == "completed"
    assert refresh_route.call_count == 1
    assert login_route.call_count == 0


@pytest.mark.asyncio
async def test_client_falls_back_to_login_when_refresh_itself_fails():
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        client._access_token = "stale-token"
        client._refresh_token = "expired-refresh"

        async with respx.mock:
            respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/refresh").respond(
                status_code=401, json={"detail": "Refresh token expired."}
            )
            login_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login").respond(
                json={"access_token": "new-access", "refresh_token": "new-refresh", "token_type": "bearer"}
            )
            report_route = respx.get(f"{_FAKE_BASE_URL}/api/v1/reports/abc")
            report_route.side_effect = [
                httpx.Response(401, json={"detail": "Token expired."}),
                httpx.Response(200, json={"id": "abc", "status": "completed", "report": None}),
            ]

            result = await client.get_report("abc")

    assert result["status"] == "completed"
    assert login_route.call_count == 1


@pytest.mark.asyncio
async def test_client_raises_report_api_error_with_detail_on_conflict():
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        async with respx.mock:
            respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login").respond(
                json={"access_token": "at", "refresh_token": "rt", "token_type": "bearer"}
            )
            respx.post(f"{_FAKE_BASE_URL}/api/v1/reports").respond(
                status_code=409,
                json={"detail": "Idempotency-Key already used with a different request body."},
            )

            with pytest.raises(ReportAPIError) as excinfo:
                await client.create_report("a query", "some-key")

    assert excinfo.value.status_code == 409
    assert "different request body" in excinfo.value.detail


@pytest.mark.asyncio
async def test_iter_sse_events_parses_events_and_skips_heartbeat_comments():
    """The client-side mirror of api/v1/reports.py's own _sse() writer and
    its `: heartbeat` comment line - both must round-trip through this
    parser exactly, since it's the only thing standing between the raw wire
    bytes and app._await_report()'s json.loads() calls."""
    from services.report_api_client import _iter_sse_events

    async def _lines():
        for line in [
            "event: snapshot",
            'data: {"agent_runs": []}',
            "",
            ": heartbeat",
            "event: progress",
            'data: {"node": "ingest_input_agent", "status": "running"}',
            "",
            "event: status",
            'data: {"id": "abc", "status": "completed"}',
            "",
        ]:
            yield line

    events = [event async for event in _iter_sse_events(_lines())]

    assert events == [
        ("snapshot", '{"agent_runs": []}'),
        ("progress", '{"node": "ingest_input_agent", "status": "running"}'),
        ("status", '{"id": "abc", "status": "completed"}'),
    ]


def _sse_body(*events) -> bytes:
    """Builds a raw SSE response body from (event, data) pairs, matching
    api/v1/reports.py's own _sse() wire format exactly."""
    return "".join(f"event: {name}\ndata: {data}\n\n" for name, data in events).encode()


@pytest.mark.asyncio
async def test_stream_report_yields_parsed_events_over_a_real_connection():
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        async with respx.mock:
            respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login").respond(
                json={"access_token": "at", "refresh_token": "rt", "token_type": "bearer"}
            )
            respx.get(f"{_FAKE_BASE_URL}/api/v1/reports/abc/stream").respond(
                200,
                content=_sse_body(
                    ("snapshot", '{"agent_runs": []}'),
                    ("status", '{"id": "abc", "status": "completed"}'),
                ),
                headers={"content-type": "text/event-stream"},
            )

            events = [event async for event in client.stream_report("abc")]

    assert events == [
        ("snapshot", '{"agent_runs": []}'),
        ("status", '{"id": "abc", "status": "completed"}'),
    ]


@pytest.mark.asyncio
async def test_stream_report_refreshes_an_expired_access_token_and_retries():
    """Same contract as test_client_refreshes_an_expired_access_token_and_
    retries above, for the streaming call - a 401 on the connection's own
    status line, discovered before any SSE body is read, triggers a
    refresh and exactly one retried connection attempt."""
    async with httpx.AsyncClient(base_url=_FAKE_BASE_URL) as http_client:
        client = ReportAPIClient(http_client)
        client._access_token = "stale-token"
        client._refresh_token = "rt-1"

        async with respx.mock:
            refresh_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/refresh").respond(
                json={"access_token": "fresh-token", "token_type": "bearer"}
            )
            login_route = respx.post(f"{_FAKE_BASE_URL}/api/v1/auth/login")
            stream_route = respx.get(f"{_FAKE_BASE_URL}/api/v1/reports/abc/stream")
            stream_route.side_effect = [
                httpx.Response(401, json={"detail": "Token expired."}),
                httpx.Response(
                    200,
                    content=_sse_body(("status", '{"id": "abc", "status": "completed"}')),
                    headers={"content-type": "text/event-stream"},
                ),
            ]

            events = [event async for event in client.stream_report("abc")]

    assert events == [("status", '{"id": "abc", "status": "completed"}')]
    assert refresh_route.call_count == 1
    assert login_route.call_count == 0


@pytest.mark.asyncio
async def test_dispose_client_clears_the_singleton():
    from services.report_api_client import dispose_client, get_client

    first = get_client()
    await dispose_client()
    second = get_client()

    assert first is not second
    await dispose_client()
