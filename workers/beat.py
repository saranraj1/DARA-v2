"""
DARA — Celery Beat Schedule
============================
Periodic background tasks for all three phases.

Phase 1 & 2 (operational maintenance):
  - Nightly repo re-index        (2:00 AM)   — keeps Qdrant current
  - Stale pipeline cleanup       (every 6h)  — errors stuck in analyzing/fixing → failed
  - Stats cache warm             (every 1h)  — pre-compute pipeline_stats into Redis
  - Pattern library optimization (weekly)    — deactivate low-success-rate patterns

Phase 3 (reflexive memory):
  - Strategy monitor scan        (every 1h)  — detect failing error classes
  - Strategy evaluation          (daily 3AM) — A/B test pending variants, promote winners
  - Anomaly detection            (every 2m)  — Z-score scan on TimescaleDB metrics
  - Fine-tuning export           (weekly Sun 1AM) — curated triples → MinIO

To run the beat scheduler alongside a worker:
  celery -A workers.main worker --beat --loglevel=info

Or run separately (preferred for production):
  celery -A workers.main beat --loglevel=info
  celery -A workers.main worker --loglevel=info
"""
from __future__ import annotations

import logging

from celery.schedules import crontab

from workers.main import celery_app

logger = logging.getLogger(__name__)


# ── Beat schedule ─────────────────────────────────────────────

celery_app.conf.beat_schedule = {

    # ── Phase 1/2: Operational maintenance ────────────────────

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

    # ── Phase 3: Reflexive memory ──────────────────────────────

    # Strategy monitor: scan all error classes for failure rate > 40%
    "strategy-monitor-scan": {
        "task": "workers.tasks.strategy_monitor_scan",
        "schedule": crontab(minute=0),          # every hour
        "options": {"queue": "maintenance"},
    },
    # Strategy evaluator: A/B test all pending variants, promote winners
    "run-strategy-evaluations": {
        "task": "workers.tasks.run_strategy_evaluations",
        "schedule": crontab(hour=3, minute=0),  # daily 3:00 AM UTC
        "options": {"queue": "maintenance"},
    },
    # Anomaly detection: Z-score scan on TimescaleDB metrics
    "run-anomaly-detection": {
        "task": "workers.tasks.run_anomaly_detection",
        "schedule": 120.0,                      # every 2 minutes (seconds float)
        "options": {"queue": "maintenance"},
    },
    # Fine-tuning data export: curated triples → MinIO (Sunday 1AM)
    "export-fine-tuning-data": {
        "task": "workers.tasks.export_fine_tuning_data",
        "schedule": crontab(hour=1, minute=0, day_of_week="sunday"),
        "options": {"queue": "low_priority"},
    },
}

celery_app.conf.timezone = "UTC"
celery_app.conf.task_queues = {
    "default": {},       # analyze_error goes here
    "low_priority": {},  # index_repository, export
    "maintenance": {},   # beat tasks, strategy ops
}
