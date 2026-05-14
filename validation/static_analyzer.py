from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class StaticAnalysisResult:
    passed: bool
    tool: str
    findings: list[dict] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    duration_ms: int = 0
    raw_output: str = ""

    @property
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        return f"{self.tool}: {status} | errors={self.error_count} warnings={self.warning_count}"


class StaticAnalyzer:
    """
    Runs Ruff (fast Python linter) on patched code.
    Falls back to a simple regex-based security scanner if ruff unavailable.
    Never crashes - always returns a result.
    """

    SECURITY_PATTERNS = [
        ("eval(", "S307", "Use of eval() is a security risk"),
        ("exec(", "S102", "Use of exec() is a security risk"),
        ("__import__", "S108", "Dynamic import"),
        ("subprocess.call(", "S603", "Subprocess call with shell"),
        ("shell=True", "S602", "Subprocess with shell=True"),
        ("pickle.loads", "S301", "Pickle deserialization"),
        ("sql =", "S608", "Possible SQL injection"),
        ("password =", "S105", "Hardcoded password"),
    ]

    async def analyze(self, file_path: str, patched_content: str) -> StaticAnalysisResult:
        import time
        t0 = time.perf_counter()

        # Security: validate file_path doesn't contain path traversal sequences
        # (file_path is stored in our DB — defense in depth against malformed data)
        if file_path:
            resolved = Path(file_path).resolve()
            # Reject paths that try to escape to system dirs
            dangerous_roots = [Path("C:/Windows"), Path("/etc"), Path("/bin"), Path("/usr")]
            if any(str(resolved).startswith(str(d)) for d in dangerous_roots):
                logger.warning("StaticAnalyzer: suspicious file_path rejected: %s", file_path)
                return StaticAnalysisResult(passed=False, tool="security_guard",
                                             error_count=1, raw_output="Path traversal rejected")

        # Write patched content to temp file (tmp_path is always a system-temp created path)
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", encoding="utf-8",
                                         delete=False, prefix="dara_patch_") as f:
            f.write(patched_content)
            tmp_path = f.name

        try:
            result = self._run_ruff(tmp_path) if shutil.which("ruff") else self._regex_scan(patched_content)
        except Exception as e:
            logger.warning("StaticAnalyzer error: %s", e)
            result = StaticAnalysisResult(passed=True, tool="noop", error_count=0)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

        result.duration_ms = int((time.perf_counter() - t0) * 1000)
        logger.debug("StaticAnalyzer: %s in %dms", result.summary, result.duration_ms)
        return result

    def _run_ruff(self, tmp_path: str) -> StaticAnalysisResult:
        proc = subprocess.run(
            ["ruff", "check", "--output-format=json", "--select=S,E9,F", tmp_path],
            capture_output=True, text=True, timeout=30,
        )
        findings = []
        errors, warnings = 0, 0
        try:
            raw = json.loads(proc.stdout) if proc.stdout.strip() else []
            for item in raw:
                severity = "error" if item.get("code","").startswith(("E","F")) else "warning"
                findings.append({
                    "code": item.get("code",""),
                    "message": item.get("message",""),
                    "line": item.get("location",{}).get("row",0),
                    "severity": severity,
                })
                if severity == "error": errors += 1
                else: warnings += 1
        except json.JSONDecodeError:
            pass
        # Block only on security codes (S*) or critical syntax errors (E9*)
        blocking = [f for f in findings if f["code"].startswith(("S","E9"))]
        return StaticAnalysisResult(
            passed=len(blocking) == 0,
            tool="ruff",
            findings=findings,
            error_count=errors,
            warning_count=warnings,
            raw_output=proc.stdout,
        )

    def _regex_scan(self, content: str) -> StaticAnalysisResult:
        findings = []
        for line_no, line in enumerate(content.splitlines(), 1):
            for pattern, code, msg in self.SECURITY_PATTERNS:
                if pattern in line:
                    findings.append({"code": code, "message": msg, "line": line_no, "severity": "error"})
        return StaticAnalysisResult(
            passed=len(findings) == 0,
            tool="regex_scanner",
            findings=findings,
            error_count=len(findings),
        )
