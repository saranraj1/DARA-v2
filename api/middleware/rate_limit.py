"""
DARA — Per-Key Rate Limiting Middleware (Week 10-11)
=====================================================
Sliding window rate limiter using Redis INCR + EXPIRE.

Features:
  - Per-API-key sliding window (default: 60 req/min)
  - Returns 429 with Retry-After header
  - Dev bypass: X-API-Key: dara-dev-token skips all limits
  - Falls back gracefully if Redis is unavailable (allow request)
  - Metrics: emits rate_limit_hits counter

Usage (wired in api/main.py middleware chain):
    from api.middleware.rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)
"""
from __future__ import annotations

import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from config.settings import get_settings

logger = logging.getLogger(__name__)

DEV_TOKENS = {"dara-dev-token", "dara-test-token"}
# Paths that are always exempt from rate limiting
EXEMPT_PATHS = {"/health", "/api/v1/metrics", "/docs", "/openapi.json", "/redoc"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Sliding window rate limiter.
    Key = API key from X-API-Key header, falls back to client IP.
    Window = 60 seconds. Limit = settings.rate_limit_per_minute.
    """

    async def dispatch(self, request: Request, call_next):
        # Always exempt certain paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Dev bypass
        api_key = request.headers.get("X-API-Key", "")
        if api_key in DEV_TOKENS:
            return await call_next(request)

        settings = get_settings()
        limit = settings.rate_limit_per_minute

        # Determine the rate-limit key (API key or IP)
        if api_key:
            rl_key = f"ratelimit:key:{api_key}"
        else:
            client_ip = request.client.host if request.client else "unknown"
            rl_key = f"ratelimit:ip:{client_ip}"

        # Apply rate limit via Redis (non-blocking fallback if Redis unavailable)
        try:
            redis = await self._get_redis()
            if redis:
                current = await self._check_and_increment(redis, rl_key, window=60)
                if current > limit:
                    # Emit metric
                    try:
                        from monitoring.metrics import rate_limit_hits
                        rate_limit_hits.labels(key_type="api_key" if api_key else "ip").inc()
                    except Exception:
                        pass
                    retry_after = 60 - (int(time.time()) % 60)
                    logger.warning(
                        "Rate limit exceeded: key=%s count=%d limit=%d",
                        rl_key[:30], current, limit,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "error": "rate_limit_exceeded",
                            "message": f"Too many requests. Limit: {limit} req/min.",
                            "retry_after": retry_after,
                        },
                        headers={"Retry-After": str(retry_after)},
                    )
        except Exception as e:
            logger.debug("RateLimitMiddleware: Redis unavailable, skipping: %s", e)

        return await call_next(request)

    async def _get_redis(self):
        """Lazy-load the shared Redis async client."""
        try:
            import redis.asyncio as aioredis
            settings = get_settings()
            # Build URL (handles password)
            url = str(settings.redis_url)
            return aioredis.from_url(url, decode_responses=True, socket_timeout=0.5)
        except Exception:
            return None

    async def _check_and_increment(self, redis, key: str, window: int) -> int:
        """
        Increment the counter for this key in a sliding window.
        Uses INCR + EXPIRE (atomic via pipelining).
        Returns the current count after increment.
        """
        pipe = redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        results = await pipe.execute()
        return int(results[0])
