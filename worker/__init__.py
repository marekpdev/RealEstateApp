from worker.celery_app import celery_app
from worker.tasks import generate_report, ping

__all__ = [
    "celery_app",
    "generate_report",
    "ping",
]
