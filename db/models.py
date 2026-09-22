import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base
from db.constants import JOB_STATUS_ENUM_NAME
from db.enums import JobStatus

# Native enum column type, shared by every table that stores a JobStatus.
# values_callable is mandatory: without it SQLAlchemy sends the Python member
# *names* ("PENDING") as the Postgres enum labels instead of the lowercase
# .value strings, and every literal comparison against 'pending' silently
# stops matching.
job_status_enum = SAEnum(
    JobStatus,
    name=JOB_STATUS_ENUM_NAME,
    values_callable=lambda enum_cls: [member.value for member in enum_cls],
)


class User(Base):
    """A person who can submit investment requests. Seeded directly in the
    database; there is no self-registration endpoint."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    investment_requests: Mapped[List["InvestmentRequest"]] = relationship(
        back_populates="user", passive_deletes=True
    )


class InvestmentRequest(Base):
    """One 'run the graph for this city/budget' job. The row a background
    worker will enqueue against and an HTTP API will return a job id for."""

    __tablename__ = "investment_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # SHA-256 hex digest of the free-text query this key was first claimed
    # with (see InvestmentRequestRepository.create_idempotent()), captured
    # at claim time rather than derived later - the raw query itself is
    # never persisted as a column, only this fixed-size fingerprint of it.
    # A caller reusing this same idempotency_key later can be compared
    # against it to tell a genuine replay (same hash) from a conflicting
    # reuse of the key for a different request (a different hash) - the
    # HTTP API is the only caller that currently acts on that distinction.
    request_payload_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[JobStatus] = mapped_column(job_status_enum, nullable=False)
    # Both extracted by ingest_input_agent as free text (schema/state.py's
    # IngestInputAgentOutput), not parsed numbers - "$500k" is a valid budget
    # string. Nothing here is queried as a number, so VARCHAR, not Numeric.
    city: Mapped[str] = mapped_column(String(255), nullable=False)
    budget: Mapped[str] = mapped_column(String(100), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="investment_requests")
    report: Mapped[Optional["Report"]] = relationship(
        back_populates="request",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    agent_runs: Mapped[List["AgentRun"]] = relationship(
        back_populates="request",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # A user retrying the same logical request (same Idempotency-Key
        # header) must land on the same row, but two different users are
        # free to reuse the same key independently - the key is scoped per
        # user, not global.
        UniqueConstraint("user_id", "idempotency_key"),
        # Leftmost column user_id also serves as the FK-covering index
        # Postgres doesn't create automatically for foreign keys (unlike
        # MySQL/InnoDB) - a bare ForeignKey() here would otherwise force a
        # full table scan on every cascading delete and every "this user's
        # requests" lookup. created_at DESC as the second column matches the
        # one query shape that needs an order: "this user's requests, newest
        # first" (pagination).
        Index(
            "ix_investment_requests_user_id_created_at",
            "user_id",
            text("created_at DESC"),
        ),
        # Partial: only PENDING/RUNNING rows are ever polled for ("what's
        # still active"), and they're a small, constantly-changing sliver of
        # an otherwise ever-growing table of mostly COMPLETED/FAILED history.
        # Indexing the other 90%+ would just be write overhead paid on every
        # insert for rows this query never touches.
        Index(
            "ix_investment_requests_active",
            "status",
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
        CheckConstraint(
            "attempt_count >= 0", name="attempt_count_non_negative"
        ),
    )


class Report(Base):
    """The finished deliverable for one request. 1:1 with InvestmentRequest -
    a request has at most one report, produced only on success."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # unique=True makes this 1:1 rather than 1:many, and (per the FK-indexing
    # note above) is also the index that covers this foreign key - no
    # separate Index() needed.
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investment_requests.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # The rendered investor-grade markdown (FinancialModelerAgentOutput.
    # financial_report) - what a client actually displays. Free text, never
    # filtered/sorted on, so no normalization benefit.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Market aggregates (RealEstateGatewayModel) normalized into real,
    # queryable columns - Numeric(14, 2), never float, for money: a report
    # search/filter ("investments under $X median") needs indexable,
    # exact-comparison columns, and float's binary rounding makes it wrong
    # for currency regardless.
    total_listings: Mapped[Optional[int]] = mapped_column(Integer)
    average_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    median_price: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    highest_listing: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    lowest_listing: Mapped[Optional[float]] = mapped_column(Numeric(14, 2))
    # The individual listings (PropertyRecord list) are only ever displayed
    # alongside the report they came from, never queried by their own fields
    # (e.g. "find me the listing with 3 bedrooms") - exactly the "store, don't
    # query" case JSONB is for. A normalized properties table would be pure
    # plumbing for a query pattern that doesn't exist.
    raw_properties: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    request: Mapped["InvestmentRequest"] = relationship(back_populates="report")


class AgentRun(Base):
    """One audit row per graph node execution within a request - what a future
    run recorder upserts as each of the six agents starts/finishes."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("investment_requests.id", ondelete="CASCADE"),
        nullable=False,
    )
    # VARCHAR, not the same native enum as `status`: this is graph.py's
    # NODE_REGISTRY key, an open domain that changes whenever the graph's
    # topology does. A native enum would need an ALTER TYPE migration every
    # time a node is renamed or added; config.constants.NodeName already
    # tracks this set in code, so the database doesn't need to constrain it
    # too.
    node_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[JobStatus] = mapped_column(job_status_enum, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    # The node's own partial state-dict output (e.g. a serialized
    # MarketDataAgentOutput) - shape varies per node, so JSONB rather than a
    # column per possible output type across six unrelated agents.
    output: Mapped[Optional[dict]] = mapped_column(JSONB)

    request: Mapped["InvestmentRequest"] = relationship(back_populates="agent_runs")

    __table_args__ = (
        # One row per node per request - re-running the same node (a replay,
        # or a future upsert path) updates this row rather than inserting a
        # second one. request_id leads, so this is also the FK-covering index
        # for the same reason as investment_requests.user_id above.
        UniqueConstraint("request_id", "node_name"),
    )
