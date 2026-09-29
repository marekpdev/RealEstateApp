import contextlib
import contextvars
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.enums import JobStatus
from events.redis_client import get_events_redis_client
from events.schemas import LogEvent, ProgressEvent

logger = logging.getLogger(__name__)

# Which request the code running right now is working on behalf of. Set for
# the duration of a graph run so that code deep inside an agent, which has
# no request id passed down to it, can still publish a line for the right
# job. A ContextVar (not a global) so concurrent runs in one process never
# see each other's value, and asyncio tasks started during a run inherit it.
_current_request_id: contextvars.ContextVar[Optional[uuid.UUID]] = contextvars.ContextVar(
    "events_current_request_id", default=None
)


@contextlib.contextmanager
def request_context(request_id: uuid.UUID):
    """Marks everything run inside the `with` block as belonging to
    `request_id` for publish_log_event_for_current_request()."""
    token = _current_request_id.set(request_id)
    try:
        yield
    finally:
        _current_request_id.reset(token)


def channel_name(request_id: uuid.UUID) -> str:
    """The Redis pub/sub channel one request_id's progress events publish
    to, and a future streaming HTTP endpoint would subscribe to. A plain
    string, not a Redis key: PUBLISH/SUBSCRIBE channels aren't keys, don't
    show up in KEYS/SCAN, and there's nothing here to flush or expire
    between tests the way rate_limit's ratelimit:* keys need to be (see
    tests/conftest.py)."""
    return f"job:{request_id}"


async def publish_progress_event(
    *,
    request_id: uuid.UUID,
    node: str,
    status: JobStatus,
    sequence: int,
    timestamp: Optional[datetime] = None,
    error_message: Optional[str] = None,
) -> None:
    """PUBLISHes one ProgressEvent to channel_name(request_id).

    Deliberately never lets a publish failure propagate: Redis pub/sub is
    fan-out to whoever happens to be subscribed *right now* and durably
    remembers nothing, so a call here when no one is listening (the common
    case before any real-time consumer exists) simply reaches zero
    subscribers and is gone - that data loss is by design, not a bug: it is
    exactly what makes these events ephemeral, with Postgres remaining the
    durable record no subscriber timing can affect. The same posture
    extends to the events Redis store itself being unreachable: orchestration/
    run_recorder.py's matching agent_runs row (the durable record) is
    already committed to Postgres by the time this is called, so letting a
    broken pub/sub side-channel fail - and thereby abort - a multi-minute,
    already-paid-for graph run would be a strictly worse outcome than losing
    one ephemeral notification. Contrast rate_limit/token_bucket.py, which
    deliberately does NOT swallow a Redis failure: that Redis outage would
    silently defeat the very thing the limiter exists to enforce, whereas
    this one only ever degrades a real-time progress UI back to
    poll_until_terminal()'s existing eventual-consistency behavior.
    """
    event = ProgressEvent(
        request_id=request_id,
        node=node,
        status=status,
        timestamp=timestamp or datetime.now(timezone.utc),
        sequence=sequence,
        error_message=error_message,
    )
    try:
        client = get_events_redis_client()
        await client.publish(channel_name(request_id), event.model_dump_json())
    except Exception:
        logger.warning(
            "failed to publish progress event (request_id=%s node=%s status=%s "
            "sequence=%s) - the durable agent_runs row was already written; "
            "only this ephemeral pub/sub notification was lost",
            request_id, node, status.value, sequence, exc_info=True,
        )


async def publish_log_event(*, request_id: uuid.UUID, node: str, message: str) -> None:
    """PUBLISHes one LogEvent to channel_name(request_id). Never raises, for
    the same reason publish_progress_event() doesn't: this is an ephemeral
    nicety on top of state already stored durably, and losing one line must
    never abort a run."""
    event = LogEvent(
        request_id=request_id,
        node=node,
        message=message,
        timestamp=datetime.now(timezone.utc),
    )
    try:
        client = get_events_redis_client()
        await client.publish(channel_name(request_id), event.model_dump_json())
    except Exception:
        logger.warning(
            "failed to publish log event (request_id=%s node=%s) - only this "
            "ephemeral line was lost",
            request_id, node, exc_info=True,
        )


async def publish_log_event_for_current_request(node: str, message: str) -> bool:
    """Publishes a log line for whichever request the calling code is
    running on behalf of (see request_context()). Returns False, doing
    nothing, when there is none - e.g. the command-line entrypoint or a
    unit test calling an agent directly."""
    request_id = _current_request_id.get()
    if request_id is None:
        return False
    await publish_log_event(request_id=request_id, node=node, message=message)
    return True
