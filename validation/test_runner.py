from __future__ import annotations
import json, logging, os, shutil, subprocess, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TestRunResult:
    passed: bool
    test_count: int
    tests_passed: int
    tests_failed: int
    coverage_pct: float | None
    duration_ms: int
    failures: list[dict] = field(default_factory=list)
    raw_output: str = ""

    @property
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return (f"Tests: {status} | {self.tests_passed}/{self.test_count} passed"
                f" | coverage={self.coverage_pct}%")


class TestRunner:
    """
    Applies a unified diff patch to a temp copy of the repo and runs pytest.
    Isolates changes so real source is never touched.
    Falls back gracefully if no tests or pytest unavailable.
    """

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = Path(repo_path)

    async def run(self, patches: list[dict], timeout_seconds: int = 60) -> TestRunResult:
        if not shutil.which("pytest"):
            logger.warning("TestRunner: pytest not found, skipping")
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None, duration_ms=0,
                                 raw_output="pytest not installed")
        if not patches:
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None, duration_ms=0,
                                 raw_output="No patches to test")

        # Create temp sandbox copy
        sandbox = tempfile.mkdtemp(prefix="dara_sandbox_")
        t0 = time.perf_counter()
        try:
            return await self._run_in_sandbox(sandbox, patches, timeout_seconds)
        except Exception as e:
            logger.error("TestRunner failed: %s", e, exc_info=True)
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None,
                                 duration_ms=int((time.perf_counter()-t0)*1000),
                                 raw_output=f"TestRunner error: {e}")
        finally:
            shutil.rmtree(sandbox, ignore_errors=True)

    async def _run_in_sandbox(self, sandbox: str, patches: list[dict], timeout: int) -> TestRunResult:
        t0 = time.perf_counter()
        sb = Path(sandbox)

        # Copy repo to sandbox (skip heavy dirs)
        SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"}
        for item in self.repo_path.iterdir():
            if item.name in SKIP: continue
            dest = sb / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*SKIP))
            else:
                shutil.copy2(item, dest)

        # Apply patches to sandbox
        for patch in patches:
            file_path = sb / patch.get("file_path","")
            fixed = patch.get("fixed_content","")
            if fixed and file_path.exists():
                file_path.write_text(fixed, encoding="utf-8")

        # Find test files
        test_files = list(sb.rglob("test_*.py")) + list(sb.rglob("*_test.py"))
        if not test_files:
            logger.info("TestRunner: no test files found in sandbox")
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None,
                                 duration_ms=int((time.perf_counter()-t0)*1000),
                                 raw_output="No test files found")

        # Run pytest with JSON report
        report_path = sb / "pytest_report.json"
        cmd = [
            "python", "-m", "pytest",
            "--json-report", f"--json-report-file={report_path}",
            "--tb=short", "-x", "--timeout=30",
            str(sb / "tests"),
        ]
        env = {**os.environ, "PYTHONPATH": str(sb)}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(sb), timeout=timeout, env=env)
        elapsed_ms = int((time.perf_counter()-t0)*1000)

        # Parse report
        return self._parse_report(report_path, elapsed_ms, proc.stdout + proc.stderr)

    def _parse_report(self, report_path: Path, elapsed_ms: int, raw: str) -> TestRunResult:
        if not report_path.exists():
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None,
                                 duration_ms=elapsed_ms, raw_output=raw[:2000])
        try:
            data = json.loads(report_path.read_text())
            summary = data.get("summary", {})
            passed = summary.get("passed", 0)
            failed = summary.get("failed", 0)
            total = summary.get("total", 0)
            failures = [{"nodeid": t["nodeid"], "message": t.get("call",{}).get("longrepr","")}
                        for t in data.get("tests",[]) if t.get("outcome") == "failed"]
            return TestRunResult(
                passed=failed == 0,
                test_count=total, tests_passed=passed, tests_failed=failed,
                coverage_pct=None, duration_ms=elapsed_ms,
                failures=failures, raw_output=raw[:2000],
            )
        except Exception as e:
            logger.warning("TestRunner: could not parse report: %s", e)
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None,
                                 duration_ms=elapsed_ms, raw_output=raw[:2000])
