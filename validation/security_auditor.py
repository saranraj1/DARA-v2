"""
validation/security_auditor.py
===============================
Security-focused static analysis on generated diffs.

Runs Bandit and/or Semgrep on the *patched* code extracted from each
unified diff, then classifies findings by severity.

Decision tree:
  HIGH severity finding   → SecurityAuditResult.rejected = True
                            (ReviewerAgent returns rejection immediately)
  MEDIUM/LOW findings     → passed through as warnings for the reviewer LLM
  No security tools found → falls back to regex scanner (defence-in-depth)

Severity mapping:
  Bandit  : confidence=HIGH+severity=HIGH → DARA HIGH
            confidence=MEDIUM or severity=MEDIUM → DARA MEDIUM
            everything else → DARA LOW
  Semgrep : severity=ERROR → DARA HIGH
            severity=WARNING → DARA MEDIUM
            severity=INFO → DARA LOW
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from api.models.agent_schemas import Fix

logger = logging.getLogger(__name__)

# Semgrep ruleset — security-audit covers OWASP Top-10, injection, auth bypass
SEMGREP_CONFIG = "p/security-audit"

# Bandit severity/confidence threshold that constitutes a DARA HIGH finding
_BANDIT_HIGH_PAIRS = {("HIGH", "HIGH"), ("HIGH", "MEDIUM"), ("MEDIUM", "HIGH")}


@dataclass
class SecurityFinding:
    tool: str                          # "bandit" | "semgrep" | "regex"
    rule_id: str
    severity: str                      # "HIGH" | "MEDIUM" | "LOW"
    message: str
    file_path: str
    line: int = 0

    def __str__(self) -> str:
        return f"[{self.severity}][{self.tool}/{self.rule_id}] {self.message} ({self.file_path}:{self.line})"


@dataclass
class SecurityAuditResult:
    rejected: bool                                  # True if ANY HIGH finding exists
    has_high_severity: bool
    findings: list[SecurityFinding] = field(default_factory=list)
    tools_run: list[str] = field(default_factory=list)
    rejection_reason: str = ""

    @property
    def high_findings(self) -> list[SecurityFinding]:
        return [f for f in self.findings if f.severity == "HIGH"]

    @property
    def summary(self) -> str:
        counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in self.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return (
            f"Security audit: HIGH={counts['HIGH']} MEDIUM={counts['MEDIUM']} "
            f"LOW={counts['LOW']} | rejected={self.rejected}"
        )


# Lightweight regex fallback (mirrors StaticAnalyzer patterns but extended)
_REGEX_RULES: list[tuple[str, str, str, str]] = [
    ("eval(",         "R001", "HIGH",   "Use of eval() — arbitrary code execution risk"),
    ("exec(",         "R002", "HIGH",   "Use of exec() — arbitrary code execution risk"),
    ("__import__(",   "R003", "MEDIUM", "Dynamic import"),
    ("shell=True",    "R004", "HIGH",   "subprocess with shell=True — command injection"),
    ("pickle.loads",  "R005", "HIGH",   "Pickle deserialization — arbitrary object instantiation"),
    ("yaml.load(",    "R006", "HIGH",   "yaml.load without Loader — code execution risk"),
    ("subprocess.call(", "R007", "MEDIUM", "Subprocess call — verify input is sanitised"),
    ("os.system(",    "R008", "HIGH",   "os.system() — command injection risk"),
    ("MD5(",          "R009", "MEDIUM", "Weak hash algorithm MD5"),
    ("SHA1(",         "R010", "MEDIUM", "Weak hash algorithm SHA1"),
    ("password =",    "R011", "MEDIUM", "Potential hardcoded password"),
    ("secret =",      "R012", "MEDIUM", "Potential hardcoded secret"),
    ("# nosec",       "R013", "LOW",    "Security check suppression — review manually"),
]


class SecurityAuditor:
    """
    Runs Bandit and Semgrep on extracted patch content.
    Falls back to regex scanning if neither tool is installed.

    Usage:
        auditor = SecurityAuditor()
        result = await auditor.audit(fix)
    """

    async def audit(self, fix: Fix) -> SecurityAuditResult:
        """Audit all patches in a Fix. Returns aggregated SecurityAuditResult."""
        import asyncio

        if not fix.patches:
            return SecurityAuditResult(rejected=False, has_high_severity=False)

        all_findings: list[SecurityFinding] = []
        tools_run: set[str] = set()

        for patch in fix.patches:
            if not patch.unified_diff:
                continue
            patched = _extract_patched_content(patch.unified_diff)
            if not patched.strip():
                continue

            findings, used_tools = await asyncio.to_thread(
                self._audit_content, patched, patch.file_path
            )
            all_findings.extend(findings)
            tools_run.update(used_tools)

        high = [f for f in all_findings if f.severity == "HIGH"]
        rejected = len(high) > 0
        rejection_reason = ""
        if rejected:
            rejection_reason = "; ".join(str(f) for f in high[:3])

        result = SecurityAuditResult(
            rejected=rejected,
            has_high_severity=rejected,
            findings=all_findings,
            tools_run=sorted(tools_run),
            rejection_reason=rejection_reason,
        )
        logger.info("SecurityAuditor: %s", result.summary)
        if rejected:
            logger.warning("SecurityAuditor: REJECTED — %s", rejection_reason[:300])
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Core scan logic (synchronous, called via to_thread)
    # ──────────────────────────────────────────────────────────────────────────

    def _audit_content(
        self, content: str, file_path: str
    ) -> tuple[list[SecurityFinding], list[str]]:
        findings: list[SecurityFinding] = []
        tools: list[str] = []

        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", encoding="utf-8",
            delete=False, prefix="dara_sec_"
        ) as f:
            f.write(content)
            tmp = f.name

        try:
            if shutil.which("bandit"):
                findings.extend(self._run_bandit(tmp, file_path))
                tools.append("bandit")

            if shutil.which("semgrep"):
                findings.extend(self._run_semgrep(tmp, file_path))
                tools.append("semgrep")

            if not tools:
                # Neither tool available — regex fallback
                findings.extend(self._regex_scan(content, file_path))
                tools.append("regex")
        finally:
            Path(tmp).unlink(missing_ok=True)

        return findings, tools

    # ──────────────────────────────────────────────────────────────────────────
    # Bandit
    # ──────────────────────────────────────────────────────────────────────────

    def _run_bandit(self, tmp_path: str, file_path: str) -> list[SecurityFinding]:
        try:
            proc = subprocess.run(
                ["bandit", "-f", "json", "-q", tmp_path],
                capture_output=True, text=True, timeout=30,
            )
            raw = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
            logger.warning("SecurityAuditor[bandit]: error: %s", exc)
            return []

        findings: list[SecurityFinding] = []
        for issue in raw.get("results", []):
            sev = issue.get("issue_severity", "LOW").upper()
            conf = issue.get("issue_confidence", "LOW").upper()
            dara_sev = self._bandit_severity(sev, conf)
            findings.append(SecurityFinding(
                tool="bandit",
                rule_id=issue.get("test_id", "B000"),
                severity=dara_sev,
                message=issue.get("issue_text", ""),
                file_path=file_path,
                line=issue.get("line_number", 0),
            ))
        return findings

    @staticmethod
    def _bandit_severity(sev: str, conf: str) -> str:
        if (sev, conf) in _BANDIT_HIGH_PAIRS:
            return "HIGH"
        if sev == "MEDIUM" or conf == "MEDIUM":
            return "MEDIUM"
        return "LOW"

    # ──────────────────────────────────────────────────────────────────────────
    # Semgrep
    # ──────────────────────────────────────────────────────────────────────────

    def _run_semgrep(self, tmp_path: str, file_path: str) -> list[SecurityFinding]:
        try:
            proc = subprocess.run(
                [
                    "semgrep", "--config", SEMGREP_CONFIG,
                    "--json", "--quiet", "--no-git-ignore",
                    tmp_path,
                ],
                capture_output=True, text=True, timeout=60,
            )
            raw = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
        except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as exc:
            logger.warning("SecurityAuditor[semgrep]: error: %s", exc)
            return []

        findings: list[SecurityFinding] = []
        for result in raw.get("results", []):
            extra = result.get("extra", {})
            raw_sev = extra.get("severity", "INFO").upper()
            dara_sev = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}.get(raw_sev, "LOW")
            findings.append(SecurityFinding(
                tool="semgrep",
                rule_id=result.get("check_id", "unknown"),
                severity=dara_sev,
                message=extra.get("message", ""),
                file_path=file_path,
                line=result.get("start", {}).get("line", 0),
            ))
        return findings

    # ──────────────────────────────────────────────────────────────────────────
    # Regex fallback
    # ──────────────────────────────────────────────────────────────────────────

    def _regex_scan(self, content: str, file_path: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for line_no, line in enumerate(content.splitlines(), 1):
            for pattern, rule_id, severity, message in _REGEX_RULES:
                if pattern in line:
                    findings.append(SecurityFinding(
                        tool="regex",
                        rule_id=rule_id,
                        severity=severity,
                        message=message,
                        file_path=file_path,
                        line=line_no,
                    ))
        return findings


# ──────────────────────────────────────────────────────────────────────────────
# Utility
# ──────────────────────────────────────────────────────────────────────────────

def _extract_patched_content(unified_diff: str) -> str:
    """Reconstruct patched file content from a unified diff."""
    lines: list[str] = []
    for line in unified_diff.splitlines():
        if line.startswith(("---", "+++", "@@")):
            continue
        if line.startswith("-"):
            continue
        lines.append(line[1:] if line.startswith("+") else line)
    return "\n".join(lines)
