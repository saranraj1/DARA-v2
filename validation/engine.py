from __future__ import annotations
import logging, time
from dataclasses import dataclass, field
from api.models.agent_schemas import Fix
from validation.static_analyzer import StaticAnalyzer, StaticAnalysisResult
from validation.test_runner import TestRunner, TestRunResult

logger = logging.getLogger(__name__)


@dataclass
class ValidationReport:
    fix_id: str
    passed: bool
    static_analysis: StaticAnalysisResult | None = None
    test_results: TestRunResult | None = None
    total_duration_ms: int = 0
    blocking_issues: list[str] = field(default_factory=list)

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
        }


class ValidationEngine:
    """
    Orchestrates all validation steps for a generated fix:
      1. Static analysis (ruff security + lint)
      2. Test runner (pytest in isolated sandbox)

    Blocking rules:
      - Any security finding (S* codes) -> BLOCK
      - Any test failure -> BLOCK (unless no tests exist)
      - Critical lint errors (E9*) -> BLOCK

    Never crashes. Always returns a ValidationReport.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self._static = StaticAnalyzer()
        self._tests = TestRunner(repo_path=repo_path)

    async def validate(self, fix: Fix, fix_id: str) -> ValidationReport:
        t0 = time.perf_counter()
        report = ValidationReport(fix_id=fix_id, passed=True)

        if not fix.patches:
            logger.info("ValidationEngine: no patches, skipping validation")
            return report

        # Step 1: Static analysis on each patched file
        for patch in fix.patches:
            if not patch.unified_diff:
                continue
            # Re-derive patched content from diff context lines
            patched_content = self._extract_patched_content(patch.unified_diff)
            if not patched_content:
                continue
            sa = await self._static.analyze(patch.file_path, patched_content)
            report.static_analysis = sa
            if not sa.passed:
                blocking = [f for f in sa.findings if f["code"].startswith(("S","E9"))]
                if blocking:
                    report.blocking_issues.extend(
                        [f"[{f['code']}] {f['message']} (line {f['line']})" for f in blocking])
                    logger.warning("ValidationEngine: static analysis blocked: %s", blocking[:2])

        # Step 2: Test runner
        patch_dicts = [{"file_path": p.file_path, "unified_diff": p.unified_diff}
                       for p in fix.patches]
        tr = await self._tests.run(patch_dicts, timeout_seconds=60)
        report.test_results = tr
        if not tr.passed and tr.test_count > 0:
            report.blocking_issues.extend(
                [f"Test failed: {f['nodeid']}" for f in tr.failures[:3]])

        report.passed = len(report.blocking_issues) == 0
        report.total_duration_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "ValidationEngine: fix=%s passed=%s issues=%d duration=%dms",
            fix_id, report.passed, len(report.blocking_issues), report.total_duration_ms,
        )
        return report

    def _extract_patched_content(self, unified_diff: str) -> str:
        """
        Reconstruct the patched file content from a unified diff.
        Takes only '+' lines and context lines, skips '-' lines.
        This is a fast approximation for static analysis (not a full patch applier).
        """
        lines = []
        for line in unified_diff.splitlines():
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-"):
                continue  # removed line, skip
            if line.startswith("+"):
                lines.append(line[1:])  # added line
            else:
                lines.append(line)  # context line
        return "\n".join(lines)
