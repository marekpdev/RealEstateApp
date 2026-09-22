import contextlib
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
import respx
from httpx import ASGITransport, AsyncClient

import server
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from orchestration.run_recorder import run_claimed_request


@pytest.fixture(autouse=True)
def fast_polling():
    """Not used by these tests directly (the API itself never polls - a
    client would), but keeps this file's inline worker consistent with
    tests/test_app_wiring.py's own fixture of the same name."""
    with patch("orchestration.run_recorder.config.REPORT_POLL_INTERVAL_SECONDS", 0.01):
        yield


@pytest.fixture
def offline_graph():
    """Every agent mock flag on, plus the UI log translator offline - the
    same set tests/test_app_wiring.py patches to run the compiled graph
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


@contextlib.contextmanager
def _inline_generate_report():
    """Stands in for a live Celery worker, exactly like
    tests/test_app_wiring.py's _inline_worker(): patches
    api.v1.reports.generate_report.delay to capture (not schedule) the
    exact continuation a real worker/tasks.py's generate_report task calls
    (run_claimed_request()), so the caller can await it to completion
    sequentially, after the POST that enqueued it has already returned -
    never concurrently with a request sharing the same db_session-rebound
    connection. test_app_wiring.py's own _inline_worker() docstring
    documents why: two coroutines each opening their own session_scope()
    against that one shared connection can interleave their SAVEPOINTs out
    of strict LIFO order and raise, if the event loop switches between them
    at an await point."""
    pending_run: dict = {}

    def _fake_delay(raw_query: str, request_id: str, recursion_limit: int):
        pending_run["coro"] = run_claimed_request(
            raw_query, uuid.UUID(request_id), recursion_limit
        )

    with patch("api.v1.reports.generate_report") as mock_task:
        mock_task.delay.side_effect = _fake_delay
        yield pending_run


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=server.raw_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _idempotency_headers(label: str = "idem") -> dict:
    return {"Idempotency-Key": f"{label}-{uuid.uuid4()}"}


@pytest.mark.asyncio
async def test_create_report_returns_202_with_job_id_and_status_url(db_session, client):
    with patch("api.v1.reports.generate_report") as mock_task:
        response = await client.post(
            "/api/v1/reports",
            json={"query": "Invest in Austin, TX up to $750,000"},
            headers=_idempotency_headers(),
        )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["status_url"].endswith(f"/api/v1/reports/{body['id']}")
    assert response.headers["location"] == body["status_url"]
    mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_create_report_rejects_empty_query_with_422(db_session, client):
    response = await client.post(
        "/api/v1/reports", json={"query": ""}, headers=_idempotency_headers()
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_report_rejects_missing_query_with_422(db_session, client):
    response = await client.post(
        "/api/v1/reports", json={}, headers=_idempotency_headers()
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_report_rejects_missing_idempotency_key_with_422(db_session, client):
    response = await client.post(
        "/api/v1/reports", json={"query": "Invest in Austin, TX up to $750,000"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_report_same_key_same_payload_replays_with_200(db_session, client):
    headers = _idempotency_headers()
    body_payload = {"query": "Invest in Austin, TX up to $750,000"}
    with patch("api.v1.reports.generate_report") as mock_task:
        first = await client.post("/api/v1/reports", json=body_payload, headers=headers)
        second = await client.post("/api/v1/reports", json=body_payload, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["status_url"] == first.json()["status_url"]
    # The replay still re-enqueues: the job is only ever claimed once (one
    # row, one id), but its status is still PENDING in this test (nothing
    # ever runs generate_report.delay's mocked-out continuation), so
    # should_run is True both times - safe, since the worker's own
    # try_claim_run() atomically refuses a second physical run of a job
    # another attempt already owns or finished (see
    # orchestration/run_recorder.py's run_claimed_request()).
    assert mock_task.delay.call_count == 2


@pytest.mark.asyncio
async def test_create_report_same_key_different_payload_returns_409(db_session, client):
    headers = _idempotency_headers()
    with patch("api.v1.reports.generate_report") as mock_task:
        first = await client.post(
            "/api/v1/reports",
            json={"query": "Invest in Austin, TX up to $750,000"},
            headers=headers,
        )
        second = await client.post(
            "/api/v1/reports",
            json={"query": "Invest in a completely different city"},
            headers=headers,
        )

    assert first.status_code == 202
    assert second.status_code == 409
    # The conflicting call must not touch the job at all - no second claim,
    # no second enqueue.
    mock_task.delay.assert_called_once()


@pytest.mark.asyncio
async def test_get_report_returns_404_for_unknown_id(db_session, client):
    response = await client.get(f"/api/v1/reports/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reports_collection_rejects_wrong_method_with_405(db_session, client):
    response = await client.put("/api/v1/reports", json={"query": "x"})
    assert response.status_code == 405


@pytest.mark.asyncio
async def test_full_round_trip_create_then_fetch_completed_report(
    db_session, offline_graph, client
):
    """A curl-style round trip: POST creates a job, and once the (inlined,
    in these tests) worker finishes it, GET returns the full report -
    covering the same ground this phase's own manual verification did
    against a real uvicorn + Celery worker process."""
    with _inline_generate_report() as pending_run:
        async with respx.mock:
            create_response = await client.post(
                "/api/v1/reports",
                json={"query": "Invest in Austin, TX up to $900,000"},
                headers=_idempotency_headers(),
            )
            assert create_response.status_code == 202
            job_id = create_response.json()["id"]

            # Run the "worker" to completion before issuing the follow-up
            # GET - see _inline_generate_report()'s own docstring on why
            # this must be sequential, not concurrent.
            await pending_run["coro"]

        get_response = await client.get(f"/api/v1/reports/{job_id}")

    assert get_response.status_code == 200
    body = get_response.json()
    assert body["status"] == "completed"
    assert body["city"] == "Los Angeles, CA"  # from the mocked ingest_input output
    assert body["report"]["content"]
    assert body["report"]["total_listings"] is not None


@pytest.mark.asyncio
async def test_list_reports_is_paginated_and_newest_first(db_session, client):
    from db.repositories import InvestmentRequestRepository

    repo = InvestmentRequestRepository(db_session)
    created_ids = []
    for i in range(3):
        request, _ = await repo.create_idempotent(
            user_id=DEMO_USER_ID,
            idempotency_key=f"list-test-{uuid.uuid4()}",
            raw_query=f"Invest in City {i}",
            city=f"City {i}",
            budget="$1",
            status=JobStatus.PENDING,
        )
        created_ids.append(request.id)
    await db_session.commit()

    response = await client.get("/api/v1/reports?limit=2&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert len(body["items"]) == 2
    assert body["total"] >= 3

    returned_ids = {item["id"] for item in body["items"]}
    assert returned_ids.issubset({str(i) for i in created_ids} | returned_ids)


@pytest.mark.asyncio
async def test_reports_endpoints_return_503_when_persistence_disabled(client):
    with patch("api.v1.reports.config.DB_PERSISTENCE_ENABLED", False):
        response = await client.post(
            "/api/v1/reports", json={"query": "x"}, headers=_idempotency_headers()
        )
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_swagger_and_openapi_render(client):
    docs_response = await client.get("/docs")
    assert docs_response.status_code == 200

    openapi_response = await client.get("/openapi.json")
    assert openapi_response.status_code == 200
    paths = openapi_response.json()["paths"]
    assert "/api/v1/reports" in paths
    assert "/api/v1/reports/{request_id}" in paths
