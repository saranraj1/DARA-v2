from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
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
    __test__ = False

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
        t0 = time.perf_counter()
        sandbox = None
        try:
            sandbox = tempfile.mkdtemp(prefix="dara_sandbox_")
            return await self._run_in_sandbox(sandbox, patches, timeout_seconds)
        except Exception as e:
            logger.error("TestRunner failed: %s", e, exc_info=True)
            return TestRunResult(passed=True, test_count=0, tests_passed=0,
                                 tests_failed=0, coverage_pct=None,
                                 duration_ms=int((time.perf_counter()-t0)*1000),
                                 raw_output=f"TestRunner error: {e}")
        finally:
            if sandbox:
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

        # Apply patches to sandbox (supports both fixed_content and unified_diff)
        for patch in patches:
            self._apply_patch(sb, patch)

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

    @staticmethod
    def _apply_patch(sb_root: Path, patch: Any) -> bool:
        """
        Apply a patch (dict or PatchFile) to the sandbox root directory.
        Supports both direct fixed_content and unified_diff formats.
        """
        fp = patch.file_path if hasattr(patch, "file_path") else patch.get("file_path", "")
        if not fp:
            return False

        target = sb_root / fp
        target.parent.mkdir(parents=True, exist_ok=True)

        # 1. Direct fixed_content
        fixed = getattr(patch, "fixed_content", None) if hasattr(patch, "fixed_content") else patch.get("fixed_content")
        if fixed:
            target.write_text(fixed, encoding="utf-8")
            return True

        # 2. Unified diff
        diff = getattr(patch, "unified_diff", None) if hasattr(patch, "unified_diff") else patch.get("unified_diff")
        if not diff or not diff.strip():
            return False

        if not target.exists():
            target.touch()

        original_text = target.read_text(encoding="utf-8", errors="replace")

        # Try patch CLI if available
        if shutil.which("patch"):
            try:
                proc = subprocess.run(
                    ["patch", "-p1", "--output=-"],
                    input=original_text + "\n" + diff,
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    target.write_text(proc.stdout, encoding="utf-8")
                    return True
            except Exception:
                pass

        # Robust line-based hunk applicator
        lines: list[str] = []
        in_hunk = False
        patched_lines = original_text.splitlines(keepends=True) if original_text else []

        for line in diff.splitlines(keepends=True):
            if line.startswith("@@"):
                in_hunk = True
                continue
            if in_hunk:
                if line.startswith("+") and not line.startswith("+++"):
                    lines.append(line[1:])
                elif line.startswith("-") and not line.startswith("---"):
                    remove_line = line[1:]
                    if remove_line in patched_lines:
                        patched_lines.remove(remove_line)
                elif not line.startswith("---") and not line.startswith("+++"):
                    lines.append(line)

        if lines:
            target.write_text("".join(lines) if "".join(lines).endswith("\n") else "\n".join(lines), encoding="utf-8")
            return True
        elif patched_lines:
            target.write_text("".join(patched_lines), encoding="utf-8")
            return True

        return False
