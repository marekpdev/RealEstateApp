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
    # Both settings below are already Celery's own defaults as of 5.6 -
    # pinned explicitly, with the reasoning written down, so a future
    # Celery upgrade can't silently change this app's shutdown behaviour
    # and so the choice reads as deliberate rather than accidental.
    #
    # A worker that loses its broker connection mid-task (a Redis restart,
    # a network blip) does NOT abort whatever it's currently running - it
    # keeps executing and only the next broker interaction (acking,
    # fetching new work) is affected. The alternative (True) exists mainly
    # for brokers/setups where a lost connection means a task's eventual
    # ack can never land anyway; that's not this app's failure mode, and
    # aborting a multi-minute graph run over a connection blip it would
    # otherwise have recovered from is strictly worse than letting it finish.
    worker_cancel_long_running_tasks_on_connection_loss=False,
    # Celery 5.5+'s "soft shutdown": on a warm shutdown (one SIGTERM), wait
    # at most this many seconds for in-flight tasks before forcing a cold
    # shutdown anyway. 0 (the default) disables it entirely, so a warm
    # shutdown waits for the in-flight task with no internal ceiling of its
    # own. That's deliberate, not an oversight: task_acks_late=True above
    # exists specifically so this app never loses a run to a worker that
    # exits mid-task, and giving Celery its own internal timeout would
    # reintroduce exactly that risk from a second, harder-to-see angle.
    # The actual ceiling on how long a warm shutdown is allowed to take
    # belongs one level up, in the orchestrator that sends the signal -
    # Kubernetes' terminationGracePeriodSeconds, or Compose's
    # stop_grace_period (see docker-compose.yml's worker service and
    # k8s/deployment.yaml) - which forcibly SIGKILLs the process if it
    # overruns. Keeping the ceiling in exactly one place, set by whatever
    # actually sends the signal, is simpler to reason about than letting
    # Celery and the orchestrator each enforce their own, possibly
    # conflicting, deadline.
    worker_soft_shutdown_timeout=0,
)

celery_app.conf.beat_schedule = {
    # Celery Beat's own scheduler process reads this dict and calls
    # apply_async() by task name on the configured interval - it never
    # imports or runs worker.tasks itself (see the "beat" service in
    # docker-compose.yml, which starts a `celery ... beat` process with no
    # Postgres/vendor credentials at all: it only needs the broker). A
    # plain number here is interpreted by Celery as a timedelta in seconds.
    "sync-knowledge-base": {
        "task": "worker.sync_knowledge_base",
        "schedule": config.KNOWLEDGE_BASE_SYNC_SCHEDULE_SECONDS,
    },
}
