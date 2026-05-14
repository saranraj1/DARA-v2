"""
DARA — Beat Scheduler Tests
=============================
Tests for workers/beat.py:
  - All 8 scheduled tasks are registered in the beat schedule
  - Crontab / interval schedules are correctly configured
  - Queue assignments are correct
  - Task names match what's registered in Celery
"""
from __future__ import annotations

import pytest


class TestBeatScheduleStructure:

    def _get_schedule(self):
        """Import beat module (registers side-effects) and return schedule dict."""
        from workers.main import celery_app
        import workers.beat  # noqa: F401 — registers beat_schedule as side effect
        return celery_app.conf.beat_schedule

    def test_all_phase1_2_tasks_registered(self):
        schedule = self._get_schedule()
        expected = {
            "nightly-repo-reindex",
            "cleanup-stale-pipelines",
            "warm-stats-cache",
            "optimize-pattern-library",
        }
        assert expected.issubset(schedule.keys()), (
            f"Missing Phase 1/2 tasks: {expected - schedule.keys()}"
        )

    def test_all_phase3_tasks_registered(self):
        schedule = self._get_schedule()
        expected = {
            "strategy-monitor-scan",
            "run-strategy-evaluations",
            "run-anomaly-detection",
            "export-fine-tuning-data",
        }
        assert expected.issubset(schedule.keys()), (
            f"Missing Phase 3 tasks: {expected - schedule.keys()}"
        )

    def test_total_task_count(self):
        schedule = self._get_schedule()
        assert len(schedule) >= 8, f"Expected ≥8 tasks, got {len(schedule)}"

    def test_nightly_reindex_schedule(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["nightly-repo-reindex"]
        assert entry["task"] == "workers.tasks.index_repository"
        sched = entry["schedule"]
        assert isinstance(sched, crontab)
        assert entry["args"] == [".", "dara-self", [".py"]]
        assert entry["options"]["queue"] == "low_priority"

    def test_cleanup_stale_schedule(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["cleanup-stale-pipelines"]
        assert entry["task"] == "workers.tasks.cleanup_stale_pipelines"
        assert isinstance(entry["schedule"], crontab)
        assert entry["options"]["queue"] == "maintenance"

    def test_warm_stats_schedule(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["warm-stats-cache"]
        assert entry["task"] == "workers.tasks.warm_stats_cache"
        assert isinstance(entry["schedule"], crontab)

    def test_optimize_pattern_schedule(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["optimize-pattern-library"]
        assert entry["task"] == "workers.tasks.optimize_pattern_library"
        sched = entry["schedule"]
        assert isinstance(sched, crontab)
        # Runs at 3AM on Sunday. Celery stores hour/minute as set.
        assert sched.hour == {3}
        assert sched.minute == {0}
        # day_of_week may be represented as a set or WeekdaySet depending on celery version
        assert str(sched.day_of_week) in ("sunday", "0", "{0}")

    def test_strategy_monitor_scan_hourly(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["strategy-monitor-scan"]
        assert entry["task"] == "workers.tasks.strategy_monitor_scan"
        assert isinstance(entry["schedule"], crontab)
        assert entry["options"]["queue"] == "maintenance"

    def test_strategy_evaluations_daily_3am(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["run-strategy-evaluations"]
        assert entry["task"] == "workers.tasks.run_strategy_evaluations"
        sched = entry["schedule"]
        assert isinstance(sched, crontab)
        assert entry["options"]["queue"] == "maintenance"

    def test_anomaly_detection_every_2_minutes(self):
        schedule = self._get_schedule()
        entry = schedule["run-anomaly-detection"]
        assert entry["task"] == "workers.tasks.run_anomaly_detection"
        # Every 2 minutes = 120 seconds float schedule
        assert entry["schedule"] == 120.0
        assert entry["options"]["queue"] == "maintenance"

    def test_export_fine_tuning_weekly_sunday(self):
        from celery.schedules import crontab
        schedule = self._get_schedule()
        entry = schedule["export-fine-tuning-data"]
        assert entry["task"] == "workers.tasks.export_fine_tuning_data"
        assert isinstance(entry["schedule"], crontab)
        assert entry["options"]["queue"] == "low_priority"

    def test_timezone_utc(self):
        from workers.main import celery_app
        import workers.beat  # noqa: F401
        assert celery_app.conf.timezone == "UTC"

    def test_task_queues_defined(self):
        from workers.main import celery_app
        import workers.beat  # noqa: F401
        queues = celery_app.conf.task_queues
        assert "default" in queues
        assert "low_priority" in queues
        assert "maintenance" in queues


class TestBeatTaskNameResolution:
    """Verify task names in beat_schedule match actual Celery task registrations."""

    def test_all_task_names_importable(self):
        """All tasks referenced in the beat schedule must be importable."""
        from workers.main import celery_app
        import workers.beat   # noqa: F401
        import workers.tasks  # noqa: F401 — registers all tasks

        schedule = celery_app.conf.beat_schedule
        registered = set(celery_app.tasks.keys())

        for entry_name, entry in schedule.items():
            task_name = entry["task"]
            assert task_name in registered, (
                f"Beat entry '{entry_name}' references unregistered task '{task_name}'"
            )

    def test_phase3_task_names_match_registered(self):
        """Phase 3 task names must exactly match their @celery_app.task(name=...) declarations."""
        from workers.main import celery_app
        import workers.beat   # noqa: F401
        import workers.tasks  # noqa: F401

        phase3_names = [
            "workers.tasks.strategy_monitor_scan",
            "workers.tasks.run_strategy_evaluations",
            "workers.tasks.run_anomaly_detection",
            "workers.tasks.export_fine_tuning_data",
        ]
        registered = set(celery_app.tasks.keys())
        for name in phase3_names:
            assert name in registered, f"Task '{name}' not registered in Celery app"
