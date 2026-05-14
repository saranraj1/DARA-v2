"""
validation/engine.py
=====================
Orchestrates all validation steps for a generated fix.

Pipeline (in order):
  1. Static analysis   — Ruff security/lint on patched content  (fast, always)
  2. Sandbox loop      — Docker-isolated pytest run with internal
                         DebuggerAgent→FixerAgent iteration on failure
                         Falls back to file-system TestRunner when Docker
                         is unavailable.

Blocking rules:
  - Any security finding (S* codes) → BLOCK
  - Sandbox/test failure             → BLOCK  (unless no tests exist)
  - Critical lint errors (E9*)       → BLOCK

ValidationReport is enriched with sandbox metadata:
  sandbox_passed, sandbox_iterations, sandbox_failure_log, final_fix

Never crashes. Always returns a ValidationReport.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from api.models.agent_schemas import Fix
from validation.sandbox_runner import SandboxResult, SandboxRunner
from validation.static_analyzer import StaticAnalysisResult, StaticAnalyzer
from validation.test_runner import TestRunResult

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    fix_id: str
    passed: bool
    static_analysis: StaticAnalysisResult | None = None
    test_results: TestRunResult | None = None
    total_duration_ms: int = 0
    blocking_issues: list[str] = field(default_factory=list)

    # Sandbox metadata (new)
    sandbox_passed: bool = True
    sandbox_iterations: int = 0
    sandbox_failure_log: str = ""
    sandbox_used_docker: bool = False
    # The fix that ultimately passed sandbox (may be a refined version)
    final_fix: Fix | None = None

    def as_dict(self) -> dict:
        return {
            "fix_id": self.fix_id,
            "passed": self.passed,
            "blocking_issues": self.blocking_issues,
            "total_duration_ms": self.total_duration_ms,
            "static": {
                "passed": self.static_analysis.passed if self.static_analysis else True,
                "tool": self.static_analysis.tool if self.static_analysis else "none",
                "error_count": self.static_analysis.error_count if self.static_analysis else 0,
                "findings": (self.static_analysis.findings if self.static_analysis else [])[:5],
            },
            "tests": {
                "passed": self.test_results.passed if self.test_results else True,
                "total": self.test_results.test_count if self.test_results else 0,
                "passed_count": self.test_results.tests_passed if self.test_results else 0,
                "failed_count": self.test_results.tests_failed if self.test_results else 0,
                "failures": (self.test_results.failures if self.test_results else [])[:3],
            },
            "sandbox": {
                "passed": self.sandbox_passed,
                "iterations": self.sandbox_iterations,
                "used_docker": self.sandbox_used_docker,
                "failure_log": self.sandbox_failure_log[:500] if self.sandbox_failure_log else "",
            },
        }


class ValidationEngine:
    """
    Orchestrates all validation steps for a generated fix.

    Accepts optional debugger/fixer/bundle/error so the sandbox loop can
    iterate internally.  These are wired in by the Orchestrator after it
    instantiates all agents.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._static = StaticAnalyzer()
        self._sandbox = SandboxRunner(repo_path=repo_path)

        # Set by Orchestrator after construction (avoids circular imports)
        self._debugger = None
        self._fixer = None

    def wire_agents(self, debugger, fixer) -> None:
        """Called by Orchestrator to inject agents for the sandbox iteration loop."""
        self._debugger = debugger
        self._fixer = fixer

    async def validate(
        self,
        fix: Fix,
        fix_id: str,
        bundle=None,
        error: dict | None = None,
    ) -> ValidationReport:
        t0 = time.perf_counter()
        report = ValidationReport(fix_id=fix_id, passed=True)
        report.final_fix = fix

        if not fix.patches:
            logger.info("ValidationEngine: no patches, skipping validation")
            return report

        # ── Step 1: Static analysis on each patched file ────────────────────
        for patch in fix.patches:
            if not patch.unified_diff:
                continue
            patched_content = self._extract_patched_content(patch.unified_diff)
            if not patched_content:
                continue
            sa = await self._static.analyze(patch.file_path, patched_content)
            report.static_analysis = sa
            if not sa.passed:
                blocking = [f for f in sa.findings if f["code"].startswith(("S", "E9"))]
                if blocking:
                    report.blocking_issues.extend(
                        [f"[{f['code']}] {f['message']} (line {f['line']})" for f in blocking]
                    )
                    logger.warning(
                        "ValidationEngine: static analysis blocked: %s", blocking[:2]
                    )

        # ── Step 2: Sandbox loop ─────────────────────────────────────────────
        sandbox_result: SandboxResult = await self._sandbox.run(
            fix=fix,
            debugger=self._debugger,
            fixer=self._fixer,
            bundle=bundle,
            error=error,
        )

        report.sandbox_passed = sandbox_result.passed
        report.sandbox_iterations = sandbox_result.iterations
        report.sandbox_failure_log = sandbox_result.failure_log
        report.sandbox_used_docker = sandbox_result.used_docker

        # Use the refined fix if the sandbox iterated to a passing version
        if sandbox_result.final_fix is not None:
            report.final_fix = sandbox_result.final_fix

        # Populate test_results from sandbox for backwards-compatible reporting
        if sandbox_result.test_result:
            report.test_results = sandbox_result.test_result

        if not sandbox_result.passed:
            # Only block if there were actual tests to run
            has_tests = (
                sandbox_result.test_result is not None
                and sandbox_result.test_result.test_count > 0
            ) or sandbox_result.failure_log  # Docker run had output
            if has_tests:
                report.blocking_issues.append(
                    f"Sandbox failed after {sandbox_result.iterations} iteration(s): "
                    f"{sandbox_result.failure_log[:200]}"
                )
                logger.warning(
                    "ValidationEngine: sandbox BLOCKED fix=%s after %d iteration(s)",
                    fix_id, sandbox_result.iterations,
                )

        report.passed = len(report.blocking_issues) == 0
        report.total_duration_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "ValidationEngine: fix=%s passed=%s sandbox_passed=%s "
            "sandbox_iter=%d issues=%d duration=%dms",
            fix_id, report.passed, report.sandbox_passed,
            report.sandbox_iterations, len(report.blocking_issues),
            report.total_duration_ms,
        )
        return report

    @staticmethod
    def _extract_patched_content(unified_diff: str) -> str:
        """
        Reconstruct patched file content from a unified diff.
        Takes only '+' lines and context lines, skips '-' lines.
        """
        lines: list[str] = []
        for line in unified_diff.splitlines():
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-"):
                continue
            lines.append(line[1:] if line.startswith("+") else line)
        return "\n".join(lines)
