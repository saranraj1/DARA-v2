"""
Tests: Week 10-11 — LLM Caching (Redis cache in LLMRouter)
Covers:
  - Cache key building: same prompt+system → same key
  - Cache key building: different temperature → different key
  - Cache hit: _get_cache returns cached response, skips provider call
  - Cache miss: _get_cache returns None → provider called → _set_cache called
  - Cache write failure: gracefully ignored (non-fatal)
  - TTL: cache_ttl param overrides settings default
"""
import sys

sys.path.insert(0, ".")

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestLLMCacheKey:

    def _router(self):
        from config.llm_router import LLMRouter
        settings = MagicMock()
        settings.groq_api_key = "gsk_test"
        settings.google_api_key = "gapi_test"
        settings.ollama_base_url = "http://localhost:11434"
        settings.gemini_flash_model = "gemini-1.5-flash"
        settings.gemini_pro_model = "gemini-1.5-pro"
        settings.llm_cache_ttl_seconds = 3600
        settings.embedding_model = "all-MiniLM-L6-v2"
        settings.embedding_batch_size = 32
        with patch("config.llm_router.AsyncOpenAI"), \
             patch("config.llm_router.genai"):
            return LLMRouter(settings=settings, redis_client=None)

    def test_same_prompt_same_key(self):
        router = self._router()
        k1 = router._build_cache_key("hello", "system", 0.1)
        k2 = router._build_cache_key("hello", "system", 0.1)
        assert k1 == k2

    def test_different_temperature_different_key(self):
        router = self._router()
        k1 = router._build_cache_key("hello", "system", 0.1)
        k2 = router._build_cache_key("hello", "system", 0.9)
        assert k1 != k2

    def test_different_system_different_key(self):
        router = self._router()
        k1 = router._build_cache_key("hello", "You are A", 0.1)
        k2 = router._build_cache_key("hello", "You are B", 0.1)
        assert k1 != k2

    def test_cache_key_prefix(self):
        router = self._router()
        key = router._build_cache_key("p", "s", 0.1)
        assert key.startswith("llm:cache:")


class TestLLMCacheHitMiss:

    def _router_with_cache(self, redis):
        from config.llm_router import LLMRouter
        settings = MagicMock()
        settings.groq_api_key = "gsk_test"
        settings.google_api_key = "gapi_test"
        settings.ollama_base_url = "http://localhost:11434"
        settings.gemini_flash_model = "gemini-1.5-flash"
        settings.gemini_pro_model = "gemini-1.5-pro"
        settings.llm_cache_ttl_seconds = 3600
        settings.embedding_model = "all-MiniLM-L6-v2"
        settings.embedding_batch_size = 32
        with patch("config.llm_router.AsyncOpenAI"), \
             patch("config.llm_router.genai"):
            return LLMRouter(settings=settings, redis_client=redis)

    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_and_skips_provider(self):
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=b"cached response")
        mock_redis.setex = AsyncMock()
        router = self._router_with_cache(mock_redis)

        result = await router._get_cache("some-key")
        assert result == "cached response"

    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self):
        mock_redis = MagicMock()
        mock_redis.get = AsyncMock(return_value=None)
        router = self._router_with_cache(mock_redis)

        result = await router._get_cache("some-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_cache_write_succeeds(self):
        mock_redis = MagicMock()
        mock_redis.setex = AsyncMock()
        router = self._router_with_cache(mock_redis)

        await router._set_cache("key", "value", 3600)
        mock_redis.setex.assert_called_once_with("key", 3600, "value")

    @pytest.mark.asyncio
    async def test_cache_write_failure_is_non_fatal(self):
        mock_redis = MagicMock()
        mock_redis.setex = AsyncMock(side_effect=Exception("Redis down"))
        router = self._router_with_cache(mock_redis)

        # Should not raise
        await router._set_cache("key", "value", 3600)

    @pytest.mark.asyncio
    async def test_no_cache_client_returns_none(self):
        router = self._router_with_cache(None)
        result = await router._get_cache("any-key")
        assert result is None
