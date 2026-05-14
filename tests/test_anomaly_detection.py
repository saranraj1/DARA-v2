"""
Tests: Week 18-19 — Anomaly Detector
Covers:
  - BaselineStats.is_usable: False when stddev=0 or sample_count<10
  - BaselineStats.is_usable: True with valid data
  - AnomalyAlertData.is_critical: True at z >= 5
  - AnomalyAlertData.is_critical: False at z = 3
  - _gather_safe: returns None for exceptions, values for successes
  - run_full_scan: returns empty when no active services
  - detect_latency_spike: returns None when no data
"""
import sys

sys.path.insert(0, ".")
from unittest.mock import AsyncMock

import pytest


class TestBaselineStats:

    def _baseline(self, mean, stddev, sample_count):
        from monitoring.anomaly_detector import BaselineStats
        return BaselineStats(
            service_name="svc", metric_name="avg_latency_ms",
            mean=mean, stddev=stddev, sample_count=sample_count,
        )

    def test_is_usable_false_zero_stddev(self):
        b = self._baseline(mean=100.0, stddev=0.0, sample_count=20)
        assert not b.is_usable

    def test_is_usable_false_low_sample_count(self):
        b = self._baseline(mean=100.0, stddev=10.0, sample_count=5)
        assert not b.is_usable

    def test_is_usable_true_with_valid_data(self):
        b = self._baseline(mean=100.0, stddev=15.0, sample_count=50)
        assert b.is_usable


class TestAnomalyAlertData:

    def _alert(self, z_score):
        from monitoring.anomaly_detector import AnomalyAlertData
        return AnomalyAlertData(
            service_name="api-svc",
            alert_type="latency_spike",
            metric_name="avg_latency_ms",
            current_value=500.0,
            baseline_value=100.0,
            z_score=z_score,
        )

    def test_is_critical_at_z5(self):
        a = self._alert(z_score=5.0)
        assert a.is_critical

    def test_is_not_critical_at_z3(self):
        a = self._alert(z_score=3.0)
        assert not a.is_critical

    def test_triggered_at_set_automatically(self):
        a = self._alert(z_score=3.1)
        assert a.triggered_at is not None
        assert a.triggered_at.tzinfo is not None  # timezone-aware


class TestGatherSafe:

    @pytest.mark.asyncio
    async def test_exceptions_return_none(self):

        from monitoring.anomaly_detector import _gather_safe

        async def ok():
            return "good"

        async def fail():
            raise ValueError("oops")

        results = await _gather_safe(ok(), fail())
        assert results[0] == "good"
        assert results[1] is None


class TestAnomalyDetector:

    def _detector(self):
        from monitoring.anomaly_detector import AnomalyDetector
        detector = AnomalyDetector()
        detector._get_active_services = AsyncMock(return_value=[])
        return detector

    @pytest.mark.asyncio
    async def test_run_full_scan_empty_when_no_services(self):
        detector = self._detector()
        result = await detector.run_full_scan()
        assert result == []

    @pytest.mark.asyncio
    async def test_detect_latency_spike_returns_none_when_no_data(self):
        detector = self._detector()
        detector._current_avg_latency = AsyncMock(return_value=None)
        result = await detector.detect_latency_spike("api-svc")
        assert result is None
