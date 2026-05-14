"""
Tests: Week 14-15 — Memory Consolidation Agent
Covers:
  - _compute_signature_hash: deterministic same hash for same input
  - _compute_signature_hash: different error_class → different hash
  - _extract_code_pattern: returns None for empty code
  - _extract_code_pattern: returns CodePatternData with ast_hash for valid Python
  - on_fix_accepted: calls neo4j.upsert_error_pattern with correct class
  - on_fix_accepted: calls neo4j.link_fix_to_pattern with outcome='accepted'
  - on_fix_rejected: calls neo4j.increment_pattern_rejection
"""
import sys

sys.path.insert(0, ".")
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_agent():
    from agents.memory_consolidation import MemoryConsolidationAgent
    mock_neo4j = MagicMock()
    mock_neo4j.upsert_error_pattern = AsyncMock()
    mock_neo4j.upsert_fix_template = AsyncMock()
    mock_neo4j.link_fix_to_pattern = AsyncMock()
    mock_neo4j.increment_pattern_acceptance = AsyncMock()
    mock_neo4j.increment_pattern_rejection = AsyncMock()
    mock_neo4j.update_fix_template_outcome = AsyncMock()
    mock_neo4j.upsert_code_pattern = AsyncMock()
    mock_neo4j.link_code_to_error = AsyncMock()

    mock_pg = MagicMock()
    mock_pg.session = MagicMock(return_value=MagicMock(
        __aenter__=AsyncMock(return_value=MagicMock(execute=AsyncMock())),
        __aexit__=AsyncMock(return_value=False),
    ))

    return MemoryConsolidationAgent(neo4j=mock_neo4j, postgres=mock_pg), mock_neo4j


def _make_fix(confidence=0.85, strategy="null_reference", explanation="Fixed null check"):
    fix = SimpleNamespace()
    fix.fix_id = "fix-001"
    fix.error_id = "err-001"
    fix.confidence_retained = confidence
    fix.strategy = strategy
    fix.fix_explanation = explanation
    return fix


def _make_root_cause(rc="Object was None at line 42"):
    rc_obj = SimpleNamespace()
    rc_obj.root_cause = rc
    rc_obj.immediate_cause = "AttributeError"
    rc_obj.suggested_strategy = "null_reference"
    rc_obj.confidence = 0.85
    return rc_obj


class TestSignatureHash:

    def test_same_inputs_produce_same_hash(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        error = {"error_class": "null_reference"}
        rc = _make_root_cause()
        h1 = agent._compute_signature_hash(error, rc)
        h2 = agent._compute_signature_hash(error, rc)
        assert h1 == h2

    def test_different_class_different_hash(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        rc = _make_root_cause()
        h1 = agent._compute_signature_hash({"error_class": "null_reference"}, rc)
        h2 = agent._compute_signature_hash({"error_class": "type_mismatch"}, rc)
        assert h1 != h2

    def test_hash_is_32_chars(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        h = agent._compute_signature_hash({"error_class": "db_error"}, _make_root_cause())
        assert len(h) == 32


class TestCodePatternExtraction:

    def test_empty_bundle_returns_none(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        assert agent._extract_code_pattern(None) is None

    def test_short_code_returns_none(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        bundle = SimpleNamespace(erroring_code="x = 1", language="python", erroring_function="f")
        # < 20 chars
        assert agent._extract_code_pattern(bundle) is None

    def test_valid_python_returns_code_pattern(self):
        from agents.memory_consolidation import MemoryConsolidationAgent
        agent = MemoryConsolidationAgent()
        bundle = SimpleNamespace(
            erroring_code="def charge_card(amount):\n    if amount is None:\n        raise ValueError('amount')\n    return process(amount)",
            language="python",
            erroring_function="charge_card",
        )
        result = agent._extract_code_pattern(bundle)
        assert result is not None
        assert len(result.ast_hash) == 24
        assert result.language == "python"


class TestConsolidationCallchain:

    @pytest.mark.asyncio
    async def test_on_fix_accepted_calls_upsert_pattern(self):
        agent, neo4j = _make_agent()
        await agent.on_fix_accepted(
            error={"error_class": "null_reference", "service": "api-svc"},
            fix=_make_fix(),
            root_cause=_make_root_cause(),
            bundle=None,
        )
        neo4j.upsert_error_pattern.assert_called_once()
        args = neo4j.upsert_error_pattern.call_args[1]
        assert args["error_class"] == "null_reference"

    @pytest.mark.asyncio
    async def test_on_fix_accepted_links_to_pattern_with_accepted_outcome(self):
        agent, neo4j = _make_agent()
        await agent.on_fix_accepted(
            error={"error_class": "type_mismatch"},
            fix=_make_fix(),
            root_cause=_make_root_cause(),
            bundle=None,
        )
        neo4j.link_fix_to_pattern.assert_called_once()
        call_kwargs = neo4j.link_fix_to_pattern.call_args[1]
        assert call_kwargs["outcome"] == "accepted"

    @pytest.mark.asyncio
    async def test_on_fix_rejected_calls_increment_rejection(self):
        agent, neo4j = _make_agent()
        await agent.on_fix_rejected(
            error={"error_class": "database_error"},
            fix=None,
            root_cause=None,
            reason="Patch failed validation",
        )
        neo4j.increment_pattern_rejection.assert_called_once()
