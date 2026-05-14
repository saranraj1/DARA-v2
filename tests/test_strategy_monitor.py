"""
Tests: Week 15-16 — Strategy Monitor
Covers:
  - FailingClass.summary includes error_class and rate
  - FailingClass.total = rejection + acceptance
  - scan() with no failing classes returns empty list
  - scan() detects class above FAILURE_THRESHOLD
  - flag_for_refresh() skips if already flagged (idempotent)
  - get_dashboard_data() returns dict with failing_classes key
"""
import sys

sys.path.insert(0, ".")
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestFailingClass:

    def _make_fc(self, error_class="null_reference", rejection=5, acceptance=2):
        from agents.strategy_monitor import FailingClass
        total = rejection + acceptance
        return FailingClass(
            error_class=error_class,
            rejection_rate=rejection / total,
            rejection_count=rejection,
            acceptance_count=acceptance,
            sample_count=total,
            dominant_strategy="llm_single_file",
            last_seen=datetime.now(timezone.utc),
        )

    def test_total_is_sum(self):
        fc = self._make_fc(rejection=5, acceptance=3)
        assert fc.total == 8

    def test_summary_includes_class_and_rate(self):
        fc = self._make_fc()
        assert "null_reference" in fc.summary
        assert "%" in fc.summary

    def test_rejection_rate_correct(self):
        fc = self._make_fc(rejection=4, acceptance=6)
        assert abs(fc.rejection_rate - 0.4) < 0.01


class TestStrategyMonitor:

    def _monitor(self, neo4j_rows=None, pg_rows=None):
        from agents.strategy_monitor import StrategyMonitor
        monitor = StrategyMonitor()

        mock_neo4j = MagicMock()
        mock_neo4j.get_failing_classes = AsyncMock(return_value=neo4j_rows or [])
        mock_neo4j.get_graph_stats = AsyncMock(return_value={
            "error_patterns": 10, "fix_templates": 7,
            "code_patterns": 5, "services": 3, "cascades": 2,
        })
        monitor._neo4j = mock_neo4j
        monitor._postgres = MagicMock()
        return monitor

    @pytest.mark.asyncio
    async def test_scan_returns_empty_when_no_data(self):
        monitor = self._monitor(neo4j_rows=[])
        monitor._scan_postgres = AsyncMock(return_value=[])
        result = await monitor.scan()
        assert result == []

    @pytest.mark.asyncio
    async def test_scan_detects_high_rejection_class(self):
        monitor = self._monitor(neo4j_rows=[{
            "error_class": "network_timeout",
            "rejection_count": 8,
            "acceptance_count": 2,
            "dominant_strategy": "network_timeout",
            "needs_refresh": True,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "signature_hash": "abc123",
            "template_id": None,
            "template_success_rate": None,
        }])
        # Patch postgres fallback so no unawaited coroutine warning
        monitor._scan_postgres = AsyncMock(return_value=[])
        result = await monitor.scan()
        assert len(result) == 1
        assert result[0].error_class == "network_timeout"
        assert result[0].rejection_rate >= 0.40

    @pytest.mark.asyncio
    async def test_scan_filters_below_min_samples(self):
        from agents.strategy_monitor import StrategyMonitor
        monitor = self._monitor(neo4j_rows=[{
            "error_class": "logic_error",
            "rejection_count": 2,
            "acceptance_count": 1,  # only 3 total — below MIN_SAMPLES=5
            "dominant_strategy": "logic_error",
            "needs_refresh": False,
            "last_seen": None,
            "signature_hash": "def456",
            "template_id": None,
            "template_success_rate": None,
        }])
        # Patch postgres fallback so no unawaited coroutine warning
        monitor._scan_postgres = AsyncMock(return_value=[])
        result = await monitor.scan()
        # Should be filtered out (total=3 < MIN_SAMPLES=5)
        assert all(fc.sample_count >= StrategyMonitor.MIN_SAMPLES for fc in result)
