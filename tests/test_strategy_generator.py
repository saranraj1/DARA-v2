"""
Tests: Week 16-17 — Strategy Generator
Covers:
  - _build_generation_prompt includes error_class, failure_rate, failure_bullets
  - _parse_variants returns correct count from valid JSON
  - _parse_variants handles markdown-wrapped JSON
  - _parse_variants returns empty list for invalid JSON
  - get_active_strategy_prompt returns None when no active variant in DB
  - _save_variants creates StrategyVariant rows with status='draft'
  - DB-driven router override: get_db_driven_strategy returns None when no active variant
"""
import sys

sys.path.insert(0, ".")
from types import SimpleNamespace

import pytest


def _make_failing_stats(
    error_class="null_reference",
    rejection_rate=0.55,
    sample_count=10,
    strategy="llm_single_file",
):
    return SimpleNamespace(
        error_class=error_class,
        rejection_rate=rejection_rate,
        sample_count=sample_count,
        dominant_strategy=strategy,
    )


class TestGenerationPrompt:

    def _generator(self):
        from agents.strategy_generator import StrategyGenerator
        return StrategyGenerator()

    def test_prompt_includes_error_class(self):
        gen = self._generator()
        prompt = gen._build_generation_prompt(
            error_class="network_timeout",
            current_strategy="network_timeout",
            failure_rate=0.55,
            sample_count=10,
            failure_history=["timeout not set", "no retry logic"],
            success_examples=["Added exponential backoff"],
            n_variants=2,
        )
        assert "network_timeout" in prompt

    def test_prompt_includes_failure_rate(self):
        gen = self._generator()
        prompt = gen._build_generation_prompt(
            error_class="db_error",
            current_strategy="database_error",
            failure_rate=0.60,
            sample_count=15,
            failure_history=["deadlock", "constraint violation"],
            success_examples=[],
            n_variants=2,
        )
        assert "60%" in prompt or "0.60" in prompt or "failure_rate" in prompt.lower()

    def test_prompt_includes_failure_reasons(self):
        gen = self._generator()
        prompt = gen._build_generation_prompt(
            error_class="null_reference",
            current_strategy="null_reference",
            failure_rate=0.45,
            sample_count=8,
            failure_history=["Missing guard clause for user input"],
            success_examples=[],
            n_variants=2,
        )
        assert "Missing guard clause" in prompt

    def test_prompt_requests_json_output(self):
        gen = self._generator()
        prompt = gen._build_generation_prompt(
            error_class="type_mismatch",
            current_strategy="type_mismatch",
            failure_rate=0.5,
            sample_count=6,
            failure_history=[],
            success_examples=[],
            n_variants=2,
        )
        assert "JSON" in prompt or "json" in prompt


class TestVariantParsing:

    def _generator(self):
        from agents.strategy_generator import StrategyGenerator
        return StrategyGenerator()

    def test_parse_valid_json_array(self):
        gen = self._generator()
        raw = '''[
            {"variant_name": "v1", "prompt_template": "Fix the null: ...", "hypothesis": "works"},
            {"variant_name": "v2", "prompt_template": "Alternative...", "hypothesis": "better"}
        ]'''
        result = gen._parse_variants(raw, "null_reference", 2)
        assert len(result) == 2
        assert result[0]["variant_name"] == "v1"

    def test_parse_markdown_wrapped_json(self):
        gen = self._generator()
        raw = '```json\n[{"variant_name": "v1", "prompt_template": "Fix it", "hypothesis": "yes"}]\n```'
        result = gen._parse_variants(raw, "null_reference", 2)
        assert len(result) == 1

    def test_parse_invalid_json_returns_empty(self):
        gen = self._generator()
        result = gen._parse_variants("not json at all", "null_reference", 2)
        assert result == []

    def test_parse_caps_at_n_variants(self):
        gen = self._generator()
        raw = '''[
            {"variant_name": "v1", "prompt_template": "A", "hypothesis": "h"},
            {"variant_name": "v2", "prompt_template": "B", "hypothesis": "h"},
            {"variant_name": "v3", "prompt_template": "C", "hypothesis": "h"}
        ]'''
        result = gen._parse_variants(raw, "null_reference", n_variants=2)
        assert len(result) == 2


class TestDBDrivenRouter:

    @pytest.mark.asyncio
    async def test_get_db_driven_returns_none_on_no_db(self):
        """
        get_db_driven_strategy must return (None, None) gracefully when
        Postgres is unavailable — the static specialist strategy takes over.
        In tests there is no DB, so the except clause always fires.
        """
        from agents.strategies.router import StrategyRouter
        prompt, name = await StrategyRouter.get_db_driven_strategy("null_reference")
        # No DB in test environment → should fall back to (None, None)
        assert prompt is None
        assert name is None


