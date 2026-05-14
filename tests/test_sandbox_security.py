"""
tests/test_sandbox_security.py
===============================
Unit tests for:
  - SandboxRunner (Docker + fallback, iteration loop)
  - SecurityAuditor (Bandit, Semgrep, regex, severity mapping)
  - ReviewerAgent security gate (immediate rejection, LLM bypass)
  - Orchestrator security retry loop
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.models.agent_schemas import Fix, PatchFile, ReviewResult, RootCauseResult
from validation.sandbox_runner import SandboxResult, SandboxRunner
from validation.security_auditor import SecurityAuditor, SecurityAuditResult, SecurityFinding


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def make_patch(diff: str = "", file_path: str = "app/main.py") -> PatchFile:
    return PatchFile(
        file_path=file_path,
        unified_diff=diff or "+x = 1\n context\n",
        lines_changed=1,
        change_description="test patch",
    )


def make_fix(patches: list[PatchFile] | None = None) -> Fix:
    patches = patches or [make_patch()]
    return Fix(
        error_id="err-001",
        patches=patches,
        total_files_changed=len(patches),
        total_lines_changed=1,
        fix_explanation="Test fix",
        suggested_tests=[],
        confidence_retained=0.8,
        regression_risk="low",
        strategy="llm_single_file",
        llm_provider="groq",
    )


def make_root_cause() -> RootCauseResult:
    return RootCauseResult(
        immediate_cause="NullPointerException in handler",
        root_cause="Missing null check before attribute access",
        contributing_factors=[],
        confidence=0.85,
        evidence_quality="high",
        files_to_change=["app/main.py"],
        suggested_strategy="llm_single_file",
        reasoning_trace="trace",
    )


# ──────────────────────────────────────────────────────────────────────────────
# SandboxRunner — Docker path
# ──────────────────────────────────────────────────────────────────────────────

class TestSandboxRunnerDocker:

    @pytest.mark.asyncio
    async def test_passes_clean_fix(self):
        """Sandbox returns passed=True when Docker run exits 0."""
        fix = make_fix()
        runner = SandboxRunner(repo_path=".")

        # Mock docker availability and container execution
        with (
            patch.object(runner, "_docker_ok", new=AsyncMock(return_value=True)),
            patch.object(runner, "_run_in_docker", new=AsyncMock(
                return_value=SandboxResult(passed=True, failure_log="", iterations=1, used_docker=True)
            )),
        ):
            result = await runner.run(fix)

        assert result.passed is True
        assert result.used_docker is True
        assert result.iterations == 1

    @pytest.mark.asyncio
    async def test_iterates_on_failure_then_passes(self):
        """
        Sandbox fails on iteration 1, internal DebuggerAgent+FixerAgent cycle
        regenerates the fix, iteration 2 passes.
        """
        fix = make_fix()
        runner = SandboxRunner(repo_path=".")
        bundle = MagicMock()
        error = {"id": "err-001", "error_class": "ValueError", "message": "fail"}

        mock_debugger = AsyncMock()
        mock_debugger.analyze.return_value = make_root_cause()

        mock_fixer = AsyncMock()
        # Return a slightly different fix on retry
        refined_fix = make_fix([make_patch("+x = safe_value\n")])
        mock_fixer.generate.return_value = refined_fix

        call_count = 0

        async def docker_side_effect(f):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SandboxResult(passed=False, failure_log="FAILED test_foo", used_docker=True)
            return SandboxResult(passed=True, failure_log="", used_docker=True)

        with (
            patch.object(runner, "_docker_ok", new=AsyncMock(return_value=True)),
            patch.object(runner, "_run_in_docker", side_effect=docker_side_effect),
        ):
            result = await runner.run(
                fix, debugger=mock_debugger, fixer=mock_fixer,
                bundle=bundle, error=error,
            )

        assert result.passed is True
        assert result.iterations == 2
        assert mock_debugger.analyze.call_count == 1
        assert mock_fixer.generate.call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_iterations_and_returns_failure(self):
        """After MAX_ITERATIONS all fail, returns passed=False."""
        from validation.sandbox_runner import MAX_ITERATIONS
        fix = make_fix()
        runner = SandboxRunner(repo_path=".")
        mock_debugger = AsyncMock()
        mock_debugger.analyze.return_value = make_root_cause()
        mock_fixer = AsyncMock()
        mock_fixer.generate.return_value = fix

        with (
            patch.object(runner, "_docker_ok", new=AsyncMock(return_value=True)),
            patch.object(runner, "_run_in_docker", new=AsyncMock(
                return_value=SandboxResult(passed=False, failure_log="always fails", used_docker=True)
            )),
        ):
            result = await runner.run(
                fix, debugger=mock_debugger, fixer=mock_fixer,
                bundle=MagicMock(), error={"id": "x"},
            )

        assert result.passed is False
        assert result.iterations == MAX_ITERATIONS

    @pytest.mark.asyncio
    async def test_docker_unavailable_falls_back_to_file_runner(self):
        """When Docker is unavailable, falls back to file-system TestRunner."""
        fix = make_fix()
        runner = SandboxRunner(repo_path=".")

        from validation.test_runner import TestRunResult
        file_result = TestRunResult(
            passed=True, test_count=3, tests_passed=3,
            tests_failed=0, coverage_pct=None, duration_ms=120,
        )

        with (
            patch.object(runner, "_docker_ok", new=AsyncMock(return_value=False)),
            patch.object(runner._file_runner, "run", new=AsyncMock(return_value=file_result)),
        ):
            result = await runner.run(fix)

        assert result.passed is True
        assert result.used_docker is False


# ──────────────────────────────────────────────────────────────────────────────
# SecurityAuditor
# ──────────────────────────────────────────────────────────────────────────────

class TestSecurityAuditor:

    @pytest.mark.asyncio
    async def test_high_severity_eval_detected_by_regex(self):
        """eval() is always a HIGH finding via regex fallback."""
        auditor = SecurityAuditor()
        diff = "+result = eval(user_input)\n context\n"
        fix = make_fix([make_patch(diff)])

        # Force regex path (no bandit/semgrep)
        with (
            patch("shutil.which", return_value=None),
        ):
            result = await auditor.audit(fix)

        assert result.has_high_severity is True
        assert result.rejected is True
        high = result.high_findings
        assert any("eval" in f.message.lower() or f.rule_id == "R001" for f in high)

    @pytest.mark.asyncio
    async def test_shell_true_is_high_severity(self):
        """shell=True is a HIGH finding."""
        auditor = SecurityAuditor()
        diff = "+subprocess.run(cmd, shell=True)\n context\n"
        fix = make_fix([make_patch(diff)])

        with patch("shutil.which", return_value=None):
            result = await auditor.audit(fix)

        assert result.has_high_severity is True
        assert result.rejected is True

    @pytest.mark.asyncio
    async def test_clean_diff_passes_audit(self):
        """A safe diff produces no findings."""
        auditor = SecurityAuditor()
        diff = "+x = value + 1\n context line\n"
        fix = make_fix([make_patch(diff)])

        with patch("shutil.which", return_value=None):
            result = await auditor.audit(fix)

        assert result.has_high_severity is False
        assert result.rejected is False

    @pytest.mark.asyncio
    async def test_bandit_high_confidence_maps_to_high(self):
        """Bandit HIGH/HIGH severity→confidence maps to DARA HIGH."""
        auditor = SecurityAuditor()
        bandit_output = {
            "results": [{
                "test_id": "B307",
                "issue_text": "Use of possibly insecure function - consider using safer alternatives",
                "issue_severity": "HIGH",
                "issue_confidence": "HIGH",
                "line_number": 5,
            }],
            "metrics": {},
        }

        import json
        diff = "+eval(user_input)\n"
        fix = make_fix([make_patch(diff)])

        with (
            patch("shutil.which", side_effect=lambda x: "/usr/bin/bandit" if x == "bandit" else None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout=json.dumps(bandit_output),
                stderr="",
                returncode=1,
            )
            result = await auditor.audit(fix)

        assert result.has_high_severity is True
        assert result.rejected is True
        assert result.tools_run == ["bandit"]

    @pytest.mark.asyncio
    async def test_semgrep_error_severity_maps_to_high(self):
        """Semgrep ERROR severity maps to DARA HIGH."""
        auditor = SecurityAuditor()
        semgrep_output = {
            "results": [{
                "check_id": "python.lang.security.audit.eval-detected.eval-detected",
                "start": {"line": 3},
                "extra": {
                    "severity": "ERROR",
                    "message": "Detected the use of eval(). eval() can be dangerous ...",
                },
            }],
            "errors": [],
        }

        import json
        diff = "+eval(data)\n"
        fix = make_fix([make_patch(diff)])

        with (
            patch("shutil.which", side_effect=lambda x: "/usr/bin/semgrep" if x == "semgrep" else None),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                stdout=json.dumps(semgrep_output),
                stderr="",
                returncode=1,
            )
            result = await auditor.audit(fix)

        assert result.has_high_severity is True
        assert result.rejected is True
        assert "semgrep" in result.tools_run

    @pytest.mark.asyncio
    async def test_empty_patches_never_rejected(self):
        """Fix with no patches is always safe."""
        auditor = SecurityAuditor()
        fix = make_fix([])

        result = await auditor.audit(fix)

        assert result.rejected is False
        assert result.findings == []


# ──────────────────────────────────────────────────────────────────────────────
# ReviewerAgent security gate
# ──────────────────────────────────────────────────────────────────────────────

class TestReviewerSecurityGate:

    @pytest.mark.asyncio
    async def test_high_severity_bypasses_llm_and_rejects(self):
        """When SecurityAuditor finds HIGH, LLM is never called and rec=reject."""
        from agents.reviewer import ReviewerAgent

        mock_llm = AsyncMock()
        reviewer = ReviewerAgent(llm_router=mock_llm)

        high_audit = SecurityAuditResult(
            rejected=True,
            has_high_severity=True,
            findings=[SecurityFinding(
                tool="regex", rule_id="R001", severity="HIGH",
                message="eval() usage", file_path="app.py", line=5,
            )],
            tools_run=["regex"],
            rejection_reason="[HIGH][regex/R001] eval() usage (app.py:5)",
        )

        with patch.object(reviewer._auditor, "audit", new=AsyncMock(return_value=high_audit)):
            fix = make_fix([make_patch("+eval(user_input)\n")])
            result = await reviewer.review(fix, make_root_cause(), {"error_class": "ValueError"})

        # LLM must NOT have been called
        mock_llm.complete.assert_not_called()
        assert result.overall_recommendation == "reject"
        assert result.security_passes is False
        assert "HIGH" in (result.rejection_reason or "")

    @pytest.mark.asyncio
    async def test_clean_audit_proceeds_to_llm(self):
        """Clean security audit allows the LLM review to proceed."""
        from agents.reviewer import ReviewerAgent

        mock_llm = AsyncMock()
        mock_llm.complete.return_value = (
            '{"quality_score": 0.9, "overall_recommendation": "approve", '
            '"correctness_passes": true, "security_passes": true, '
            '"reviewer_notes": "Looks good", "issues": []}'
        )
        reviewer = ReviewerAgent(llm_router=mock_llm)

        clean_audit = SecurityAuditResult(
            rejected=False, has_high_severity=False,
            findings=[], tools_run=["regex"],
        )

        with patch.object(reviewer._auditor, "audit", new=AsyncMock(return_value=clean_audit)):
            fix = make_fix()
            result = await reviewer.review(fix, make_root_cause(), {"error_class": "ValueError"})

        mock_llm.complete.assert_called_once()
        assert result.overall_recommendation == "approve"
        assert result.security_passes is True


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator security retry loop
# ──────────────────────────────────────────────────────────────────────────────

class TestOrchestratorSecurityRetry:

    def _make_orchestrator(self, mock_fixer, mock_reviewer):
        """Build a minimal Orchestrator with mocked dependencies."""
        from agents.orchestrator import Orchestrator

        orch = object.__new__(Orchestrator)
        orch._pg = AsyncMock()
        orch._redis = AsyncMock()
        orch._llm = AsyncMock()
        orch._settings = MagicMock(auto_merge_confidence_threshold=0.85)
        orch._builder = AsyncMock()
        orch._debugger = AsyncMock()
        orch._fixer = mock_fixer
        orch._reviewer = mock_reviewer
        orch._memory = AsyncMock()
        orch._validator = AsyncMock()
        orch._slack = AsyncMock()
        orch._github = AsyncMock()
        return orch

    @pytest.mark.asyncio
    async def test_security_retry_calls_fixer_again(self):
        """On HIGH security rejection, FixerAgent.generate is called again."""
        from agents.orchestrator import MAX_SECURITY_RETRIES

        # First review: security rejection. Second review: approve.
        root_cause = make_root_cause()
        fix = make_fix()
        clean_fix = make_fix([make_patch("+x = safe_function(input)\n")])

        security_rejection = ReviewResult(
            quality_score=0.0, correctness_passes=False, security_passes=False,
            overall_recommendation="reject",
            rejection_reason="Security audit: HIGH severity",
            reviewer_notes="Bandit found eval()",
            issues=["[HIGH] eval() detected"],
            security_audit=SecurityAuditResult(
                rejected=True, has_high_severity=True,
                findings=[SecurityFinding("regex", "R001", "HIGH", "eval()", "app.py", 5)],
                tools_run=["regex"],
                rejection_reason="eval()",
            ),
        )

        approval = ReviewResult(
            quality_score=0.9, correctness_passes=True, security_passes=True,
            overall_recommendation="approve",
            rejection_reason=None,
            reviewer_notes="Looks good",
            issues=[],
            security_audit=SecurityAuditResult(
                rejected=False, has_high_severity=False, findings=[], tools_run=["regex"],
            ),
        )

        mock_fixer = AsyncMock()
        mock_fixer.generate.return_value = clean_fix

        mock_reviewer = AsyncMock()
        mock_reviewer.review.side_effect = [security_rejection, approval]

        orch = self._make_orchestrator(mock_fixer, mock_reviewer)

        final_fix, final_review, retries = await orch._review_with_security_retry(
            fix=fix, root_cause=root_cause,
            error={"id": "e1", "error_class": "ValueError", "message": "bad"},
            bundle=MagicMock(),
        )

        assert final_review.overall_recommendation == "approve"
        assert retries == 1
        assert mock_fixer.generate.call_count == 1
        # Check constraints were injected
        call_error = mock_fixer.generate.call_args[0][2]
        assert "SECURITY REWRITE REQUIRED" in call_error["message"]

    @pytest.mark.asyncio
    async def test_security_blocked_after_max_retries(self):
        """After MAX_SECURITY_RETRIES, returns the last rejection."""
        from agents.orchestrator import MAX_SECURITY_RETRIES

        root_cause = make_root_cause()
        fix = make_fix()

        def make_sec_rejection():
            return ReviewResult(
                quality_score=0.0, correctness_passes=False, security_passes=False,
                overall_recommendation="reject",
                rejection_reason="Security: eval() detected",
                reviewer_notes="blocked", issues=[],
                security_audit=SecurityAuditResult(
                    rejected=True, has_high_severity=True,
                    findings=[SecurityFinding("regex", "R001", "HIGH", "eval()", "app.py", 1)],
                    tools_run=["regex"], rejection_reason="eval()",
                ),
            )

        mock_fixer = AsyncMock()
        mock_fixer.generate.return_value = fix

        mock_reviewer = AsyncMock()
        mock_reviewer.review.side_effect = [make_sec_rejection() for _ in range(MAX_SECURITY_RETRIES + 1)]

        orch = self._make_orchestrator(mock_fixer, mock_reviewer)

        final_fix, final_review, retries = await orch._review_with_security_retry(
            fix=fix, root_cause=root_cause,
            error={"id": "e1", "error_class": "ValueError", "message": "bad"},
            bundle=MagicMock(),
        )

        assert final_review.overall_recommendation == "reject"
        assert retries == MAX_SECURITY_RETRIES
        assert mock_fixer.generate.call_count == MAX_SECURITY_RETRIES
