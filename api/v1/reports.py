import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from api.v1.schemas import (
    ReportAccepted,
    ReportContent,
    ReportCreateRequest,
    ReportDetail,
    ReportListResponse,
    ReportSummary,
)
from auth.api_keys import get_current_caller
from config import config
from db.enums import JobStatus
from db.models import User
from db.repositories import InvestmentRequestRepository, ReportRepository
from db.session import session_scope
from orchestration.run_recorder import claim_request
from rate_limit import enforce_rate_limit
from worker.tasks import generate_report

router = APIRouter(prefix="/reports", tags=["Reports"])

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"


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
