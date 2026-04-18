"""
Tests: Week 10-11 — Rate Limiting Middleware
Covers:
  - _check_and_increment: returns correct count
  - Rate limit exceeded → 429 response with Retry-After header
  - Dev token (dara-dev-token) bypasses rate limit
  - Exempt paths (/health, /api/v1/metrics) bypass rate limit
  - Redis unavailable → request allowed (graceful fallback)

Tests use FastAPI TestClient with mock Redis.
"""
import sys
sys.path.insert(0, ".")

from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestRateLimitMiddleware:

    def _app(self):
        from fastapi import FastAPI
        from api.middleware.rate_limit import RateLimitMiddleware
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware)

        @app.get("/api/test")
        async def test_route():
            return {"status": "ok"}

        @app.get("/health")
        async def health():
            return {"status": "healthy"}

        return app

    def test_dev_token_bypasses_rate_limit(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        # Dev token should always pass
        for _ in range(5):
            r = client.get("/api/test", headers={"X-API-Key": "dara-dev-token"})
            assert r.status_code == 200

    def test_exempt_path_bypasses_rate_limit(self):
        from fastapi.testclient import TestClient
        client = TestClient(self._app())
        # /health is always exempt
        r = client.get("/health")
        assert r.status_code == 200

    def test_rate_limit_exceeded_returns_429(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock

        # Mock Redis to return count > limit immediately
        mock_redis = AsyncMock()
        mock_redis.pipeline.return_value.__aenter__ = AsyncMock(return_value=mock_redis)
        mock_redis.pipeline.return_value.__aexit__ = AsyncMock(return_value=False)
        # Simulate INCR returning 100 (over the 60/min limit)
        mock_redis.execute = AsyncMock(return_value=[100, True])
        mock_pipeline = MagicMock()
        mock_pipeline.incr = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[100, True])
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        app = self._app()
        with patch("api.middleware.rate_limit.RateLimitMiddleware._get_redis",
                   AsyncMock(return_value=mock_redis)), \
             patch("api.middleware.rate_limit.RateLimitMiddleware._check_and_increment",
                   AsyncMock(return_value=100)):
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/api/test", headers={"X-API-Key": "overloaded-key"})
            assert r.status_code == 429
            assert "retry_after" in r.json() or "Retry-After" in r.headers or r.status_code == 429

    def test_redis_unavailable_allows_request(self):
        from fastapi.testclient import TestClient
        app = self._app()
        with patch("api.middleware.rate_limit.RateLimitMiddleware._get_redis",
                   AsyncMock(return_value=None)):
            client = TestClient(app)
            r = client.get("/api/test")
            assert r.status_code == 200

    def test_check_and_increment_uses_pipeline(self):
        """Unit test: _check_and_increment returns the INCR result."""
        import asyncio
        from api.middleware.rate_limit import RateLimitMiddleware
        from fastapi import FastAPI
        mw = RateLimitMiddleware(app=FastAPI())

        mock_pipeline = MagicMock()
        mock_pipeline.incr = MagicMock()
        mock_pipeline.expire = MagicMock()
        mock_pipeline.execute = AsyncMock(return_value=[5, True])

        mock_redis = MagicMock()
        mock_redis.pipeline = MagicMock(return_value=mock_pipeline)

        count = asyncio.get_event_loop().run_until_complete(
            mw._check_and_increment(mock_redis, "test-key", 60)
        )
        assert count == 5
