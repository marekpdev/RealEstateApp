from celery import Celery

from config import config

celery_app = Celery(
    "realestateapp",
    broker=config.CELERY_BROKER_URL,
    backend=config.CELERY_RESULT_BACKEND,
    include=["worker.tasks"],
)

celery_app.conf.update(
    # Explicit serializer rather than Celery's default pickle: pickle can
    # execute arbitrary code on deserialization, which is a real risk for a
    # broker that (eventually) carries data derived from LLM/user input.
    # json is slightly more restrictive (no arbitrary Python objects) but
    # that restriction is what makes it safe.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Tasks in this app wrap a multi-agent graph run that can take minutes
    # and calls paid external APIs - losing one silently to a crashed worker
    # is much worse than the alternative failure mode this setting accepts.
    # With acks_late, the broker only removes a task from the queue once the
    # worker reports it finished (success or failure), not the moment the
    # worker picks it up - so a worker that dies mid-task leaves the task
    # unacked and Redis redelivers it to another worker instead of losing it.
    # The cost: a task can now run twice (the original worker may have
    # actually finished the work moments before dying, before the ack made
    # it back) - at-least-once delivery, not exactly-once. Task-level
    # idempotency is what closes that gap; acks_late only prevents silent
    # loss, not duplication.
    task_acks_late=True,
    # Default Celery worker prefetches several tasks per worker process at
    # once (a multiple of concurrency) to cut down on broker round-trips.
    # That's the right call for many cheap, uniform tasks, but wrong here:
    # this app's tasks are few, long-running and expensive, so a worker that
    # grabbed several in advance would sit on them while sibling workers
    # went idle waiting for work. Prefetching exactly one means a worker
    # only reserves its *next* task once it has actually finished (and,
    # with acks_late, acknowledged) its current one, which spreads long jobs
    # evenly across the pool at the cost of one extra broker round-trip per
    # task - negligible next to the task's own runtime.
    worker_prefetch_multiplier=1,
    # Result keys are only useful for a bounded window after a task
    # finishes (a caller polling for status); without an expiry they'd
    # accumulate in Redis forever.
    result_expires=3600,
)
