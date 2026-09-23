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
    """Both the inlined "worker" (run_claimed_request(), patched exactly
    like tests/test_app_wiring.py's own fixture of this name) and app.py's
    own HTTP poll loop need a fast interval, or these tests would run at
    real time (config.py's 1s/300s production defaults)."""
    with patch("orchestration.run_recorder.config.REPORT_POLL_INTERVAL_SECONDS", 0.01), \
         patch("app.config.REPORT_POLL_INTERVAL_SECONDS", 0.01), \
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
    before app.py's own first poll - never concurrently with it, since both
    would otherwise open competing SAVEPOINTs on the one connection
    tests/conftest.py's db_session fixture rebinds every session_scope()
    call to (see either sibling fixture's own docstring for the full
    argument)."""
    pending_run: dict = {}

    def _fake_delay(raw_query: str, request_id: str, recursion_limit: int):
        pending_run["coro"] = run_claimed_request(
            raw_query, uuid.UUID(request_id), recursion_limit
        )

    real_poll_until_terminal = app._poll_until_terminal

    async def _run_worker_then_poll(client, request_id):
        coro = pending_run.pop("coro", None)
        if coro is not None:
            await coro
        return await real_poll_until_terminal(client, request_id)

    with patch("api.v1.reports.generate_report") as mock_task, \
         patch.object(app, "_poll_until_terminal", side_effect=_run_worker_then_poll):
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
async def test_dispose_client_clears_the_singleton():
    from services.report_api_client import dispose_client, get_client

    first = get_client()
    await dispose_client()
    second = get_client()

    assert first is not second
    await dispose_client()
