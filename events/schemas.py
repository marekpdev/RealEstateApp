import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel

from db.enums import JobStatus

# Bumped whenever a field is added, removed, or reinterpreted - never reused
# for a backward-compatible addition. A subscriber can branch on this value
# instead of guessing a message's shape from whichever fields happen to be
# present, the same reason api/v1/schemas.py's DTOs are versioned by URL
# rather than by inspecting the payload.
PROGRESS_EVENT_SCHEMA_VERSION = 1


class ProgressEvent(BaseModel):
    """The wire format published to channel job:{request_id} (see
    events/publisher.py) each time orchestration/run_recorder.py writes an
    agent_runs row. Deliberately its own model, not db/models.py's AgentRun
    reused or reshaped - this is an ephemeral fan-out notification, the
    durable record stays Postgres, and the two are free to diverge as
    either evolves independently.

    `sequence` is assigned by the publisher, monotonically increasing per
    request_id across every event that request_id's run publishes (RUNNING,
    COMPLETED and FAILED events all share one counter) - it is what lets a
    subscriber notice a gap (a message Redis pub/sub itself dropped because
    no one was listening at that instant) even though nothing here
    retransmits a missed event.
    """

    schema_version: int = PROGRESS_EVENT_SCHEMA_VERSION
    request_id: uuid.UUID
    node: str
    status: JobStatus
    timestamp: datetime
    sequence: int
    error_message: Optional[str] = None


class LogEvent(BaseModel):
    """A short human-readable line describing something one node just did
    (e.g. "Successfully extracted target market as 'Miami, FL'"), published
    to the same channel as ProgressEvent so a client can show it under that
    node. `type` is what tells the two message shapes apart on the wire -
    ProgressEvent has no such field. Like a progress event it is ephemeral:
    nothing stores these, so a client that connects after the fact sees the
    node states (from the database) but not these lines."""

    schema_version: int = PROGRESS_EVENT_SCHEMA_VERSION
    type: Literal["log"] = "log"
    request_id: uuid.UUID
    node: str
    message: str
    timestamp: datetime
