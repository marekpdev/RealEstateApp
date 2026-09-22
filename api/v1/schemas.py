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
    """202 response for POST /api/v1/reports. `status` is always PENDING at
    this point - the job has been claimed and enqueued, not run - callers
    poll `status_url` (GET .../{id}) for progress, exactly as
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


class ReportListResponse(BaseModel):
    """`total` is the full matching count regardless of `limit`/`offset`, so
    a client can compute how many pages remain without a second request."""

    items: List[ReportSummary]
    total: int
    limit: int
    offset: int
