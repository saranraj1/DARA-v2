"""
agents/reviewer.py
==================
Reviews a Fix against root cause analysis.

Security gate (new):
  SecurityAuditor runs on the raw diff BEFORE the LLM is called.
  HIGH severity finding → immediate rejection (LLM never called).
  This saves tokens and ensures security issues are never approved.

Confidence gate (existing):
  fix.confidence_retained < 0.5 downgrades "approve" → "approve_with_comments".

Temperature: 0.0 (strict pass/fail).
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult
from config.settings import get_settings
from validation.security_auditor import SecurityAuditResult, SecurityAuditor

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/reviewer_v1.txt")


class ReviewerAgent:
    """
    Reviews a Fix against root cause analysis.

    Pipeline:
      1. SecurityAuditor pre-check (Bandit + Semgrep on diff)
         → HIGH finding: reject immediately, no LLM call
      2. LLM review (quality/correctness gate)
      3. Confidence gate (downgrade if confidence_retained < 0.5)
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")
        self._auditor = SecurityAuditor()

    async def review(
        self,
        fix: Fix,
        root_cause: RootCauseResult,
        error: dict,
    ) -> ReviewResult:
        # ── Guard: no patches ──────────────────────────────────────────────
        if not fix.patches:
            return ReviewResult(
                quality_score=0.0,
                correctness_passes=False,
                security_passes=True,
                overall_recommendation="reject",
                rejection_reason="No patches generated",
                reviewer_notes="Empty patch set",
                issues=["No patches"],
            )

        # ── Step 1: Security audit (Bandit + Semgrep) ──────────────────────
        settings = get_settings()
        audit = SecurityAuditResult(rejected=False, has_high_severity=False, findings=[])
        if settings.enable_security_validation:
            audit = await self._auditor.audit(fix)

            if audit.rejected:
                logger.warning(
                    "ReviewerAgent: SECURITY REJECTION — %s", audit.rejection_reason[:300]
                )
                issues = [str(f) for f in audit.high_findings]
                return ReviewResult(
                    quality_score=0.0,
                    correctness_passes=False,
                    security_passes=False,
                    overall_recommendation="reject",
                    rejection_reason=(
                        f"Security audit detected HIGH severity issue(s): "
                        f"{audit.rejection_reason[:400]}"
                    ),
                    reviewer_notes=(
                        f"Automatic security rejection. Tools: {', '.join(audit.tools_run)}. "
                        f"The FixerAgent must rewrite the patch without these patterns."
                    ),
                    issues=issues,
                    security_audit=audit,
                )

        # ── Step 2: LLM review ─────────────────────────────────────────────
        patch_content = "\n\n".join(p.unified_diff for p in fix.patches)

        # Append medium/low security findings as reviewer context
        security_notes = ""
        if audit.findings:
            security_notes = "\n\nSECURITY NOTES (non-blocking, review manually):\n" + "\n".join(
                f"  [{f.severity}] {f.tool}/{f.rule_id}: {f.message} (line {f.line})"
                for f in audit.findings[:10]
            )

        prompt = (
            self._prompt_template
            .replace("{{patch_content}}", patch_content[:4000] + security_notes)
            .replace("{{error_class}}", error.get("error_class", ""))
            .replace("{{message}}", error.get("message", "")[:300])
            .replace("{{service}}", error.get("service", "unknown"))
            .replace("{{root_cause}}", root_cause.root_cause[:500])
            .replace("{{confidence_retained}}", str(round(fix.confidence_retained, 2)))
        )
        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a code reviewer. Output ONLY valid JSON. Be strict.",
            temperature=0.0, max_tokens=1024, priority="high",
        )
        result = self._parse(raw, fix, audit)

        logger.info(
            "ReviewerAgent: score=%.2f rec=%s security_tools=%s",
            result.quality_score, result.overall_recommendation,
            ", ".join(audit.tools_run) or "none",
        )
        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Parsing helpers (unchanged from original)
    # ──────────────────────────────────────────────────────────────────────────

    def _parse(self, raw: str, fix: Fix, audit=None) -> ReviewResult:
        data = self._extract_json(raw)
        score = min(1.0, max(0.0, float(data.get("quality_score", 0.5))))
        rec = data.get("overall_recommendation", "approve_with_comments")
        if rec not in ("approve", "approve_with_comments", "reject"):
            rec = "approve_with_comments"
        if fix.confidence_retained < 0.5 and rec == "approve":
            rec = "approve_with_comments"
        # Never emit 0.0 score unless reviewer explicitly said so
        if score == 0.0 and not data:
            score = 0.5
        return ReviewResult(
            quality_score=score,
            correctness_passes=bool(data.get("correctness_passes", True)),
            security_passes=bool(data.get("security_passes", True)),
            overall_recommendation=rec,
            rejection_reason=data.get("rejection_reason"),
            reviewer_notes=data.get("reviewer_notes", "No notes"),
            issues=data.get("issues", []),
            security_audit=audit,
        )

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        fallback: dict = {}
        score_m = re.search(r'"quality_score"\s*:\s*([0-9.]+)', text)
        if score_m:
            fallback["quality_score"] = float(score_m.group(1))
        rec_m = re.search(r'"overall_recommendation"\s*:\s*"([^"]+)"', text)
        if rec_m:
            fallback["overall_recommendation"] = rec_m.group(1)
        if fallback:
            return fallback
        logger.warning(
            "ReviewerAgent: JSON parse failed, using neutral fallback. raw=%s", text[:200]
        )
        return {}
