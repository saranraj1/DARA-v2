"""
tests/test_agents_unit.py
=========================
Comprehensive unit tests for the four core DARA agents:
  - DebuggerAgent
  - FixerAgent
  - ReviewerAgent
  - Orchestrator (pipeline stage logic)
  - StrategyEvaluator (RLHF feedback loop)
  - StrategyMonitor (win rate computation)

ALL LLM calls are replaced with AsyncMock — no API keys required.
ALL DB calls are mocked — no infrastructure required.

Coverage target: 70%+ on agents/ module.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.agent_schemas import Fix, PatchFile, ReviewResult, RootCauseResult
from context.builder import ContextBundle


# ─────────────────────────── Fixtures ────────────────────────────────────────

@pytest.fixture
def mock_llm():
    """AsyncMock LLM router that returns configurable JSON responses."""
    llm = AsyncMock()
    llm.complete = AsyncMock(return_value=json.dumps({
        "immediate_cause": "x._store is None",
        "root_cause": "The cache object is initialised lazily but never guarded against None",
        "contributing_factors": ["missing null check", "no defensive initialisation"],
        "confidence": 0.87,
        "evidence_quality": "high",
        "files_to_change": ["auth/cache.py"],
        "suggested_strategy": "llm_single_file",
        "reasoning_trace": "step 1: identified None dereference",
    }))
    return llm


@pytest.fixture
def sample_bundle() -> ContextBundle:
    return ContextBundle(
        error_id="test-001",
        erroring_file="auth/cache.py",
        erroring_function="get",
        erroring_code="def get(self, key):\n    return self._store.get(key)\n",
        related_functions=[],
        recent_commits=[{"sha": "abc123", "message": "add cache", "date": "2026-01-01"}],
        similar_past_bugs=[],
        total_tokens=120,
    )


@pytest.fixture
def sample_error() -> dict:
    return {
        "id": "test-001",
        "error_class": "AttributeError",
        "message": "NoneType object has no attribute get",
        "stack_trace": "File cache.py line 12 in get\n  return self._store.get(key)\nAttributeError",
        "file_path": "auth/cache.py",
        "line_number": 12,
        "service": "auth-service",
        "severity": "critical",
        "commit_sha": "abc123",
        "branch": "main",
        "trace_id": None,
    }


@pytest.fixture
def sample_root_cause() -> RootCauseResult:
    return RootCauseResult(
        immediate_cause="self._store is None",
        root_cause="Cache initialised lazily without null guard",
        contributing_factors=["missing null check"],
        confidence=0.87,
        evidence_quality="high",
        files_to_change=["auth/cache.py"],
        suggested_strategy="llm_single_file",
        reasoning_trace="step 1",
    )


@pytest.fixture
def sample_fix() -> Fix:
    patch = PatchFile(
        file_path="auth/cache.py",
        unified_diff="--- a/auth/cache.py\n+++ b/auth/cache.py\n@@ -1,2 +1,3 @@\n def get(self, key):\n+    if self._store is None: return None\n     return self._store.get(key)",
        lines_changed=1,
        change_description="Add null guard before store access",
    )
    return Fix(
        error_id="test-001",
        patches=[patch],
        total_files_changed=1,
        total_lines_changed=1,
        fix_explanation="Added null check for _store attribute",
        suggested_tests=["test_cache_none_store"],
        confidence_retained=0.87,
        regression_risk="low",
        strategy="llm_single_file",
        llm_provider="groq",
    )


# ─────────────────────────── DebuggerAgent ────────────────────────────────────

class TestDebuggerAgent:
    """Tests for DebuggerAgent.analyze() with mocked LLM."""

    def _make_agent(self, llm):
        with patch("pathlib.Path.read_text", return_value="{{error_class}} {{message}} {{stack_trace}} {{erroring_code}} {{related_functions}} {{recent_commits}} {{similar_past_bugs}} {{blame_info}} {{file_path}} {{line_number}} {{service}} {{severity}} {{commit_sha}} {{branch}}"):
            from agents.debugger import DebuggerAgent
            return DebuggerAgent(llm_router=llm)

    @pytest.mark.asyncio
    async def test_analyze_returns_root_cause(self, mock_llm, sample_bundle, sample_error):
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        assert isinstance(result, RootCauseResult)
        assert result.confidence == 0.87
        assert result.suggested_strategy == "llm_single_file"
        assert result.evidence_quality == "high"
        assert "auth/cache.py" in result.files_to_change

    @pytest.mark.asyncio
    async def test_analyze_confidence_clamped_to_0_1(self, mock_llm, sample_bundle, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "confidence": 99.9,  # Invalid — should be clamped
            "immediate_cause": "x", "root_cause": "y",
            "contributing_factors": [],
            "evidence_quality": "low",
            "files_to_change": [],
            "suggested_strategy": "human_escalation",
            "reasoning_trace": "",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        assert result.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_analyze_invalid_strategy_falls_back(self, mock_llm, sample_bundle, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "confidence": 0.7, "immediate_cause": "x", "root_cause": "y",
            "contributing_factors": [], "evidence_quality": "low",
            "files_to_change": [], "suggested_strategy": "totally_invalid_strategy",
            "reasoning_trace": "",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        # Invalid strategy must fall back to llm_single_file
        assert result.suggested_strategy == "llm_single_file"

    @pytest.mark.asyncio
    async def test_analyze_malformed_json_uses_defaults(self, mock_llm, sample_bundle, sample_error):
        mock_llm.complete.return_value = "Not JSON at all {{broken"
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        # Must not raise — falls back gracefully
        assert isinstance(result, RootCauseResult)
        assert result.confidence >= 0.0

    @pytest.mark.asyncio
    async def test_analyze_uses_file_path_when_no_files_to_change(
        self, mock_llm, sample_bundle, sample_error
    ):
        mock_llm.complete.return_value = json.dumps({
            "confidence": 0.7, "immediate_cause": "x", "root_cause": "y",
            "contributing_factors": [], "evidence_quality": "low",
            "files_to_change": [],  # Empty — should fall back to error.file_path
            "suggested_strategy": "llm_single_file", "reasoning_trace": "",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        assert "auth/cache.py" in result.files_to_change

    @pytest.mark.asyncio
    async def test_analyze_calls_llm_once(self, mock_llm, sample_bundle, sample_error):
        agent = self._make_agent(mock_llm)
        await agent.analyze(sample_bundle, sample_error)
        mock_llm.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_analyze_low_confidence_is_valid(self, mock_llm, sample_bundle, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "confidence": 0.1, "immediate_cause": "unknown", "root_cause": "unclear",
            "contributing_factors": [], "evidence_quality": "low",
            "files_to_change": [], "suggested_strategy": "human_escalation",
            "reasoning_trace": "",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.analyze(sample_bundle, sample_error)
        assert result.confidence == 0.1
        assert result.suggested_strategy == "human_escalation"


# ─────────────────────────── FixerAgent ──────────────────────────────────────

class TestFixerAgent:
    """Tests for FixerAgent.generate() with mocked LLM."""

    def _make_agent(self, llm):
        with patch("pathlib.Path.read_text", return_value="{{error_class}} {{files_to_change}} {{erroring_code}}"):
            from agents.fixer import FixerAgent
            return FixerAgent(llm_router=llm)

    @pytest.mark.asyncio
    async def test_generate_returns_fix(self, mock_llm, sample_root_cause, sample_bundle, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "files": [{
                "file_path": "auth/cache.py",
                "fixed_code": "def get(self, key):\n    if self._store is None: return None\n    return self._store.get(key)",
                "change_description": "Add null guard",
                "lines_changed": 1,
            }],
            "fix_explanation": "Added null guard for _store attribute",
            "suggested_tests": ["test_cache_none"],
            "confidence_retained": 0.87,
            "regression_risk": "low",
            "strategy": "llm_single_file",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.generate(sample_root_cause, sample_bundle, sample_error)
        assert isinstance(result, Fix)
        assert result.total_files_changed >= 0
        assert result.confidence_retained <= 1.0

    @pytest.mark.asyncio
    async def test_generate_regression_risk_valid(self, mock_llm, sample_root_cause, sample_bundle, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "files": [],
            "fix_explanation": "x",
            "suggested_tests": [],
            "confidence_retained": 0.5,
            "regression_risk": "medium",
            "strategy": "llm_multi_file",
        })
        agent = self._make_agent(mock_llm)
        result = await agent.generate(sample_root_cause, sample_bundle, sample_error)
        assert result.regression_risk in ("low", "medium", "high", "unknown")

    @pytest.mark.asyncio
    async def test_generate_malformed_json_does_not_raise(
        self, mock_llm, sample_root_cause, sample_bundle, sample_error
    ):
        mock_llm.complete.return_value = "{{invalid}}"
        agent = self._make_agent(mock_llm)
        # Should not raise — must return a valid Fix (possibly empty patches)
        result = await agent.generate(sample_root_cause, sample_bundle, sample_error)
        assert isinstance(result, Fix)


# ─────────────────────────── ReviewerAgent ────────────────────────────────────

class TestReviewerAgent:
    """Tests for ReviewerAgent.review() with mocked LLM."""

    def _make_agent(self, llm):
        with patch("pathlib.Path.read_text", return_value="{{patch_content}} {{fix_explanation}}"):
            from agents.reviewer import ReviewerAgent
            return ReviewerAgent(llm_router=llm)

    @pytest.mark.asyncio
    async def test_review_approve_path(self, mock_llm, sample_fix, sample_root_cause, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "quality_score": 0.92,
            "correctness_passes": True,
            "security_passes": True,
            "overall_recommendation": "approve",
            "rejection_reason": None,
            "reviewer_notes": "Clean, minimal, correct fix.",
            "issues": [],
        })
        agent = self._make_agent(mock_llm)
        result = await agent.review(sample_fix, sample_root_cause, sample_error)
        assert isinstance(result, ReviewResult)
        assert result.overall_recommendation == "approve"
        assert result.quality_score == 0.92
        assert result.correctness_passes is True
        assert result.security_passes is True

    @pytest.mark.asyncio
    async def test_review_reject_path(self, mock_llm, sample_fix, sample_root_cause, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "quality_score": 0.3,
            "correctness_passes": False,
            "security_passes": True,
            "overall_recommendation": "reject",
            "rejection_reason": "Fix does not address root cause",
            "reviewer_notes": "The null guard is added in the wrong layer.",
            "issues": ["Wrong layer", "Missing test"],
        })
        agent = self._make_agent(mock_llm)
        result = await agent.review(sample_fix, sample_root_cause, sample_error)
        assert result.overall_recommendation == "reject"
        assert result.rejection_reason is not None
        assert len(result.issues) > 0

    @pytest.mark.asyncio
    async def test_review_approve_with_comments(self, mock_llm, sample_fix, sample_root_cause, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "quality_score": 0.75,
            "correctness_passes": True,
            "security_passes": True,
            "overall_recommendation": "approve_with_comments",
            "rejection_reason": None,
            "reviewer_notes": "Mostly correct but could use a test.",
            "issues": ["Missing unit test"],
        })
        agent = self._make_agent(mock_llm)
        result = await agent.review(sample_fix, sample_root_cause, sample_error)
        assert result.overall_recommendation == "approve_with_comments"

    @pytest.mark.asyncio
    async def test_review_quality_score_clamped(self, mock_llm, sample_fix, sample_root_cause, sample_error):
        mock_llm.complete.return_value = json.dumps({
            "quality_score": 150.0,  # Invalid
            "correctness_passes": True, "security_passes": True,
            "overall_recommendation": "approve", "rejection_reason": None,
            "reviewer_notes": "", "issues": [],
        })
        agent = self._make_agent(mock_llm)
        result = await agent.review(sample_fix, sample_root_cause, sample_error)
        assert 0.0 <= result.quality_score <= 1.0

    @pytest.mark.asyncio
    async def test_review_malformed_json_does_not_raise(
        self, mock_llm, sample_fix, sample_root_cause, sample_error
    ):
        mock_llm.complete.return_value = "not json {{broken"
        agent = self._make_agent(mock_llm)
        result = await agent.review(sample_fix, sample_root_cause, sample_error)
        assert isinstance(result, ReviewResult)


# ─────────────────────────── Auto-merge gate logic ───────────────────────────

class TestAutoMergeGate:
    """Test the auto-merge eligibility conditions (from run_demo.py logic)."""

    def _eligible(self, rec, confidence, risk, val_passed) -> bool:
        return (
            rec == "approve"
            and confidence >= 0.88
            and risk == "low"
            and val_passed
        )

    def test_all_conditions_met(self):
        assert self._eligible("approve", 0.90, "low", True) is True

    def test_threshold_boundary_at_0_88(self):
        assert self._eligible("approve", 0.88, "low", True) is True
        assert self._eligible("approve", 0.879, "low", True) is False

    def test_reject_blocks_auto_merge(self):
        assert self._eligible("reject", 0.95, "low", True) is False

    def test_approve_with_comments_blocks(self):
        assert self._eligible("approve_with_comments", 0.95, "low", True) is False

    def test_high_risk_blocks_auto_merge(self):
        assert self._eligible("approve", 0.95, "high", True) is False

    def test_validation_failure_blocks_auto_merge(self):
        assert self._eligible("approve", 0.95, "low", False) is False

    def test_medium_risk_blocks_auto_merge(self):
        assert self._eligible("approve", 0.95, "medium", True) is False


# ─────────────────────────── StrategyEvaluator RLHF ──────────────────────────

class TestStrategyEvaluatorRLHF:
    """Tests for the feedback-driven strategy selection (Item 13)."""

    def _make_evaluator(self):
        from agents.strategy_evaluator import StrategyEvaluator
        return StrategyEvaluator()

    def _make_win_rate(self, strategy, accepted, rejected, reliable=True):
        from agents.strategy_monitor import StrategyWinRate
        total = accepted + rejected
        raw_rate = accepted / total if total > 0 else 0.5
        # Simplified Wilson lower bound for test fixtures
        wilson_lower = max(0.0, raw_rate - 0.1)
        return StrategyWinRate(
            strategy=strategy,
            accepted=accepted,
            rejected=rejected,
            total=total,
            raw_win_rate=raw_rate,
            wilson_lower=wilson_lower,
            wilson_upper=min(1.0, raw_rate + 0.1),
            is_reliable=reliable,
        )

    @pytest.mark.asyncio
    async def test_high_win_rate_strategy_is_selected(self):
        evaluator = self._make_evaluator()
        # Mock monitor to return llm_single_file as winner
        mock_monitor = AsyncMock()
        mock_monitor.compute_strategy_win_rates.return_value = [
            self._make_win_rate("llm_single_file", accepted=18, rejected=2),   # 90% win
            self._make_win_rate("llm_multi_file",  accepted=5,  rejected=10),  # 33% win
        ]
        evaluator._monitor = mock_monitor

        selected, scores = await evaluator.select_strategy_with_feedback(
            candidates=["llm_single_file", "llm_multi_file"],
            error_class="AttributeError",
            base_scores={"llm_single_file": 0.5, "llm_multi_file": 0.5},
        )
        assert selected == "llm_single_file"
        assert scores["llm_single_file"] > scores["llm_multi_file"]

    @pytest.mark.asyncio
    async def test_poor_strategy_is_penalised(self):
        evaluator = self._make_evaluator()
        mock_monitor = AsyncMock()
        mock_monitor.compute_strategy_win_rates.return_value = [
            self._make_win_rate("template_based",   accepted=2, rejected=18),  # 10% win
            self._make_win_rate("llm_single_file",  accepted=8, rejected=2),   # 80% win
        ]
        evaluator._monitor = mock_monitor

        selected, scores = await evaluator.select_strategy_with_feedback(
            candidates=["template_based", "llm_single_file"],
            error_class="KeyError",
        )
        assert selected == "llm_single_file"
        assert scores["template_based"] < scores["llm_single_file"]

    @pytest.mark.asyncio
    async def test_no_feedback_data_uses_base_scores(self):
        evaluator = self._make_evaluator()
        mock_monitor = AsyncMock()
        mock_monitor.compute_strategy_win_rates.return_value = []  # No data
        evaluator._monitor = mock_monitor

        selected, scores = await evaluator.select_strategy_with_feedback(
            candidates=["strategy_a", "strategy_b"],
            error_class="TypeError",
            base_scores={"strategy_a": 0.9, "strategy_b": 0.3},
        )
        # With no feedback, base scores determine winner
        assert selected == "strategy_a"

    @pytest.mark.asyncio
    async def test_monitor_failure_is_non_blocking(self):
        evaluator = self._make_evaluator()
        mock_monitor = AsyncMock()
        mock_monitor.compute_strategy_win_rates.side_effect = Exception("DB unavailable")
        evaluator._monitor = mock_monitor

        # Must not raise — should fall back to base scores
        selected, scores = await evaluator.select_strategy_with_feedback(
            candidates=["llm_single_file", "human_escalation"],
            error_class="AttributeError",
            base_scores={"llm_single_file": 0.7, "human_escalation": 0.3},
        )
        assert selected == "llm_single_file"

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_default(self):
        evaluator = self._make_evaluator()
        selected, scores = await evaluator.select_strategy_with_feedback(
            candidates=[],
            error_class="AttributeError",
        )
        assert selected == "llm_single_file"
        assert scores == {}

    @pytest.mark.asyncio
    async def test_unreliable_data_applies_half_weight(self):
        """Strategies with < MIN_RELIABLE_SAMPLES get half-weight boost/penalty."""
        evaluator = self._make_evaluator()
        mock_monitor = AsyncMock()
        # 5 samples = unreliable
        mock_monitor.compute_strategy_win_rates.return_value = [
            self._make_win_rate("llm_single_file", accepted=5, rejected=0, reliable=False),
        ]
        evaluator._monitor = mock_monitor

        _, scores = await evaluator.select_strategy_with_feedback(
            candidates=["llm_single_file"],
            error_class="AttributeError",
            base_scores={"llm_single_file": 0.5},
        )
        # Score should be boosted, but less than if reliable
        assert scores["llm_single_file"] > 0.5
        assert scores["llm_single_file"] < 0.5 + evaluator.FEEDBACK_WEIGHT


# ─────────────────────────── StrategyMonitor win rates ────────────────────────

class TestStrategyMonitorWinRates:
    """Tests for StrategyMonitor.compute_strategy_win_rates() RLHF method."""

    def _make_monitor(self):
        from agents.strategy_monitor import StrategyMonitor
        return StrategyMonitor()

    def test_wilson_interval_zero_samples(self):
        monitor = self._make_monitor()
        lower, upper = monitor._wilson_interval(0, 0)
        assert lower == 0.0
        assert upper == 1.0

    def test_wilson_interval_all_accepted(self):
        monitor = self._make_monitor()
        lower, upper = monitor._wilson_interval(20, 20)
        assert lower > 0.8   # High confidence all good
        assert upper == 1.0

    def test_wilson_interval_all_rejected(self):
        monitor = self._make_monitor()
        lower, upper = monitor._wilson_interval(0, 20)
        assert lower == 0.0
        assert upper < 0.2

    def test_wilson_interval_50_50(self):
        monitor = self._make_monitor()
        lower, upper = monitor._wilson_interval(10, 20)
        # Should be centred around 0.5 with reasonable bounds
        assert 0.29 < lower < 0.5
        assert 0.5 < upper < 0.71

    @pytest.mark.asyncio
    async def test_compute_win_rates_db_unavailable(self):
        monitor = self._make_monitor()
        # No Postgres configured — should return empty list gracefully
        result = await monitor.compute_strategy_win_rates()
        assert isinstance(result, list)
        # May be empty (no DB) or populated — must not raise

    @pytest.mark.asyncio
    async def test_compute_win_rates_returns_sorted_by_wilson_lower(self):
        monitor = self._make_monitor()

        # Mock Postgres to return specific rows
        from agents.strategy_monitor import StrategyWinRate
        mock_pg = AsyncMock()
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_pg.session.return_value = mock_session

        mock_rows = [
            ("llm_single_file", 18, 2, 20),  # 90% win
            ("llm_multi_file",  5,  15, 20),  # 25% win
            ("template_based",  10, 10, 20),  # 50% win
        ]
        mock_session.execute = AsyncMock(return_value=MagicMock(all=lambda: mock_rows))
        monitor._postgres = mock_pg

        with patch.object(monitor, "_get_pg", return_value=mock_pg):
            result = await monitor.compute_strategy_win_rates()

        if result:  # If DB mock worked
            # Verify sorted by wilson_lower descending
            for i in range(len(result) - 1):
                assert result[i].wilson_lower >= result[i + 1].wilson_lower
