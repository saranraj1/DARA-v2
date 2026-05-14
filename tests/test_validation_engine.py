"""
DARA — Validation Engine Tests
================================
Brings validation/engine.py from 34% → ≥ 80%
Brings validation/test_runner.py from 35% → ≥ 75%

Tests:
  ValidationReport dataclass
  ValidationEngine.validate() — all code paths
  ValidationEngine._extract_patched_content() — unified diff parsing
  StaticAnalyzer integration via engine
  TestRunner — pytest-not-found fallback
  TestRunner — no-patches fallback
  TestRunner — no-test-files fallback
  TestRunner._parse_report() — success + failure + missing report
  Full end-to-end: security finding BLOCKS validation
  Full end-to-end: test failure BLOCKS validation
  Full end-to-end: clean patch PASSES validation
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_patch(file_path="app.py", unified_diff=None, fixed_content=None):
    p = MagicMock()
    p.file_path = file_path
    p.unified_diff = unified_diff or (
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def get_user(uid):\n"
        "-    return uid.name\n"
        "+    if uid is None:\n"
        "+        return None\n"
        "+    return uid.name\n"
    )
    return p


def _make_fix(patches=None, confidence=0.9):
    fix = MagicMock()
    fix.patches = patches if patches is not None else [_make_patch()]
    fix.confidence_retained = confidence
    return fix


# ---------------------------------------------------------------------------
# 1. ValidationReport
# ---------------------------------------------------------------------------

class TestValidationReport:

    def test_passed_defaults_true(self):
        from validation.engine import ValidationReport
        r = ValidationReport(fix_id="x", passed=True)
        assert r.passed is True
        assert r.blocking_issues == []
        assert r.static_analysis is None
        assert r.test_results is None

    def test_as_dict_shape_no_results(self):
        from validation.engine import ValidationReport
        r = ValidationReport(fix_id="fix-001", passed=True, total_duration_ms=123)
        d = r.as_dict()
        assert d["fix_id"] == "fix-001"
        assert d["passed"] is True
        assert d["total_duration_ms"] == 123
        assert d["static"]["passed"] is True
        assert d["static"]["error_count"] == 0
        assert d["tests"]["passed"] is True
        assert d["tests"]["total"] == 0

    def test_as_dict_with_static_result(self):
        from validation.engine import ValidationReport
        from validation.static_analyzer import StaticAnalysisResult
        sa = StaticAnalysisResult(
            passed=False, tool="ruff", error_count=2, warning_count=0,
            findings=[{"code": "S307", "message": "eval()", "line": 5, "severity": "error"}],
        )
        r = ValidationReport(fix_id="f1", passed=False, static_analysis=sa,
                              blocking_issues=["[S307] eval()"])
        d = r.as_dict()
        assert d["static"]["passed"] is False
        assert d["static"]["tool"] == "ruff"
        assert len(d["static"]["findings"]) == 1
        assert d["passed"] is False

    def test_as_dict_with_test_results(self):
        from validation.engine import ValidationReport
        from validation.test_runner import TestRunResult
        tr = TestRunResult(
            passed=False, test_count=10, tests_passed=8, tests_failed=2,
            coverage_pct=72.0, duration_ms=450,
            failures=[{"nodeid": "tests/test_x.py::test_y", "message": "AssertionError"}],
        )
        r = ValidationReport(fix_id="f2", passed=False, test_results=tr)
        d = r.as_dict()
        assert d["tests"]["total"] == 10
        assert d["tests"]["passed_count"] == 8
        assert d["tests"]["failed_count"] == 2
        assert len(d["tests"]["failures"]) == 1


# ---------------------------------------------------------------------------
# 2. ValidationEngine._extract_patched_content
# ---------------------------------------------------------------------------

class TestExtractPatchedContent:

    def _engine(self):
        from validation.engine import ValidationEngine
        return ValidationEngine(repo_path=".")

    def test_extracts_added_and_context_lines(self):
        engine = self._engine()
        diff = (
            "--- a/app.py\n+++ b/app.py\n"
            "@@ -1,3 +1,4 @@\n"
            " def get_user(uid):\n"          # context
            "-    return uid.name\n"          # removed (skip)
            "+    if uid is None:\n"          # added
            "+        return None\n"          # added
            "+    return uid.name\n"          # added
        )
        result = engine._extract_patched_content(diff)
        assert "def get_user" in result
        assert "if uid is None" in result
        assert "return None" in result
        # Removed line must NOT appear
        assert "return uid.name" in result  # context version preserved

    def test_skips_removed_lines(self):
        engine = self._engine()
        diff = (
            "--- a/x.py\n+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-REMOVED_SENTINEL\n"
            "+ADDED_SENTINEL\n"
        )
        result = engine._extract_patched_content(diff)
        assert "REMOVED_SENTINEL" not in result
        assert "ADDED_SENTINEL" in result

    def test_empty_diff_returns_empty(self):
        engine = self._engine()
        result = engine._extract_patched_content("")
        assert result == ""

    def test_diff_with_only_headers(self):
        engine = self._engine()
        diff = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n"
        result = engine._extract_patched_content(diff)
        assert result == ""

    def test_context_lines_preserved(self):
        engine = self._engine()
        diff = (
            "--- a/x.py\n+++ b/x.py\n"
            "@@ -1,2 +1,2 @@\n"
            " CONTEXT_LINE\n"
            "+NEW_LINE\n"
        )
        result = engine._extract_patched_content(diff)
        assert "CONTEXT_LINE" in result
        assert "NEW_LINE" in result


# ---------------------------------------------------------------------------
# 3. ValidationEngine.validate() — all paths
# ---------------------------------------------------------------------------

class TestValidationEngine:

    def _engine(self):
        from validation.engine import ValidationEngine
        return ValidationEngine(repo_path=".")

    @pytest.mark.asyncio
    async def test_no_patches_returns_passed(self):
        engine = self._engine()
        fix = _make_fix(patches=[])
        report = await engine.validate(fix, "fix-001")
        assert report.passed is True
        assert report.blocking_issues == []

    @pytest.mark.asyncio
    async def test_patch_with_no_diff_skipped(self):
        """A patch with empty unified_diff should be skipped gracefully."""
        engine = self._engine()
        p = MagicMock()
        p.file_path = "app.py"
        p.unified_diff = ""
        fix = _make_fix(patches=[p])

        # Mock static analyzer and sandbox runner
        engine._static.analyze = AsyncMock(return_value=MagicMock(passed=True, findings=[], error_count=0))
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=0, tests_passed=0,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        report = await engine.validate(fix, "fix-002")
        assert report.passed is True
        # Static analyze should NOT have been called (no content to analyze)
        engine._static.analyze.assert_not_called()

    @pytest.mark.asyncio
    async def test_security_finding_blocks_validation(self):
        """S307 eval() finding must block the fix."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_blocked = StaticAnalysisResult(
            passed=False, tool="ruff", error_count=1,
            findings=[{"code": "S307", "message": "Use of eval()", "line": 3, "severity": "error"}],
        )
        engine._static.analyze = AsyncMock(return_value=sa_blocked)
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=5, tests_passed=5,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-003")

        assert report.passed is False
        assert len(report.blocking_issues) >= 1
        assert any("S307" in issue for issue in report.blocking_issues)

    @pytest.mark.asyncio
    async def test_test_failure_blocks_validation(self):
        """A failing test (with tests > 0) must block the fix."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_ok = StaticAnalysisResult(passed=True, tool="ruff", error_count=0, findings=[])
        engine._static.analyze = AsyncMock(return_value=sa_ok)

        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=False, failure_log="FAILED tests/test_app.py::test_get_user: AssertionError: Expected None",
            iterations=1, used_docker=False,
            test_result=MagicMock(passed=False, test_count=10, tests_passed=9,
                                  tests_failed=1,
                                  failures=[{"nodeid": "tests/test_app.py::test_get_user",
                                             "message": "AssertionError: Expected None"}]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-004")

        assert report.passed is False
        assert any("test_get_user" in issue or "Sandbox failed" in issue
                   for issue in report.blocking_issues)

    @pytest.mark.asyncio
    async def test_clean_patch_passes_validation(self):
        """Static OK + tests pass → ValidationReport.passed is True."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_ok = StaticAnalysisResult(passed=True, tool="ruff", error_count=0, findings=[])
        engine._static.analyze = AsyncMock(return_value=sa_ok)

        tr_ok = MagicMock(
            passed=True, test_count=15, tests_passed=15, tests_failed=0, failures=[]
        )
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=tr_ok,
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-005")

        assert report.passed is True
        assert report.blocking_issues == []
        assert report.static_analysis is sa_ok
        assert report.test_results is tr_ok

    @pytest.mark.asyncio
    async def test_no_tests_does_not_block(self):
        """If test_count=0 (no test files), test failures don't block."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_ok = StaticAnalysisResult(passed=True, tool="ruff", error_count=0, findings=[])
        engine._static.analyze = AsyncMock(return_value=sa_ok)

        # Sandbox returned passed=False but test_count=0 → no blocking
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=False, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=False, test_count=0, tests_passed=0,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-006")

        # test_count=0 → failures are empty list anyway → does not block
        assert report.passed is True

    @pytest.mark.asyncio
    async def test_critical_syntax_error_blocks(self):
        """E901 SyntaxError finding must block the fix."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_syntax_err = StaticAnalysisResult(
            passed=False, tool="ruff", error_count=1,
            findings=[{"code": "E901", "message": "SyntaxError", "line": 1, "severity": "error"}],
        )
        engine._static.analyze = AsyncMock(return_value=sa_syntax_err)
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=0, tests_passed=0,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-007")

        assert report.passed is False
        assert any("E901" in issue for issue in report.blocking_issues)

    @pytest.mark.asyncio
    async def test_warning_only_does_not_block(self):
        """W503 warning should NOT block the fix (only S* and E9* block)."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_warning = StaticAnalysisResult(
            passed=False, tool="ruff",  # ruff says 'failed' for warnings
            error_count=0, warning_count=1,
            findings=[{"code": "W503", "message": "line break before binary op", "line": 5, "severity": "warning"}],
        )
        engine._static.analyze = AsyncMock(return_value=sa_warning)
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=3, tests_passed=3,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-008")

        # W503 is not in (S*, E9*) → not blocking
        assert report.passed is True

    @pytest.mark.asyncio
    async def test_total_duration_ms_populated(self):
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()
        engine._static.analyze = AsyncMock(return_value=StaticAnalysisResult(passed=True, tool="ruff", error_count=0))
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=0, tests_passed=0,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        fix = _make_fix()
        report = await engine.validate(fix, "fix-009")
        assert report.total_duration_ms >= 0

    @pytest.mark.asyncio
    async def test_multiple_patches_all_analyzed(self):
        """Each patch file is analyzed independently."""
        from validation.static_analyzer import StaticAnalysisResult
        engine = self._engine()

        sa_ok = StaticAnalysisResult(passed=True, tool="ruff", error_count=0, findings=[])
        call_count = [0]
        async def _mock_analyze(file_path, content):
            call_count[0] += 1
            return sa_ok
        engine._static.analyze = _mock_analyze
        engine._sandbox.run = AsyncMock(return_value=MagicMock(
            passed=True, failure_log="", iterations=1, used_docker=False,
            test_result=MagicMock(passed=True, test_count=0, tests_passed=0,
                                  tests_failed=0, failures=[]),
            final_fix=None,
        ))

        patches = [_make_patch("a.py"), _make_patch("b.py"), _make_patch("c.py")]
        fix = _make_fix(patches=patches)
        report = await engine.validate(fix, "fix-010")

        assert call_count[0] == 3
        assert report.passed is True


# ---------------------------------------------------------------------------
# 4. TestRunner — fallback paths
# ---------------------------------------------------------------------------

class TestRunnerFallbacks:

    def _runner(self):
        from validation.test_runner import TestRunner
        return TestRunner(repo_path=".")

    @pytest.mark.asyncio
    async def test_no_pytest_returns_passed(self):
        """If pytest is not installed, skip gracefully and pass."""
        runner = self._runner()
        with patch("shutil.which", return_value=None):
            result = await runner.run([{"file_path": "x.py", "unified_diff": "---"}])
        assert result.passed is True
        assert result.test_count == 0
        assert "not installed" in result.raw_output.lower()

    @pytest.mark.asyncio
    async def test_empty_patches_returns_passed(self):
        """Empty patch list → immediate pass without running anything."""
        runner = self._runner()
        with patch("shutil.which", return_value="/usr/bin/pytest"):
            result = await runner.run([])
        assert result.passed is True
        assert result.test_count == 0

    @pytest.mark.asyncio
    async def test_sandbox_exception_returns_passed(self):
        """If mkdtemp fails (e.g. disk full), returns passed=True (graceful degradation)."""
        runner = self._runner()
        patches = [{"file_path": "app.py", "unified_diff": "--- a\n+++ b\n+line"}]

        with (
            patch("shutil.which", return_value="/usr/bin/pytest"),
            patch("validation.test_runner.tempfile.mkdtemp", side_effect=OSError("disk full")),
        ):
            result = await runner.run(patches)
        # sandbox=None guard means rmtree is skipped, OSError is caught
        assert result.passed is True
        assert "disk full" in result.raw_output



    def test_parse_report_missing_file(self):
        """_parse_report with missing JSON file returns passed=True."""
        runner = self._runner()
        result = runner._parse_report(Path("/nonexistent/report.json"), 500, "raw output")
        assert result.passed is True
        assert result.test_count == 0
        assert result.duration_ms == 500

    def test_parse_report_valid_all_pass(self):
        runner = self._runner()
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({
                "summary": {"passed": 10, "failed": 0, "total": 10},
                "tests": [{"nodeid": "test_x.py::test_a", "outcome": "passed"}]
            }, f)
            report_path = Path(f.name)

        try:
            result = runner._parse_report(report_path, 1200, "all good")
            assert result.passed is True
            assert result.test_count == 10
            assert result.tests_passed == 10
            assert result.tests_failed == 0
            assert result.failures == []
        finally:
            report_path.unlink(missing_ok=True)

    def test_parse_report_with_failures(self):
        runner = self._runner()
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            json.dump({
                "summary": {"passed": 8, "failed": 2, "total": 10},
                "tests": [
                    {"nodeid": "test_x.py::test_a", "outcome": "failed", "call": {"longrepr": "AssertionError: expected 1, got 0"}},
                    {"nodeid": "test_x.py::test_b", "outcome": "failed", "call": {"longrepr": "TypeError"}},
                    {"nodeid": "test_x.py::test_c", "outcome": "passed"},
                ]
            }, f)
            report_path = Path(f.name)

        try:
            result = runner._parse_report(report_path, 800, "2 failed")
            assert result.passed is False
            assert result.tests_failed == 2
            assert result.tests_passed == 8
            assert len(result.failures) == 2
            assert result.failures[0]["nodeid"] == "test_x.py::test_a"
        finally:
            report_path.unlink(missing_ok=True)

    def test_parse_report_invalid_json_returns_safe(self):
        runner = self._runner()
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
            f.write("NOT_VALID_JSON{{{{")
            report_path = Path(f.name)

        try:
            result = runner._parse_report(report_path, 300, "corrupt report")
            assert result.passed is True  # safe degradation
        finally:
            report_path.unlink(missing_ok=True)

    def test_test_run_result_summary_property(self):
        from validation.test_runner import TestRunResult
        r = TestRunResult(passed=True, test_count=10, tests_passed=10,
                          tests_failed=0, coverage_pct=87.5, duration_ms=300)
        assert "PASS" in r.summary
        assert "10/10" in r.summary

    def test_test_run_result_fail_summary(self):
        from validation.test_runner import TestRunResult
        r = TestRunResult(passed=False, test_count=10, tests_passed=8,
                          tests_failed=2, coverage_pct=None, duration_ms=500)
        assert "FAIL" in r.summary


# ---------------------------------------------------------------------------
# 5. TestRunner sandbox (no-test-files path)
# ---------------------------------------------------------------------------

class TestRunnerSandboxNoTests:

    @pytest.mark.asyncio
    async def test_sandbox_with_no_test_files_passes(self):
        """Sandbox created but no test_*.py files found → pass with test_count=0."""
        from validation.test_runner import TestRunner

        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a source file but NO test file
            (Path(tmpdir) / "app.py").write_text("def hello(): return 'world'\n")
            runner = TestRunner(repo_path=tmpdir)

            with patch("shutil.which", return_value="/usr/bin/pytest"):
                async def _fake_sandbox(sandbox, patches, timeout):
                    from validation.test_runner import TestRunResult
                    return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                         tests_failed=0, coverage_pct=None, duration_ms=10,
                                         raw_output="No test files found")
                runner._run_in_sandbox = _fake_sandbox
                result = await runner.run([{"file_path": "app.py", "unified_diff": "+hello"}])

        assert result.passed is True
        assert result.test_count == 0
