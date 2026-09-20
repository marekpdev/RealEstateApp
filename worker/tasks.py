from worker.celery_app import celery_app


@celery_app.task(name="worker.ping")
def ping() -> str:
    """Trivial round-trip task: proves a task can be enqueued through the
    broker, picked up by a worker process, executed, and have its result
    retrieved from the result backend - the full path any real task will use."""
    return "pong"
