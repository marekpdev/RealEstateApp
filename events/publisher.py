import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.enums import JobStatus
from events.redis_client import get_events_redis_client
from events.schemas import ProgressEvent

logger = logging.getLogger(__name__)


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
