"""
tests/test_reliability_metrics.py
=================================
Unit tests covering reliability metrics persistence in PostgreSQL for every pipeline run:
  - self-healing iteration count
  - security retry count
  - escalation trigger types (confidence_gate, strategy_escalation, security_blocked, blast_radius)
  - database persistence on success, sandbox failure, and escalation
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.agent_schemas import Fix, PatchFile, ReviewResult, RootCauseResult
from validation.sandbox_runner import SandboxResult
from validation.engine import ValidationReport


def make_patch(diff: str = "+x = 1\n", file_path: str = "app/main.py") -> PatchFile:
    return PatchFile(
        file_path=file_path,
        unified_diff=diff,
        lines_changed=1,
        change_description="test patch",
    )


def make_fix() -> Fix:
    return Fix(
        error_id="err-100",
        patches=[make_patch()],
        total_files_changed=1,
        total_lines_changed=1,
        fix_explanation="Test fix",
        suggested_tests=[],
        confidence_retained=0.9,
        regression_risk="low",
        strategy="llm_single_file",
        llm_provider="groq",
    )


def make_root_cause(confidence: float = 0.85, strategy: str = "llm_single_file") -> RootCauseResult:
    return RootCauseResult(
        immediate_cause="Null reference",
        root_cause="Missing null check",
        contributing_factors=[],
        confidence=confidence,
        evidence_quality="high",
        files_to_change=["app/main.py"],
        suggested_strategy=strategy,
        reasoning_trace="",
    )


@pytest.fixture
def mock_deps():
    pg = AsyncMock()
    redis = AsyncMock()
    llm = AsyncMock()
    return pg, redis, llm


class TestReliabilityMetricsPersistence:
    """Test suite for persistence of reliability metrics across all pipeline paths."""

    @pytest.mark.asyncio
    async def test_metrics_persisted_on_success(self, mock_deps):
        pg, redis, llm = mock_deps
        from agents.orchestrator import Orchestrator

        error_row = MagicMock()
        error_row.id = uuid.uuid4()
        error_row.error_class = "AttributeError"
        error_row.message = "NoneType object has no attribute get"
        error_row.stack_trace = "trace"
        error_row.file_path = "app/main.py"
        error_row.line_number = 10
        error_row.service = "auth-service"
        error_row.severity = "high"
        error_row.commit_sha = "abc123"
        error_row.branch = "main"
        error_row.trace_id = None
        pg.get_error.return_value = error_row
        pg.save_fix.return_value = "fix-001"

        orch = object.__new__(Orchestrator)
        orch._pg = pg
        orch._redis = redis
        orch._llm = llm
        orch._settings = MagicMock(auto_merge_confidence_threshold=0.8)
        orch._memory = AsyncMock(find_template=AsyncMock(return_value=None), record_success=AsyncMock())
        orch._builder = AsyncMock(build=AsyncMock(return_value=MagicMock(summary=lambda: "summary")))
        orch._debugger = AsyncMock(analyze=AsyncMock(return_value=make_root_cause(0.9)))
        orch._fixer = AsyncMock(generate=AsyncMock(return_value=make_fix()))
        orch._blast = AsyncMock(analyze=AsyncMock(return_value=MagicMock(risk_level="low", downstream_count=0)))
        
        validation = ValidationReport(
            fix_id="f1",
            passed=True,
            sandbox_passed=True,
            sandbox_iterations=2,
            blocking_issues=[],
        )
        orch._validator = AsyncMock(validate=AsyncMock(return_value=validation))
        
        review = ReviewResult(
            quality_score=0.95,
            correctness_passes=True,
            security_passes=True,
            overall_recommendation="approve",
            rejection_reason=None,
            reviewer_notes="",
            issues=[],
        )
        orch._review_with_security_retry = AsyncMock(return_value=(make_fix(), review, 0))
        orch._slack = AsyncMock(notify_fix_ready=AsyncMock(return_value=True))
        orch._github = AsyncMock(create_pr=AsyncMock(return_value={"pr_url": "http://pr", "pr_number": 1}))
        orch._set_state = AsyncMock()
        orch._resolve_github_repo = MagicMock(return_value="owner/repo")

        result = await orch.run(str(error_row.id))

        assert result.status == "fixed"
        assert result.sandbox_iterations == 2
        assert result.security_retries == 0
        pg.record_pipeline_run_completion.assert_awaited_once_with(
            error_id=str(error_row.id),
            status="fixed",
            stage_reached="fixed",
            stages_completed=result.stages_completed,
            sandbox_iterations=2,
            security_retries=0,
            escalation_trigger=None,
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_metrics_persisted_on_sandbox_failure(self, mock_deps):
        pg, redis, llm = mock_deps
        from agents.orchestrator import Orchestrator

        error_row = MagicMock()
        error_row.id = uuid.uuid4()
        error_row.error_class = "RuntimeError"
        error_row.message = "failed assertion"
        error_row.stack_trace = "trace"
        error_row.file_path = "app/main.py"
        error_row.line_number = 20
        error_row.service = "api"
        error_row.severity = "medium"
        error_row.commit_sha = "abc123"
        error_row.branch = "main"
        error_row.trace_id = None
        pg.get_error.return_value = error_row
        pg.save_fix.return_value = "fix-002"

        orch = object.__new__(Orchestrator)
        orch._pg = pg
        orch._redis = redis
        orch._llm = llm
        orch._settings = MagicMock(auto_merge_confidence_threshold=0.8)
        orch._memory = AsyncMock(find_template=AsyncMock(return_value=None))
        orch._builder = AsyncMock(build=AsyncMock(return_value=MagicMock(summary=lambda: "summary")))
        orch._debugger = AsyncMock(analyze=AsyncMock(return_value=make_root_cause(0.8)))
        orch._fixer = AsyncMock(generate=AsyncMock(return_value=make_fix()))
        orch._blast = AsyncMock(analyze=AsyncMock(return_value=MagicMock(risk_level="low", downstream_count=0)))
        
        validation = ValidationReport(
            fix_id="f2",
            passed=False,
            sandbox_passed=False,
            sandbox_iterations=3,
            blocking_issues=["Test failed after 3 iterations"],
        )
        orch._validator = AsyncMock(validate=AsyncMock(return_value=validation))
        
        review = ReviewResult(
            quality_score=0.4,
            correctness_passes=False,
            security_passes=True,
            overall_recommendation="reject",
            rejection_reason="Validation failed",
            reviewer_notes="",
            issues=["Validation failed"],
        )
        orch._review_with_security_retry = AsyncMock(return_value=(make_fix(), review, 0))
        orch._slack = AsyncMock(notify_fix_ready=AsyncMock(return_value=True))
        orch._set_state = AsyncMock()

        result = await orch.run(str(error_row.id))

        assert result.status == "fixed"
        assert result.sandbox_iterations == 3
        pg.record_pipeline_run_completion.assert_awaited_once_with(
            error_id=str(error_row.id),
            status="fixed",
            stage_reached="fixed",
            stages_completed=result.stages_completed,
            sandbox_iterations=3,
            security_retries=0,
            escalation_trigger="sandbox_failure",
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_metrics_persisted_on_confidence_escalation(self, mock_deps):
        pg, redis, llm = mock_deps
        from agents.orchestrator import Orchestrator

        error_row = MagicMock()
        error_row.id = uuid.uuid4()
        error_row.error_class = "UnknownBug"
        error_row.message = "obscure error"
        error_row.stack_trace = "trace"
        error_row.file_path = "app/main.py"
        error_row.line_number = 5
        error_row.service = "api"
        error_row.severity = "critical"
        error_row.commit_sha = "abc123"
        error_row.branch = "main"
        error_row.trace_id = None
        pg.get_error.return_value = error_row

        orch = object.__new__(Orchestrator)
        orch._pg = pg
        orch._redis = redis
        orch._llm = llm
        orch._settings = MagicMock()
        orch._memory = AsyncMock(find_template=AsyncMock(return_value=None))
        orch._builder = AsyncMock(build=AsyncMock(return_value=MagicMock(summary=lambda: "summary")))
        # Low confidence -> triggers confidence_gate escalation
        orch._debugger = AsyncMock(analyze=AsyncMock(return_value=make_root_cause(confidence=0.20)))
        orch._slack = AsyncMock(notify_escalation=AsyncMock(return_value=True))
        orch._set_state = AsyncMock()

        result = await orch.run(str(error_row.id))

        assert result.status == "escalated"
        assert result.escalation_trigger == "confidence_gate"
        pg.record_pipeline_run_completion.assert_awaited_once_with(
            error_id=str(error_row.id),
            status="escalated",
            stage_reached="escalated",
            stages_completed=result.stages_completed,
            sandbox_iterations=0,
            security_retries=0,
            escalation_trigger="confidence_gate",
            error_message=None,
        )

    @pytest.mark.asyncio
    async def test_metrics_persisted_on_security_blocked_escalation(self, mock_deps):
        pg, redis, llm = mock_deps
        from agents.orchestrator import MAX_SECURITY_RETRIES, Orchestrator
        from validation.security_auditor import SecurityAuditResult, SecurityFinding

        error_row = MagicMock()
        error_row.id = uuid.uuid4()
        error_row.error_class = "SecurityRisk"
        error_row.message = "unsafe input"
        error_row.stack_trace = "trace"
        error_row.file_path = "app/main.py"
        error_row.line_number = 12
        error_row.service = "auth"
        error_row.severity = "high"
        error_row.commit_sha = "abc123"
        error_row.branch = "main"
        error_row.trace_id = None
        pg.get_error.return_value = error_row

        orch = object.__new__(Orchestrator)
        orch._pg = pg
        orch._redis = redis
        orch._llm = llm
        orch._settings = MagicMock()
        orch._memory = AsyncMock(find_template=AsyncMock(return_value=None))
        orch._builder = AsyncMock(build=AsyncMock(return_value=MagicMock(summary=lambda: "summary")))
        orch._debugger = AsyncMock(analyze=AsyncMock(return_value=make_root_cause(confidence=0.85)))
        orch._fixer = AsyncMock(generate=AsyncMock(return_value=make_fix()))
        orch._blast = AsyncMock(analyze=AsyncMock(return_value=MagicMock(risk_level="low", downstream_count=0)))
        orch._validator = AsyncMock(validate=AsyncMock(return_value=ValidationReport(fix_id="f3", passed=True, sandbox_passed=True)))
        
        security_audit = SecurityAuditResult(
            rejected=True,
            has_high_severity=True,
            findings=[SecurityFinding("bandit", "B102", "HIGH", "exec used", "app.py", 1)],
            tools_run=["bandit"],
            rejection_reason="Exec statement detected",
        )
        rejected_review = ReviewResult(
            quality_score=0.0,
            correctness_passes=False,
            security_passes=False,
            overall_recommendation="reject",
            rejection_reason="Security audit failed",
            reviewer_notes="",
            issues=["[HIGH] Exec used"],
            security_audit=security_audit,
        )
        orch._review_with_security_retry = AsyncMock(return_value=(make_fix(), rejected_review, MAX_SECURITY_RETRIES))
        orch._slack = AsyncMock(notify_escalation=AsyncMock(return_value=True))
        orch._set_state = AsyncMock()

        result = await orch.run(str(error_row.id))

        assert result.status == "security_blocked"
        assert result.escalation_trigger == "security_blocked"
        assert result.security_retries == MAX_SECURITY_RETRIES
        pg.record_pipeline_run_completion.assert_awaited_once_with(
            error_id=str(error_row.id),
            status="security_blocked",
            stage_reached="security_blocked",
            stages_completed=result.stages_completed,
            sandbox_iterations=0,
            security_retries=MAX_SECURITY_RETRIES,
            escalation_trigger="security_blocked",
            error_message=None,
        )


class TestPostgresClientReliabilityMetrics:
    """Unit tests for PostgresClient reliability metrics persistence methods."""

    @pytest.mark.asyncio
    async def test_record_pipeline_run_completion_creates_and_updates(self):
        from storage.models import PipelineRun
        from storage.postgres import PostgresClient

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session.flush = AsyncMock()

        client = object.__new__(PostgresClient)
        client.session = MagicMock(return_value=mock_session)

        # 1. Existing run updated
        existing_run = PipelineRun(
            id=uuid.uuid4(),
            error_id=uuid.uuid4(),
            status="started",
            stages_completed=[],
        )
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: existing_run)
        )

        run_id = await client.record_pipeline_run_completion(
            error_id=str(existing_run.error_id),
            status="fixed",
            stage_reached="fixed",
            stages_completed=["loaded", "analyzing", "fixed"],
            sandbox_iterations=2,
            security_retries=1,
            escalation_trigger=None,
        )

        assert run_id == str(existing_run.id)
        assert existing_run.status == "fixed"
        assert existing_run.sandbox_iterations == 2
        assert existing_run.security_retries == 1
        assert existing_run.escalation_trigger is None
        assert existing_run.completed_at is not None

        # 2. No existing run -> creates new PipelineRun record
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: None)
        )
        new_err_id = str(uuid.uuid4())
        new_run_id = await client.record_pipeline_run_completion(
            error_id=new_err_id,
            status="escalated",
            stage_reached="escalated",
            stages_completed=["loaded", "analyzing"],
            sandbox_iterations=0,
            security_retries=0,
            escalation_trigger="confidence_gate",
        )
        assert new_run_id is not None
        assert mock_session.add.call_count == 1
        added_run = mock_session.add.call_args[0][0]
        assert str(added_run.error_id) == new_err_id
        assert added_run.escalation_trigger == "confidence_gate"

