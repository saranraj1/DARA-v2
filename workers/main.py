"""DARA - Celery worker app with Redis broker"""
from __future__ import annotations
import logging
from celery import Celery
from config.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

celery_app = Celery(
    "dara",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,            # only ack after successful execution
    task_reject_on_worker_lost=True,# re-queue if worker dies mid-task
    worker_prefetch_multiplier=1,   # one task at a time per worker (fair dispatch)
    task_routes={
        "workers.tasks.analyze_error": {"queue": "dara:analysis"},
        "workers.tasks.index_repository": {"queue": "dara:indexing"},
    },
    beat_schedule={},
)

if __name__ == "__main__":
    celery_app.start()
