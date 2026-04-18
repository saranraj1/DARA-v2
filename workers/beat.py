"""
DARA — Celery Beat Schedule
============================
Periodic background tasks for operational maintenance:

  - Nightly repo re-index        (2:00 AM) — keeps Qdrant current
  - Stale pipeline cleanup        (every 6h) — errors stuck in analyzing/fixing → failed
  - Stats cache warm              (every 1h) — pre-compute pipeline_stats into Redis
  - Pattern library optimization (weekly)   — deactivate low-success-rate patterns

To run the beat scheduler alongside a worker:
  celery -A workers.main worker --beat --loglevel=info

Or run separately (preferred for production):
  celery -A workers.main beat --loglevel=info
  celery -A workers.main worker --loglevel=info
"""
from __future__ import annotations

import asyncio
import logging
from celery.schedules import crontab
from workers.main import celery_app

logger = logging.getLogger(__name__)


# ── Beat schedule ─────────────────────────────────────────────

celery_app.conf.beat_schedule = {
    # Re-index current repo every night at 2AM
    "nightly-repo-reindex": {
        "task": "workers.tasks.index_repository",
        "schedule": crontab(hour=2, minute=0),
        "args": [".", "dara-self", [".py"]],
        "options": {"queue": "low_priority"},
    },
    # Clean up stale pipelines every 6 hours
    "cleanup-stale-pipelines": {
        "task": "workers.tasks.cleanup_stale_pipelines",
        "schedule": crontab(minute=0, hour="*/6"),
        "options": {"queue": "maintenance"},
    },
    # Warm stats cache every hour
    "warm-stats-cache": {
        "task": "workers.tasks.warm_stats_cache",
        "schedule": crontab(minute=0),
        "options": {"queue": "maintenance"},
    },
    # Deactivate low-success patterns weekly (Sunday 3AM)
    "optimize-pattern-library": {
        "task": "workers.tasks.optimize_pattern_library",
        "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),
        "options": {"queue": "maintenance"},
    },
}

celery_app.conf.timezone = "UTC"
celery_app.conf.task_queues = {
    "default": {},      # analyze_error goes here
    "low_priority": {}, # index_repository
    "maintenance": {},  # beat tasks
}
