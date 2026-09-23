import asyncio
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
import redis

from config import config
from db.enums import JobStatus
from events.publisher import channel_name, publish_progress_event
from events.redis_client import dispose_events_redis_client, get_events_redis_client
from events.schemas import PROGRESS_EVENT_SCHEMA_VERSION, ProgressEvent


def test_channel_name_is_job_prefixed_by_request_id():
    request_id = uuid.uuid4()
    assert channel_name(request_id) == f"job:{request_id}"


def test_progress_event_defaults_to_the_current_schema_version():
    event = ProgressEvent(
        request_id=uuid.uuid4(),
        node="market_data_agent",
        status=JobStatus.RUNNING,
        timestamp=datetime.now(timezone.utc),
        sequence=1,
    )
    assert event.schema_version == PROGRESS_EVENT_SCHEMA_VERSION


def test_get_events_redis_client_is_a_process_wide_singleton():
    """Mirrors rate_limit/redis_client.py's own singleton test - two calls
    in the same process must hand back the identical client object, not
    build a fresh connection pool every time."""
    first = get_events_redis_client()
    second = get_events_redis_client()
    assert first is second


@pytest.mark.asyncio
async def test_dispose_events_redis_client_clears_the_singleton():
    first = get_events_redis_client()
    await dispose_events_redis_client()
    second = get_events_redis_client()
    assert first is not second
    await dispose_events_redis_client()


@pytest.mark.asyncio
async def test_publish_progress_event_round_trips_through_real_redis():
    """A real subscriber, never a mock - the same posture this project
    already takes for Postgres and every other Redis-backed piece (rate
    limiting, Celery's broker/backend): a mocked client can't verify a real
    PUBLISH/SUBSCRIBE round trip actually carries the right bytes."""
    request_id = uuid.uuid4()
    client = redis.Redis.from_url(config.EVENTS_REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    try:
        pubsub.subscribe(channel_name(request_id))
        pubsub.get_message(timeout=1)  # the "subscribe" confirmation message

        await publish_progress_event(
            request_id=request_id,
            node="market_data_agent",
            status=JobStatus.RUNNING,
            sequence=1,
        )

        message = pubsub.get_message(timeout=2)
        assert message is not None
        assert message["type"] == "message"
        payload = json.loads(message["data"])
        assert payload["request_id"] == str(request_id)
        assert payload["node"] == "market_data_agent"
        assert payload["status"] == JobStatus.RUNNING.value
        assert payload["sequence"] == 1
        assert payload["schema_version"] == PROGRESS_EVENT_SCHEMA_VERSION
    finally:
        pubsub.close()
        client.close()


@pytest.mark.asyncio
async def test_publish_progress_event_swallows_a_redis_failure_without_raising():
    """The deliberate design choice publish_progress_event() makes: an
    unreachable events Redis must never fail the graph run it's reporting
    on - the durable
    agent_runs row (Postgres) is already written by the time this is
    called; only the ephemeral pub/sub notification is lost. Contrast
    rate_limit/token_bucket.py, which deliberately does the opposite for a
    different reason (see events/publisher.py's own docstring)."""
    with patch(
        "events.publisher.get_events_redis_client"
    ) as mock_get_client:
        mock_get_client.return_value.publish = AsyncMock(
            side_effect=ConnectionError("simulated events Redis outage")
        )
        await publish_progress_event(
            request_id=uuid.uuid4(),
            node="market_data_agent",
            status=JobStatus.RUNNING,
            sequence=1,
        )  # must not raise
