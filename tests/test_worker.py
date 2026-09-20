import pytest
from celery.contrib.testing.worker import start_worker

from config import config
from worker.celery_app import celery_app
from worker.tasks import ping


def test_celery_app_configured_with_separate_broker_and_backend():
    """Broker and result backend point at the same Redis server but different
    logical databases - two separate concerns, not one."""
    assert celery_app.conf.broker_url == config.CELERY_BROKER_URL
    assert celery_app.conf.result_backend == config.CELERY_RESULT_BACKEND
    assert celery_app.conf.broker_url != celery_app.conf.result_backend


def test_celery_app_uses_json_serialization():
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.result_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_celery_app_acks_late_and_prefetch_one():
    """Long, expensive tasks must not be lost to a crashed worker (acks_late)
    and must not pile up on one worker while others sit idle (prefetch=1)."""
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.worker_prefetch_multiplier == 1


@pytest.fixture(scope="module")
def celery_worker_process():
    """Starts a real embedded Celery worker (solo pool, in this process) so
    the round-trip test below exercises the actual broker and result backend
    over a real Redis connection - never a mock and never task_always_eager,
    which would skip the broker entirely and prove nothing about delivery."""
    with start_worker(celery_app, pool="solo", perform_ping_check=False) as worker:
        yield worker


def test_ping_task_round_trips_through_real_redis(celery_worker_process):
    result = ping.delay()
    assert result.get(timeout=10) == "pong"
