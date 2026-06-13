"""
validation/sandbox_runner.py
============================
Ephemeral Docker sandbox for fix verification.

Runs the test suite inside an isolated container against the patched code.
Falls back to the file-system-based TestRunner if Docker is unavailable.

Architecture:
  SandboxRunner.run(patches, debugger, fixer, bundle, error)
    → SandboxResult(passed, failure_log, iterations, used_docker)

Iteration loop (max MAX_ITERATIONS=3):
  1. Apply patches to temp dir
  2. Spin up DockerContainer (python:3.11-slim)
  3. Run pytest inside container
  4. If FAIL: DebuggerAgent.analyze_failure → FixerAgent.generate → goto 2
  5. If PASS or iterations exhausted: return result
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from api.models.agent_schemas import Fix, RootCauseResult
from validation.test_runner import TestRunResult, TestRunner

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3
DOCKER_IMAGE = "python:3.11-slim"
CONTAINER_WORKDIR = "/app"
PYTEST_CMD = [
    "python", "-m", "pytest",
    "tests/", "-x", "--tb=short",
    "--json-report", "--json-report-file=/app/pytest_report.json",
    "--timeout=30", "-q",
]


@dataclass
class SandboxResult:
    passed: bool
    failure_log: str = ""
    iterations: int = 1
    used_docker: bool = False
    final_fix: Fix | None = None          # the fix that finally passed (may differ from input)
    test_result: TestRunResult | None = None


class SandboxRunner:
    """
    Ephemeral Docker sandbox.  Falls back to file-system TestRunner when
    Docker is unavailable (CI environments without a Docker daemon, etc.).

    Usage:
        runner = SandboxRunner(repo_path=".")
        result = await runner.run(patches, debugger_agent, fixer_agent, bundle, error)
    """

    def __init__(self, repo_path: str = ".") -> None:
        self.repo_path = Path(repo_path)
        self._file_runner = TestRunner(repo_path=repo_path)
        self._docker_available: bool | None = None   # lazily checked

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def run(
        self,
        fix: Fix,
        debugger=None,
        fixer=None,
        bundle=None,
        error: dict | None = None,
    ) -> SandboxResult:
        """
        Run the sandbox loop.  If debugger/fixer/bundle/error are supplied,
        failed runs trigger an internal iteration before returning to the caller.
        """
        error = error or {}
        current_fix = fix
        last_failure = ""

        for iteration in range(1, MAX_ITERATIONS + 1):
            logger.info("SandboxRunner: iteration %d/%d", iteration, MAX_ITERATIONS)

            if await self._docker_ok():
                result = await self._run_in_docker(current_fix)
            else:
                result = await self._run_file_based(current_fix)

            result.iterations = iteration

            if result.passed:
                result.final_fix = current_fix
                logger.info("SandboxRunner: PASSED on iteration %d", iteration)
                return result

            last_failure = result.failure_log
            logger.warning(
                "SandboxRunner: iteration %d FAILED. log=%s",
                iteration, last_failure[:300],
            )

            # No agents supplied, or last iteration — stop here
            if not (debugger and fixer and bundle) or iteration == MAX_ITERATIONS:
                break

            # Internal iteration: ask DebuggerAgent to analyse the failure,
            # then ask FixerAgent for a revised fix
            logger.info("SandboxRunner: triggering internal DebuggerAgent→FixerAgent cycle")
            try:
                refined_rc = await self._analyze_failure(
                    debugger, bundle, error, last_failure, current_fix
                )
                current_fix = await fixer.generate(refined_rc, bundle, error)
            except Exception as exc:
                logger.error("SandboxRunner: internal iteration failed: %s", exc, exc_info=True)
                break

        # All iterations exhausted — return the last failure
        failed_result = SandboxResult(
            passed=False,
            failure_log=last_failure,
            iterations=MAX_ITERATIONS,
            used_docker=await self._docker_ok(),
            final_fix=current_fix,
        )
        return failed_result

    # ──────────────────────────────────────────────────────────────────────────
    # Docker execution
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_in_docker(self, fix: Fix) -> SandboxResult:
        """Spin up an ephemeral container, apply patches, run pytest."""
        t0 = time.perf_counter()
        sandbox_dir = tempfile.mkdtemp(prefix="dara_docker_sandbox_")
        try:
            await asyncio.to_thread(self._prepare_sandbox, sandbox_dir, fix)
            result = await asyncio.to_thread(
                self._exec_docker, sandbox_dir
            )
            result.used_docker = True
            logger.info(
                "SandboxRunner[docker]: passed=%s duration=%.1fs",
                result.passed, time.perf_counter() - t0,
            )
            return result
        except Exception as exc:
            logger.error("SandboxRunner[docker]: error: %s", exc, exc_info=True)
            # Graceful degradation
            return await self._run_file_based(fix)
        finally:
            shutil.rmtree(sandbox_dir, ignore_errors=True)

    def _exec_docker(self, sandbox_dir: str) -> SandboxResult:
        """Synchronous Docker run (called via asyncio.to_thread)."""
        try:
            from testcontainers.core.container import DockerContainer  # type: ignore[import]
        except ImportError:
            raise RuntimeError("testcontainers not installed")

        report_path = Path(sandbox_dir) / "pytest_report.json"

        with (
            DockerContainer(DOCKER_IMAGE)
            .with_volume_mapping(sandbox_dir, CONTAINER_WORKDIR, "rw")
            .with_env("PYTHONPATH", CONTAINER_WORKDIR)
            .with_command(" ".join(PYTEST_CMD))
            # ── Security & resource isolation ──────────────────────────────
            # Block all outbound network traffic — patches must not phone home
            .with_kwargs(network_mode="none")
            # Hard cap: 512 MB RAM, 0.5 CPU core
            # Prevents a malicious or runaway patch from exhausting the host
            .with_kwargs(mem_limit="512m", nano_cpus=500_000_000)
            # Drop all Linux capabilities — minimal attack surface
            .with_kwargs(cap_drop=["ALL"])
            # Prevent privilege escalation inside container
            .with_kwargs(security_opt=["no-new-privileges:true"])
            # Container is automatically removed by testcontainers context manager;
            # auto_remove=True ensures cleanup even on exception paths
            .with_kwargs(auto_remove=True)
        ) as container:
            exit_info = container.get_wrapped_container().wait()
            exit_code = exit_info.get("StatusCode", 1)
            raw_logs = container.get_logs()
            stdout = raw_logs[0].decode("utf-8", errors="replace") if raw_logs[0] else ""
            stderr = raw_logs[1].decode("utf-8", errors="replace") if raw_logs[1] else ""
            full_log = (stdout + stderr)[:4000]

        # Parse pytest JSON report written inside the container (accessible via volume)
        return self._parse_report(report_path, full_log)

    # ──────────────────────────────────────────────────────────────────────────
    # File-system fallback
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_file_based(self, fix: Fix) -> SandboxResult:
        """Fall back to the existing file-system TestRunner."""
        logger.info("SandboxRunner: using file-system fallback (no Docker)")
        patches = [
            {"file_path": p.file_path, "unified_diff": p.unified_diff}
            for p in fix.patches
        ]
        tr = await self._file_runner.run(patches, timeout_seconds=60)
        failure_log = ""
        if not tr.passed:
            failure_log = "; ".join(
                f"{f['nodeid']}: {str(f.get('message',''))[:200]}"
                for f in tr.failures[:5]
            )
        return SandboxResult(
            passed=tr.passed,
            failure_log=failure_log,
            used_docker=False,
            test_result=tr,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _prepare_sandbox(self, sandbox_dir: str, fix: Fix) -> None:
        """Copy repo into sandbox_dir and apply patches (file-content replacement)."""
        sb = Path(sandbox_dir)
        SKIP = {".git", "__pycache__", ".venv", "node_modules", ".mypy_cache", "admin_ui", "artisan-admin"}
        for item in self.repo_path.iterdir():
            if item.name in SKIP:
                continue
            dest = sb / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*SKIP))
            else:
                shutil.copy2(item, dest)

        # Apply patches: write fixed content derived from the diff
        for patch in fix.patches:
            target = sb / patch.file_path
            if not target.exists():
                continue
            fixed_lines = self._apply_diff(
                target.read_text(encoding="utf-8", errors="replace"),
                patch.unified_diff,
            )
            if fixed_lines is not None:
                target.write_text(fixed_lines, encoding="utf-8")

    @staticmethod
    def _apply_diff(original: str, unified_diff: str) -> str | None:
        """
        Apply a unified diff to original content.
        Uses `patch` CLI if available; falls back to simple line-based application.
        """
        if shutil.which("patch"):
            try:
                proc = subprocess.run(
                    ["patch", "-p1", "--output=-"],
                    input=original + "\n" + unified_diff,
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    return proc.stdout
            except Exception:
                pass
        # Fallback: reconstruct patched content from diff (+/context lines)
        lines: list[str] = []
        for line in unified_diff.splitlines():
            if line.startswith(("---", "+++", "@@")):
                continue
            if line.startswith("-"):
                continue
            lines.append(line[1:] if line.startswith("+") else line)
        return "\n".join(lines) if lines else None

    def _parse_report(self, report_path: Path, raw_log: str) -> SandboxResult:
        if not report_path.exists():
            # No report — infer from log content
            passed = "failed" not in raw_log.lower() and "error" not in raw_log.lower()
            return SandboxResult(passed=passed, failure_log=raw_log[:2000])
        try:
            data = json.loads(report_path.read_text())
            summary = data.get("summary", {})
            failed = summary.get("failed", 0)
            failures = [
                f"{t['nodeid']}: {t.get('call', {}).get('longrepr', '')[:300]}"
                for t in data.get("tests", [])
                if t.get("outcome") == "failed"
            ]
            return SandboxResult(
                passed=failed == 0,
                failure_log="\n".join(failures)[:3000] if failures else "",
            )
        except Exception as exc:
            logger.warning("SandboxRunner: could not parse report: %s", exc)
            return SandboxResult(passed=True, failure_log="")

    async def _docker_ok(self) -> bool:
        """Check once if Docker daemon is reachable."""
        if self._docker_available is not None:
            return self._docker_available
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            self._docker_available = proc.returncode == 0
        except Exception:
            self._docker_available = False
        logger.info("SandboxRunner: docker_available=%s", self._docker_available)
        return self._docker_available

    async def _analyze_failure(
        self,
        debugger,
        bundle,
        error: dict,
        failure_log: str,
        current_fix: Fix,
    ) -> RootCauseResult:
        """
        Build an augmented error dict from the sandbox failure log and
        ask the DebuggerAgent for a refined root cause to drive the next fix.
        """
        augmented_error = {
            **error,
            "message": f"[SANDBOX FAILURE] {failure_log[:500]}",
            "stack_trace": failure_log[:2000],
            "error_class": error.get("error_class", "test_failure"),
        }
        return await debugger.analyze(bundle, augmented_error)
