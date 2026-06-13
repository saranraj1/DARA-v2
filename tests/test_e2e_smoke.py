"""
DARA — End-to-End Smoke Test
==============================
Validates the full pipeline contract without real external services.
Fast, offline, deterministic.

What this tests:
  1. Full PipelineResult dataclass shape + field types
  2. Orchestrator stage flow logic (all 12 stages, happy path)
  3. Escalation gate (confidence < 0.35 → escalated)
  4. Template-hit fast path (now includes memory consolidation)
  5. Validation gate (validation failed → recommendation forced to reject)
  6. Auto-approve gate (confidence high + risk low + validation passed)
  7. Auto-approved fixes → GitHub PR created with [AUTO-APPROVED] prefix
  8. StrategyRouter → correct specialist returned for each error class
  9. MemoryConsolidation agent is wired (not asyncio.create_task crashing)
  10. _resolve_github_repo security: strips path traversal, uses org prefix

Run with:
  pytest tests/test_e2e_smoke.py -v
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _root_cause(confidence=0.87, strategy="llm_single_file"):
    rc = MagicMock()
    rc.confidence = confidence
    rc.suggested_strategy = strategy
    rc.root_cause = "NullPointerException in get_user at line 42"
    return rc


def _fix(confidence=0.90, risk="low"):
    fix = MagicMock()
    fix.confidence_retained = confidence
    fix.regression_risk = risk
    fix.strategy = "null_reference"
    fix.fix_explanation = "Added null guard before attribute access"
    fix.total_lines_changed = 3
    fix.patches = [MagicMock(file_path="app.py", unified_diff="--- a/app.py\n+++ b/app.py\n")]
    return fix


def _review(recommendation="approve", score=0.91, reason=None):
    r = MagicMock()
    r.overall_recommendation = recommendation
    r.quality_score = score
    r.rejection_reason = reason
    return r


def _validation(passed=True, issues=None):
    v = MagicMock()
    v.passed = passed
    v.blocking_issues = issues or []
    v.test_results = None
    v.total_duration_ms = 850
    v.final_fix = None
    return v


def _make_orch():
    """Build an Orchestrator with all dependencies mocked."""
    from agents.orchestrator import Orchestrator

    pg = MagicMock()
    redis = MagicMock()
    llm = MagicMock()

    # Async methods
    pg.get_error = AsyncMock(return_value=MagicMock(
        id="00000000-0000-0000-0000-000000000001",
        error_class="null_reference",
        message="NullPointerException",
        stack_trace="at app.py:42",
        file_path="app.py",
        line_number=42,
        service="orders-svc",
        severity="P2",
        commit_sha="abc123",
        branch="main",
        trace_id="trace-001",
    ))
    pg.update_error_status = AsyncMock()
    pg.save_fix = AsyncMock(return_value="fix-uuid-001")
    pg.update_fix_outcome = AsyncMock()
    pg.update_fix_validation = AsyncMock()

    redis.set_pipeline_state = AsyncMock()

    with (
        patch("agents.orchestrator.ContextBuilder") as MockBuilder,
        patch("agents.orchestrator.ContextRetriever"),
        patch("agents.orchestrator.get_qdrant"),
        patch("agents.orchestrator.SlackNotifier") as MockSlack,
        patch("agents.orchestrator.GitHubPRCreator") as MockGH,
        patch("agents.orchestrator.DebuggerAgent"),
        patch("agents.orchestrator.FixerAgent"),
        patch("agents.orchestrator.ReviewerAgent"),
        patch("agents.orchestrator.PatternMemory") as MockMemory,
        patch("agents.orchestrator.ValidationEngine"),
    ):
        orch = Orchestrator(postgres=pg, redis=redis, llm_router=llm, repo_path=".")

    orch._pg = pg
    orch._redis = redis
    orch._slack = MagicMock()
    orch._slack.notify_fix_ready = AsyncMock(return_value=True)
    orch._slack.notify_escalation = AsyncMock(return_value=True)
    orch._github = MagicMock()
    orch._github.create_pr = AsyncMock(return_value={"pr_url": "https://github.com/org/repo/pull/1", "pr_number": 1})
    orch._memory = MagicMock()
    orch._memory.find_template = AsyncMock(return_value=None)  # no template hit by default
    orch._memory.record_success = AsyncMock()
    orch._memory.record_failure = AsyncMock()
    orch._builder = MagicMock()
    orch._builder.build = AsyncMock(return_value=MagicMock(
        error_id="00000000-0000-0000-0000-000000000001",
        erroring_file="app.py",
        erroring_function="get_user",
        erroring_code="def get_user(uid): return db.find(uid).name",
        summary=lambda: "bundle-summary",
    ))
    orch._debugger = MagicMock()
    orch._fixer = MagicMock()
    orch._reviewer = MagicMock()
    orch._validator = MagicMock()
    return orch


# ---------------------------------------------------------------------------
# 1. PipelineResult structure
# ---------------------------------------------------------------------------

class TestPipelineResultStructure:
    def test_dataclass_fields(self):
        from agents.orchestrator import PipelineResult
        r = PipelineResult(error_id="x", status="started")
        assert r.error_id == "x"
        assert r.status == "started"
        assert r.fix_id is None
        assert r.pr_url is None
        assert r.slack_sent is False
        assert r.root_cause is None

    def test_stage_reached_defaults_to_start(self):
        from agents.orchestrator import PipelineResult
        r = PipelineResult(error_id="y", status="fixed")
        assert r.stage_reached == "start"


# ---------------------------------------------------------------------------
# 2. Happy path (full pipeline)
# ---------------------------------------------------------------------------

class TestHappyPath:
    @pytest.mark.asyncio
    async def test_full_pipeline_returns_fixed(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.87))
        orch._fixer.generate = AsyncMock(return_value=_fix(0.90, "low"))
        orch._validator.validate = AsyncMock(return_value=_validation(True))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        result = await orch.run("00000000-0000-0000-0000-000000000001")

        assert result.status == "fixed"
        assert result.fix_id == "fix-uuid-001"
        assert result.stage_reached == "fixed"

    @pytest.mark.asyncio
    async def test_auto_approve_creates_pr_with_auto_prefix(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.92))
        fix = _fix(0.92, "low")
        orch._fixer.generate = AsyncMock(return_value=fix)
        orch._validator.validate = AsyncMock(return_value=_validation(True))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        result = await orch.run("00000000-0000-0000-0000-000000000001")

        # Auto-approved → record_success called
        orch._memory.record_success.assert_called_once()

        # At least one PR must be flagged [AUTO-APPROVED]
        # (Stage 10b creates AUTO-APPROVED PR; Stage 12 may also create one)
        all_calls = orch._github.create_pr.call_args_list
        assert len(all_calls) >= 1
        auto_calls = [
            c for c in all_calls
            if "[AUTO-APPROVED]" in (c[1].get("fix_explanation") or "")
        ]
        assert len(auto_calls) == 1, "Expected exactly 1 [AUTO-APPROVED] PR"
        assert result.pr_url == "https://github.com/org/repo/pull/1"

    @pytest.mark.asyncio
    async def test_slack_notification_sent(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.87))
        orch._fixer.generate = AsyncMock(return_value=_fix(0.87, "medium"))
        orch._validator.validate = AsyncMock(return_value=_validation(True))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        result = await orch.run("00000000-0000-0000-0000-000000000001")

        assert result.slack_sent is True


# ---------------------------------------------------------------------------
# 3. Escalation gate
# ---------------------------------------------------------------------------

class TestEscalationGate:
    @pytest.mark.asyncio
    async def test_low_confidence_escalates(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.30))  # < 0.35
        result = await orch.run("00000000-0000-0000-0000-000000000001")
        assert result.status == "escalated"
        assert result.stage_reached == "escalated"

    @pytest.mark.asyncio
    async def test_human_escalation_strategy_escalates(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(
            return_value=_root_cause(0.90, strategy="human_escalation")
        )
        result = await orch.run("00000000-0000-0000-0000-000000000001")
        assert result.status == "escalated"
        orch._slack.notify_escalation.assert_called_once()


# ---------------------------------------------------------------------------
# 4. Template-hit fast path
# ---------------------------------------------------------------------------

class TestTemplateFastPath:
    @pytest.mark.asyncio
    async def test_template_hit_returns_immediately(self):
        orch = _make_orch()
        orch._memory.find_template = AsyncMock(return_value={
            "template_id": "tmpl-001",
            "fix_template": "add null guard",
            "success_rate": 0.92,
        })

        result = await orch.run("00000000-0000-0000-0000-000000000001")

        assert result.status == "template_hit"
        assert result.stage_reached == "template"
        # FLAW-2 fix: memory.record_success must be called for template hits
        orch._memory.record_success.assert_called_once()
        call_kwargs = orch._memory.record_success.call_args[1]
        assert call_kwargs.get("outcome") == "template_hit"

    @pytest.mark.asyncio
    async def test_low_success_rate_template_does_not_fast_path(self):
        """Template with rate < 0.85 must NOT trigger fast path."""
        orch = _make_orch()
        orch._memory.find_template = AsyncMock(return_value={
            "template_id": "tmpl-002",
            "success_rate": 0.70,  # below threshold
        })
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.87))
        orch._fixer.generate = AsyncMock(return_value=_fix())
        orch._validator.validate = AsyncMock(return_value=_validation(True))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        result = await orch.run("00000000-0000-0000-0000-000000000001")
        assert result.status == "fixed"  # went through full pipeline


# ---------------------------------------------------------------------------
# 5. Validation gate
# ---------------------------------------------------------------------------

class TestValidationGate:
    @pytest.mark.asyncio
    async def test_failed_validation_forces_rejection(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.90))
        orch._fixer.generate = AsyncMock(return_value=_fix())
        orch._validator.validate = AsyncMock(return_value=_validation(
            passed=False, issues=["S501: eval() used", "E901: SyntaxError"]
        ))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        result = await orch.run("00000000-0000-0000-0000-000000000001")

        # Review recommendation overridden to reject
        assert result.review.overall_recommendation == "reject"
        assert "Validation failed" in result.review.rejection_reason

    @pytest.mark.asyncio
    async def test_failed_validation_does_not_auto_approve(self):
        orch = _make_orch()
        orch._debugger.analyze = AsyncMock(return_value=_root_cause(0.99))
        fix = _fix(0.99, "low")
        orch._fixer.generate = AsyncMock(return_value=fix)
        orch._validator.validate = AsyncMock(return_value=_validation(passed=False))
        orch._reviewer.review = AsyncMock(return_value=_review("approve"))

        # Even with confidence=0.99 + reviewer=approve, validation failure blocks auto-approve
        orch._memory.record_success.reset_mock()
        result = await orch.run("00000000-0000-0000-0000-000000000001")
        orch._memory.record_success.assert_not_called()


# ---------------------------------------------------------------------------
# 6. Error-not-found guard
# ---------------------------------------------------------------------------

class TestErrorNotFound:
    @pytest.mark.asyncio
    async def test_missing_error_returns_error_not_found(self):
        orch = _make_orch()
        orch._pg.get_error = AsyncMock(return_value=None)
        result = await orch.run("non-existent-id")
        assert result.status == "error_not_found"


# ---------------------------------------------------------------------------
# 7. SEV-6: _resolve_github_repo security
# ---------------------------------------------------------------------------

class TestResolveGithubRepo:
    def _orch(self):
        orch = _make_orch()
        orch._settings = MagicMock()
        orch._settings.known_github_repos = {}
        orch._settings.github_org = "acme-corp"
        return orch

    def test_clean_service_name_uses_org_prefix(self):
        orch = self._orch()
        assert orch._resolve_github_repo("orders-svc") == "acme-corp/orders-svc"

    def test_path_traversal_stripped(self):
        orch = self._orch()
        # Attacker tries: service = "../other-org/sensitive"
        result = orch._resolve_github_repo("../other-org/sensitive")
        # Must NOT contain ".." or "/"
        assert ".." not in result
        assert result.startswith("acme-corp/")

    def test_empty_service_uses_fallback(self):
        orch = self._orch()
        result = orch._resolve_github_repo("")
        assert result == "acme-corp/unknown-service"

    def test_allowlist_overrides_regex(self):
        orch = self._orch()
        orch._settings.known_github_repos = {"payments": "acme-corp/payments-v2"}
        result = orch._resolve_github_repo("payments")
        assert result == "acme-corp/payments-v2"

    def test_special_chars_stripped(self):
        orch = self._orch()
        result = orch._resolve_github_repo("my service!@#$%^&*()")
        # Only alphanum + dash + underscore allowed
        assert result == "acme-corp/myservice"


# ---------------------------------------------------------------------------
# 8. StrategyRouter smoke
# ---------------------------------------------------------------------------

class TestStrategyRouterSmoke:
    def test_all_8_error_classes_resolve(self):
        from agents.strategies.router import StrategyRouter

        classes = [
            "null_reference", "type_mismatch", "network_timeout",
            "database_error", "authentication", "resource_leak",
            "concurrency", "logic_error",
        ]
        for ec in classes:
            strategy = StrategyRouter.get_strategy(ec)
            assert strategy is not None
            prompt = strategy.generate_fix_prompt(
                MagicMock(erroring_code="def f(): pass", erroring_file="a.py", erroring_function="f"),
                MagicMock(root_cause="bug", suggested_strategy="llm", confidence=0.8),
                {"error_class": ec, "service": "svc", "message": "err"},
            )
            assert len(prompt) > 100

    def test_unknown_class_falls_back_to_logic_error(self):
        from agents.strategies.router import StrategyRouter, LogicErrorStrategy
        strategy = StrategyRouter.get_strategy("totally_unknown_error")
        assert isinstance(strategy, LogicErrorStrategy)

    def test_confidence_boost_applied(self):
        from agents.strategies.router import StrategyRouter

        rc = MagicMock(root_cause="NoneType object", confidence=0.7)
        bundle = MagicMock(erroring_code="if x is None: return None", erroring_function="f")
        strategy, boost = StrategyRouter.get_strategy_with_boost("null_reference", rc, bundle)
        assert boost >= 0.0
        assert boost <= 0.30


# ---------------------------------------------------------------------------
# 9. _schedule_consolidation doesn't crash in sync context
# ---------------------------------------------------------------------------

class TestScheduleConsolidation:
    def test_schedule_consolidation_in_sync_context(self):
        """Must not raise RuntimeError even when called from non-async context."""
        from agents.memory import _schedule_consolidation

        async def _noop():
            pass

        try:
            # No running loop → must fall back to daemon thread silently
            _schedule_consolidation(_noop())
        except RuntimeError as e:
            pytest.fail(f"_schedule_consolidation raised RuntimeError in sync context: {e}")

    @pytest.mark.asyncio
    async def test_schedule_consolidation_in_async_context(self):
        """In async context, should use loop.create_task without error."""
        from agents.memory import _schedule_consolidation

        results = []

        async def _capture():
            results.append("ran")

        _schedule_consolidation(_capture())
        await asyncio.sleep(0.05)  # allow task to complete
        assert results == ["ran"]
