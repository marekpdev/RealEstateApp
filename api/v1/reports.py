import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from api.v1.schemas import (
    ReportAccepted,
    ReportContent,
    ReportCreateRequest,
    ReportDetail,
    ReportListResponse,
    ReportSummary,
)
from config import config
from db.constants import DEMO_USER_ID
from db.enums import JobStatus
from db.repositories import InvestmentRequestRepository, ReportRepository
from db.session import session_scope
from orchestration.run_recorder import claim_request
from worker.tasks import generate_report

router = APIRouter(prefix="/reports", tags=["Reports"])


def _require_persistence() -> None:
    """Every route here deals in a request_id that has to mean the same
    thing across two separate processes (this one and the Celery worker
    that will eventually run the graph) - unlike app.py/cli.py,
    DB_PERSISTENCE_ENABLED=false has no equivalent synchronous fallback
    here, since there is no HTTP response that could hand back a finished
    report on the spot. 503, not 500: the server itself is healthy, this
    one capability is deliberately turned off."""
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
        "Enqueues a new report-generation job and returns immediately with "
        "its job id and a status URL to poll. Every call creates a brand-new "
        "job right now - this endpoint does not yet accept an Idempotency-Key "
        "header, so a retried identical request is not deduplicated here."
    ),
)
async def create_report(
    payload: ReportCreateRequest, request: Request, response: Response
) -> ReportAccepted:
    _require_persistence()
    # No client-supplied idempotency key exists at this boundary yet - a
    # fresh key per call, exactly like cli.py's own per-invocation
    # uuid.uuid4(), is what makes every POST its own job instead of
    # colliding with (and silently replaying) a previous one.
    idempotency_key = str(uuid.uuid4())
    request_id, should_run = await claim_request(DEMO_USER_ID, idempotency_key)
    if should_run:
        generate_report.delay(payload.query, str(request_id), 20)
    status_url = str(request.url_for("get_report", request_id=request_id))
    # A Location header, not just the same URL in the body: standard
    # practice for a 202/201 pointing at where to check on the accepted
    # work, and free to set alongside a body a client may prefer to parse
    # instead.
    response.headers["Location"] = status_url
    return ReportAccepted(id=request_id, status=JobStatus.PENDING, status_url=status_url)


@router.get(
    "/{request_id}",
    response_model=ReportDetail,
    responses={404: {"description": "No report request with this id exists"}},
    summary="Fetch one report request's current status, and its content once complete",
    name="get_report",
)
async def get_report(request_id: uuid.UUID) -> ReportDetail:
    _require_persistence()
    async with session_scope() as session:
        investment_request = await InvestmentRequestRepository(session).get_by_id_for_user(
            request_id, DEMO_USER_ID
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
) -> ReportListResponse:
    _require_persistence()
    async with session_scope() as session:
        repo = InvestmentRequestRepository(session)
        rows = await repo.list_by_user(DEMO_USER_ID, limit=limit, offset=offset)
        total = await repo.count_by_user(DEMO_USER_ID)

    return ReportListResponse(
        items=[ReportSummary.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
