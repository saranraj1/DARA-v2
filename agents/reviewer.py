from __future__ import annotations
import json, logging, re
from pathlib import Path
from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/reviewer_v1.txt")


class ReviewerAgent:
    """
    Reviews a Fix against root cause analysis. Temp=0.0 (strict pass/fail).
    Confidence gate: fix.confidence_retained < 0.5 downgrades "approve" to "approve_with_comments".
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    async def review(self, fix: Fix, root_cause: RootCauseResult, error: dict) -> ReviewResult:
        if not fix.patches:
            return ReviewResult(quality_score=0.0, correctness_passes=False,
                                security_passes=True, overall_recommendation="reject",
                                rejection_reason="No patches generated",
                                reviewer_notes="Empty patch set", issues=["No patches"])
        patch_content = "\n\n".join(p.unified_diff for p in fix.patches)
        prompt = (self._prompt_template
            .replace("{{patch_content}}", patch_content[:4000])
            .replace("{{error_class}}", error.get("error_class",""))
            .replace("{{message}}", error.get("message","")[:300])
            .replace("{{service}}", error.get("service","unknown"))
            .replace("{{root_cause}}", root_cause.root_cause[:500])
            .replace("{{confidence_retained}}", str(round(fix.confidence_retained, 2))))
        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a code reviewer. Output ONLY valid JSON. Be strict.",
            temperature=0.0, max_tokens=1024, priority="high",
        )
        result = self._parse(raw, fix)
        logger.info("ReviewerAgent: score=%.2f rec=%s", result.quality_score, result.overall_recommendation)
        return result

    def _parse(self, raw: str, fix: Fix) -> ReviewResult:
        data = self._extract_json(raw)
        score = min(1.0, max(0.0, float(data.get("quality_score", 0.5))))
        rec = data.get("overall_recommendation", "approve_with_comments")
        if rec not in ("approve","approve_with_comments","reject"):
            rec = "approve_with_comments"
        if fix.confidence_retained < 0.5 and rec == "approve":
            rec = "approve_with_comments"
        return ReviewResult(
            quality_score=score,
            correctness_passes=bool(data.get("correctness_passes", True)),
            security_passes=bool(data.get("security_passes", True)),
            overall_recommendation=rec,
            rejection_reason=data.get("rejection_reason"),
            reviewer_notes=data.get("reviewer_notes","No notes"),
            issues=data.get("issues",[]),
        )

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?","",text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        logger.warning("ReviewerAgent: JSON parse failed")
        return {}
