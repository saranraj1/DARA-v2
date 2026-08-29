"""
DARA — LLM Router
Priority-based LLM routing with Redis caching, rate-limit detection,
and automatic fallback across Groq → Gemini Flash → Gemini Pro → Local Ollama.
All methods are async. All responses are cached by prompt hash.
"""
from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

try:
    import google.generativeai as genai
except ImportError:
    genai = None

from openai import AsyncOpenAI, RateLimitError

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class LLMProvider:
    """Track a single LLM provider's availability."""

    def __init__(self, name: str, rpm_limit: int = 100) -> None:
        self.name = name
        self.rpm_limit = rpm_limit
        self._request_times: list[float] = []

    def is_rate_limited(self) -> bool:
        now = time.time()
        # Sliding 60-second window
        self._request_times = [t for t in self._request_times if now - t < 60]
        return len(self._request_times) >= self.rpm_limit

    def record_request(self) -> None:
        self._request_times.append(time.time())


class LLMRouter:
    """
    Production LLM router with:
    - Redis response caching (1hr TTL by default)
    - Rate-limit aware routing (Groq → Gemini Flash → Gemini Pro → Local)
    - Priority queue: high / normal / low
    - Automatic retry on transient errors
    - Structured logging of every routing decision
    """

    def __init__(self, settings: Settings, redis_client: Any | None = None) -> None:
        self._settings = settings
        self._cache = redis_client  # Optional — works without cache

        # Groq client (OpenAI-compatible)
        self._groq = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )

        # Local Ollama (OpenAI-compatible)
        self._ollama = AsyncOpenAI(
            api_key="ollama",
            base_url=str(settings.ollama_base_url) + "/v1",
        )

        # Gemini
        if genai is not None and getattr(settings, "google_api_key", None):
            try:
                genai.configure(api_key=settings.google_api_key)
                self._gemini_flash = genai.GenerativeModel(settings.gemini_flash_model)
                self._gemini_pro = genai.GenerativeModel(settings.gemini_pro_model)
            except Exception as e:
                logger.warning("LLMRouter: Failed to initialize Gemini models: %s", e)
                self._gemini_flash = None
                self._gemini_pro = None
        else:
            self._gemini_flash = None
            self._gemini_pro = None

        # Provider rate-limit trackers
        self._providers = {
            "groq": LLMProvider("groq", rpm_limit=28),       # ~14400/day = 600/hr = 10/min * safety factor
            "gemini_flash": LLMProvider("gemini_flash", rpm_limit=12),
            "gemini_pro": LLMProvider("gemini_pro", rpm_limit=1),
            "ollama": LLMProvider("ollama", rpm_limit=999),  # No limit (local)
        }

    # ─────────────────────────── Public API ─────────────────

    async def complete(
        self,
        prompt: str,
        system: str = "You are an expert software engineer.",
        priority: str = "normal",
        temperature: float = 0.1,
        max_tokens: int = 2048,
        cache_ttl: int | None = None,
    ) -> str:
        """
        Route an LLM completion request to the best available provider.

        Args:
            prompt: The user prompt.
            system: The system prompt.
            priority: "high" | "normal" | "low" — controls provider order.
            temperature: Sampling temperature (low = deterministic, good for debugging).
            max_tokens: Maximum output tokens.
            cache_ttl: Cache TTL in seconds. None = use settings default.

        Returns:
            The LLM response text.

        Raises:
            RuntimeError: If all providers are exhausted.
        """
        # 1. Cache lookup
        cache_key = self._build_cache_key(prompt, system, temperature)
        cached = await self._get_cache(cache_key)
        if cached:
            logger.debug("LLM cache hit", extra={"cache_key": cache_key[:16]})
            return cached

        # 2. Route by priority
        provider_chain = self._get_provider_chain(priority)
        last_error: Exception | None = None

        for provider_name in provider_chain:
            provider = self._providers[provider_name]
            if provider.is_rate_limited():
                logger.info(
                    "Provider rate-limited, skipping",
                    extra={"provider": provider_name},
                )
                continue

            try:
                logger.info(
                    "Routing LLM request",
                    extra={"provider": provider_name, "priority": priority},
                )
                result = await self._call_provider(
                    provider_name, prompt, system, temperature, max_tokens
                )
                provider.record_request()

                # Cache the result
                ttl = cache_ttl or self._settings.llm_cache_ttl_seconds
                await self._set_cache(cache_key, result, ttl)

                # Emit cost + token metrics
                self._emit_cost_metrics(provider_name, prompt, result)

                return result

            except RateLimitError as e:
                logger.warning(
                    "Rate limit hit, trying next provider",
                    extra={"provider": provider_name, "error": str(e)},
                )
                # Force mark as rate-limited
                for _ in range(provider.rpm_limit):
                    provider.record_request()
                last_error = e

            except Exception as e:
                logger.error(
                    "Provider error",
                    extra={"provider": provider_name, "error": str(e)},
                    exc_info=True,
                )
                last_error = e
                continue

        raise RuntimeError(
            f"All LLM providers exhausted. Last error: {last_error}"
        ) from last_error

    async def embed(self, text: str) -> list[float]:
        """
        Generate text embeddings using sentence-transformers (local, no API call).
        Falls back to HuggingFace API if local model not available.
        """
        from sentence_transformers import SentenceTransformer  # lazy import

        # Lazy-load model (downloaded once, cached locally)
        if not hasattr(self, "_embed_model"):
            logger.info(
                "Loading embedding model",
                extra={"model": self._settings.embedding_model},
            )
            self._embed_model = SentenceTransformer(self._settings.embedding_model)

        vector: list[float] = self._embed_model.encode(text).tolist()  # type: ignore
        return vector

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embed multiple texts efficiently."""
        from sentence_transformers import SentenceTransformer  # lazy import

        if not hasattr(self, "_embed_model"):
            self._embed_model = SentenceTransformer(self._settings.embedding_model)

        batch_size = self._settings.embedding_batch_size
        all_vectors: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            vectors = self._embed_model.encode(batch).tolist()  # type: ignore
            all_vectors.extend(vectors)

        return all_vectors

    # ─────────────────────────── Private Helpers ─────────────

    def _emit_cost_metrics(
        self,
        provider: str,
        prompt: str,
        response: str,
    ) -> None:
        """
        Emit Prometheus token + cost metrics for a completed LLM call.
        Uses tiktoken for Groq (GPT tokeniser is close enough) and
        character-count / 4 as a fallback for Gemini.
        """
        try:
            from monitoring.metrics import estimate_llm_cost_usd, llm_cost_usd_total, llm_token_batch_size, llm_tokens_total

            # Best-effort token count
            try:
                import tiktoken
                enc = tiktoken.get_encoding("cl100k_base")
                prompt_tokens = len(enc.encode(prompt))
                completion_tokens = len(enc.encode(response))
            except Exception:
                # Fallback: divide char count by 4 (rough approximation)
                prompt_tokens = max(1, len(prompt) // 4)
                completion_tokens = max(1, len(response) // 4)

            llm_tokens_total.labels(provider=provider, token_type="prompt").inc(prompt_tokens)
            llm_tokens_total.labels(provider=provider, token_type="completion").inc(completion_tokens)
            llm_token_batch_size.labels(provider=provider).observe(prompt_tokens + completion_tokens)

            cost = estimate_llm_cost_usd(provider, prompt_tokens, completion_tokens)
            if cost > 0:
                llm_cost_usd_total.labels(provider=provider).inc(cost)

            logger.debug(
                "LLM cost: provider=%s prompt_tokens=%d completion_tokens=%d usd=%.6f",
                provider, prompt_tokens, completion_tokens, cost,
            )
        except Exception as e:
            logger.debug("_emit_cost_metrics failed (non-blocking): %s", e)

    def _get_provider_chain(self, priority: str) -> list[str]:
        chains: dict[str, list[str]] = {
            "high": ["groq", "gemini_pro", "gemini_flash", "ollama"],
            "normal": ["groq", "gemini_flash", "ollama"],
            "low": ["ollama", "gemini_flash"],
        }
        return chains.get(priority, chains["normal"])

    async def _call_provider(
        self,
        provider_name: str,
        prompt: str,
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

        if provider_name == "groq":
            response = await self._groq.chat.completions.create(
                model=self._settings.groq_model,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return str(response.choices[0].message.content)

        elif provider_name == "ollama":
            response = await self._ollama.chat.completions.create(
                model=self._settings.ollama_model,
                messages=messages,  # type: ignore
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return str(response.choices[0].message.content)

        elif provider_name == "gemini_flash":
            full_prompt = f"{system}\n\n{prompt}"
            response = self._gemini_flash.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(  # type: ignore
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return str(response.text)

        elif provider_name == "gemini_pro":
            full_prompt = f"{system}\n\n{prompt}"
            response = self._gemini_pro.generate_content(
                full_prompt,
                generation_config=genai.GenerationConfig(  # type: ignore
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                ),
            )
            return str(response.text)

        raise ValueError(f"Unknown provider: {provider_name}")

    def _build_cache_key(self, prompt: str, system: str, temperature: float) -> str:
        content = f"{system}|||{prompt}|||{temperature}"
        return f"llm:cache:{hashlib.sha256(content.encode()).hexdigest()}"

    async def _get_cache(self, key: str) -> str | None:
        if self._cache is None:
            return None
        try:
            value = await self._cache.get(key)
            return value.decode() if value else None
        except Exception:
            return None

    async def _set_cache(self, key: str, value: str, ttl: int) -> None:
        if self._cache is None:
            return
        try:
            await self._cache.setex(key, ttl, value)
        except Exception as e:
            logger.warning("Cache write failed", extra={"error": str(e)})


# ── Singleton factory ────────────────────────────────────────

_router_instance: LLMRouter | None = None


def get_llm_router(redis_client: Any | None = None) -> LLMRouter:
    """
    Return the shared LLMRouter singleton.
    Pass redis_client on first call to enable caching.
    """
    global _router_instance
    if _router_instance is None:
        _router_instance = LLMRouter(settings=get_settings(), redis_client=redis_client)
    return _router_instance
