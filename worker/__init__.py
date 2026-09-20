from worker.celery_app import celery_app
from worker.tasks import ping

__all__ = [
    "celery_app",
    "ping",
]
