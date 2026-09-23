import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from db.enums import JobStatus

# Deliberately separate from schema/state.py's OverallGraphState and its
# nested agent-output models: those describe what flows *through the graph*
# and change whenever a node's prompt or topology does. These describe the
# HTTP contract - what a client sends and receives - which must stay stable
# even if the graph's internal shape changes underneath it. Collapsing the
# two would mean every graph refactor is also a breaking API change.


class ReportCreateRequest(BaseModel):
    """Body for POST /api/v1/reports. `query` is the same free-text
    investment prompt app.py/cli.py already pass straight to the graph as a
    HumanMessage (e.g. ingest_input_agent expects to extract a city and a
    budget out of it) - the API does not parse or validate its contents
    beyond non-emptiness, exactly like the existing entrypoints."""

    query: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="A natural-language investment request for ingest_input_agent to parse.",
        examples=["Invest in Austin, TX with a budget of up to $750,000"],
    )


class ReportAccepted(BaseModel):
    """Response for POST /api/v1/reports - `202` for a brand-new job
    (`status` is PENDING, since it's just been claimed and enqueued, not
    run) or `200` for a replay of an existing one (`status` reflects that
    job's actual current state, whatever it is). Either way, callers poll
    `status_url` (GET .../{id}) for progress, exactly as
    poll_until_terminal() already does internally for app.py/cli.py."""

    id: uuid.UUID
    status: JobStatus
    status_url: str


class ReportContent(BaseModel):
    """The finished deliverable, nested inside ReportDetail once a job
    reaches COMPLETED. Numeric aggregates stay Decimal, matching
    db/models.py's Report.average_price etc. (Numeric(14, 2), never float,
    for money - see db/models.py's own comment on why)."""

    model_config = ConfigDict(from_attributes=True)

    content: str
    total_listings: Optional[int] = None
    average_price: Optional[Decimal] = None
    median_price: Optional[Decimal] = None
    highest_listing: Optional[Decimal] = None
    lowest_listing: Optional[Decimal] = None


class ReportDetail(BaseModel):
    """200 response for GET /api/v1/reports/{id}. `report` is None until
    `status` reaches COMPLETED - there is nothing to nest before then."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    city: str
    budget: str
    created_at: datetime
    updated_at: datetime
    report: Optional[ReportContent] = None


class ReportSummary(BaseModel):
    """One row of GET /api/v1/reports's paginated listing - deliberately
    thinner than ReportDetail (no nested report content) since a listing is
    for scanning many jobs at once, not reading one closely."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    city: str
    budget: str
    created_at: datetime


class LoginRequest(BaseModel):
    """Body for POST /api/v1/auth/login. There is no self-registration
    endpoint (see db/models.py's User docstring), so `email` must already
    belong to a migration-seeded row."""

    email: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class RefreshRequest(BaseModel):
    """Body for POST /api/v1/auth/refresh."""

    refresh_token: str = Field(..., min_length=1)


class TokenPair(BaseModel):
    """Response for POST /api/v1/auth/login. `token_type` is always
    "bearer" - what a client puts in the Authorization header
    (`Authorization: Bearer <access_token>`) on every subsequent call to a
    protected route. `refresh_token` is only ever sent back here and to
    /auth/refresh, never required on a report route."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class AccessTokenResponse(BaseModel):
    """Response for POST /api/v1/auth/refresh - deliberately narrower than
    TokenPair: this endpoint mints a new access token from a still-valid
    refresh token but does not rotate the refresh token itself (see
    api/v1/auth.py's refresh() docstring for why), so there is nothing new
    to hand back on that front."""

    access_token: str
    token_type: str = "bearer"


class AgentRunSnapshot(BaseModel):
    """One node's currently known state, as read straight from the
    agent_runs table - one entry of ReportStreamSnapshot.agent_runs. `node`
    (not `node_name`, db/models.py's own column name) so a client can treat
    this the same way it treats a live event/schemas.py ProgressEvent's own
    `node` field, without needing two different key names for "which agent"
    depending on whether it learned about it from the snapshot or live."""

    node: str
    status: JobStatus
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class ReportStreamSnapshot(BaseModel):
    """First event (`event: snapshot`) on GET /api/v1/reports/{id}/stream -
    every node's currently known state, reconstructed from Postgres rather
    than replayed from Redis: events/publisher.py's progress events are
    deliberately ephemeral, so there is no history sitting anywhere to
    replay from the pub/sub side. This is what lets a client
    connecting mid-run, or after the job already finished, see accurate
    current state instead of being left to guess what happened before it
    subscribed."""

    agent_runs: List[AgentRunSnapshot]


class ReportStreamStatus(BaseModel):
    """Final event (`event: status`) on the same stream - the job-level
    outcome, sent exactly once, right before the stream ends. A per-node
    ProgressEvent's own `status` (event: progress) only ever describes one
    node, never the job as a whole, so this is the only place a client
    learns the run is over, without needing to know anything about
    graph.py's topology (e.g. which node happens to run last)."""

    id: uuid.UUID
    status: JobStatus


class ReportListResponse(BaseModel):
    """`total` is the full matching count regardless of `limit`/`offset`, so
    a client can compute how many pages remain without a second request."""

    items: List[ReportSummary]
    total: int
    limit: int
    offset: int
