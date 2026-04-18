"""
Unit tests: monitoring/metrics.py + api/routers/admin.py + api/routers/metrics.py (Week 8)
"""
import sys
sys.path.insert(0, ".")

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestPrometheusMetrics:
    """Tests for monitoring/metrics.py registry."""

    def test_metrics_module_imports(self):
        from monitoring import metrics
        assert hasattr(metrics, "errors_ingested")
        assert hasattr(metrics, "pipelines_total")
        assert hasattr(metrics, "fixes_generated")
        assert hasattr(metrics, "fixes_reviewed")
        assert hasattr(metrics, "hitl_decisions")
        assert hasattr(metrics, "duplicates_detected")
        assert hasattr(metrics, "llm_requests")
        assert hasattr(metrics, "patch_apply_total")

    def test_histograms_exist(self):
        from monitoring import metrics
        assert hasattr(metrics, "pipeline_duration")
        assert hasattr(metrics, "llm_latency")
        assert hasattr(metrics, "patch_apply_duration")

    def test_gauges_exist(self):
        from monitoring import metrics
        assert hasattr(metrics, "active_pipelines")
        assert hasattr(metrics, "pattern_library_size")

    def test_counter_increment(self):
        """Counter can be incremented without errors."""
        from monitoring import metrics
        before = metrics.errors_ingested.labels(service="test", severity="high")._value.get()
        metrics.errors_ingested.labels(service="test", severity="high").inc()
        after = metrics.errors_ingested.labels(service="test", severity="high")._value.get()
        assert after == before + 1

    def test_histogram_observe(self):
        """Histogram.observe doesn't raise."""
        from monitoring import metrics
        metrics.pipeline_duration.observe(2.5)  # 2.5 seconds
        metrics.llm_latency.labels(provider="groq").observe(0.45)
        metrics.patch_apply_duration.observe(1.2)

    def test_gauge_inc_dec(self):
        """Gauge inc/dec tracks correctly."""
        from monitoring import metrics
        before = metrics.active_pipelines._value.get()
        metrics.active_pipelines.inc()
        assert metrics.active_pipelines._value.get() == before + 1
        metrics.active_pipelines.dec()
        assert metrics.active_pipelines._value.get() == before

    def test_prometheus_text_format(self):
        """generate_latest() produces valid Prometheus text."""
        from prometheus_client import generate_latest
        output = generate_latest().decode("utf-8")
        assert "dara_errors_ingested_total" in output
        assert "dara_active_pipelines" in output
        assert "dara_pipeline_duration_seconds" in output


class TestMetricsRouter:
    """Tests for api/routers/metrics.py."""

    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus_text(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from api.routers.metrics import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        client = TestClient(app)
        r = client.get("/api/v1/metrics")
        assert r.status_code == 200
        assert "text/plain" in r.headers["content-type"]
        assert "dara_" in r.text or "python_gc" in r.text  # prometheus output

    @pytest.mark.asyncio
    async def test_metrics_summary_returns_json(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from api.routers.metrics import router
        with patch("api.routers.metrics.get_postgres") as mock_get:
            mock_pg = MagicMock()
            mock_pg.get_pipeline_stats = AsyncMock(return_value={
                "total_errors": 50, "total_fixes": 40,
                "accepted_fixes": 30, "avg_confidence": 0.85
            })
            mock_get.return_value = mock_pg
            app = FastAPI()
            app.include_router(router, prefix="/api/v1")
            client = TestClient(app)
            r = client.get("/api/v1/metrics/summary")
            assert r.status_code == 200
            data = r.json()
            assert data["total_errors"] == 50
            assert "acceptance_rate" in data
            assert data["acceptance_rate"] == 0.75


class TestAdminAPI:
    """Tests for api/routers/admin.py."""

    def _make_app(self):
        from fastapi import FastAPI
        from api.routers.admin import router
        app = FastAPI()
        app.include_router(router)
        return app

    def test_stats_requires_admin_token(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())
        r = client.get("/api/v1/admin/stats")
        assert r.status_code == 401

    def test_stats_with_valid_token(self):
        from fastapi.testclient import TestClient
        with patch("api.routers.admin.get_postgres") as mock_pg_get, \
             patch("api.routers.admin.get_redis") as mock_redis_get, \
             patch("api.routers.admin.get_settings") as mock_settings:
            mock_settings.return_value.admin_api_key = "test-admin-key"

            mock_pg = MagicMock()
            mock_pg.session = MagicMock()
            # Mock context manager
            mock_sess = AsyncMock()
            mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
            mock_sess.__aexit__ = AsyncMock(return_value=False)
            mock_sess.scalar = AsyncMock(return_value=5)
            mock_pg.session.return_value = mock_sess
            mock_pg_get.return_value = mock_pg

            mock_redis = MagicMock()
            mock_redis.client = MagicMock()
            mock_redis.client.get = AsyncMock(return_value=b'{"total_errors": 10, "total_fixes": 8, "accepted_fixes": 6, "avg_confidence": 0.8}')
            mock_redis_get.return_value = mock_redis

            client = TestClient(self._make_app())
            r = client.get("/api/v1/admin/stats", headers={"X-Admin-Token": "test-admin-key"})
            assert r.status_code == 200
            data = r.json()
            assert "total_errors" in data

    def test_admin_key_in_settings(self):
        from config.settings import Settings
        s = Settings.model_construct()
        assert hasattr(s, "admin_api_key")

    def test_audit_endpoint_registered(self):
        from api.routers import admin
        assert hasattr(admin, "admin_audit")

    def test_pipeline_endpoint_registered(self):
        from api.routers import admin
        assert hasattr(admin, "admin_pipeline")

    def test_reindex_requires_admin(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._make_app())
        r = client.post("/api/v1/admin/reindex")
        assert r.status_code == 401
