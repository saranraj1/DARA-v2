"""
DARA — Redis Async Client
Wraps redis.asyncio with typed helper methods for:
  - Task queue (LPUSH/BRPOP)
  - LLM response cache (GET/SETEX)
  - Pipeline state (HSET/HGETALL)
  - Pub/Sub for real-time dashboard
"""
from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from config.settings import get_settings

logger = logging.getLogger(__name__)


class RedisClient:
    def __init__(self, redis_url: str) -> None:
        self._client: Redis = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30,
        )

    @property
    def client(self) -> Redis:
        return self._client

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except Exception:
            return False

    async def close(self) -> None:
        await self._client.aclose()

    # ─── Task Queue ──────────────────────────────────────────

    async def push_task(self, queue: str, payload: dict) -> int:
        """Push a task to a named queue (left push = FIFO with BRPOP)."""
        return await self._client.lpush(queue, json.dumps(payload))

    async def pop_task(self, queue: str, timeout: int = 5) -> dict | None:
        """Blocking right pop — waits up to `timeout` seconds."""
        result = await self._client.brpop(queue, timeout=timeout)
        if result:
            _, value = result
            return json.loads(value)
        return None

    async def get_queue_length(self, queue: str) -> int:
        return await self._client.llen(queue)

    # ─── Cache ───────────────────────────────────────────────

    async def get_cached(self, key: str) -> str | None:
        return await self._client.get(key)

    async def set_cached(self, key: str, value: str, ttl: int = 3600) -> None:
        await self._client.setex(key, ttl, value)

    async def delete_cached(self, key: str) -> None:
        await self._client.delete(key)

    async def get_json(self, key: str) -> dict | None:
        raw = await self._client.get(key)
        return json.loads(raw) if raw else None

    async def set_json(self, key: str, value: dict, ttl: int = 3600) -> None:
        await self._client.setex(key, ttl, json.dumps(value))

    # ─── Pipeline State Hash ─────────────────────────────────

    async def set_pipeline_state(
        self, run_id: str, field: str, value: Any
    ) -> None:
        """Store pipeline stage state in a Redis hash."""
        await self._client.hset(
            f"pipeline:{run_id}",
            field,
            json.dumps(value) if not isinstance(value, str) else value,
        )
        # Auto-expire pipeline state after 24 hours
        await self._client.expire(f"pipeline:{run_id}", 86400)

    async def get_pipeline_state(self, run_id: str) -> dict:
        """Retrieve all pipeline state fields for a run."""
        raw = await self._client.hgetall(f"pipeline:{run_id}")
        result = {}
        for k, v in raw.items():
            try:
                result[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                result[k] = v
        return result

    # ─── Rate Limit Counter ──────────────────────────────────

    async def increment_rate_counter(
        self, key: str, window_seconds: int = 60
    ) -> int:
        """
        Atomic increment + expire for sliding window rate limiting.
        Returns the current count in the window.
        """
        pipe = self._client.pipeline()
        await pipe.incr(key)
        await pipe.expire(key, window_seconds)
        results = await pipe.execute()
        return int(results[0])

    async def get_rate_count(self, key: str) -> int:
        val = await self._client.get(key)
        return int(val) if val else 0

    # ─── Pub/Sub ─────────────────────────────────────────────

    async def publish(self, channel: str, message: dict) -> None:
        await self._client.publish(channel, json.dumps(message))

    async def subscribe(self, channel: str):  # type: ignore[return]
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        return pubsub


_redis_instance: RedisClient | None = None


def get_redis() -> RedisClient:
    global _redis_instance
    if _redis_instance is None:
        _redis_instance = RedisClient(get_settings().redis_url)
    return _redis_instance
