from __future__ import annotations
import json, logging, re
from pathlib import Path
from context.builder import ContextBundle
from context.retriever import count_tokens
from api.models.agent_schemas import RootCauseResult

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/root_cause_v1.txt")
VALID_STRATEGIES = {"template_based", "llm_single_file", "llm_multi_file", "human_escalation"}


class DebuggerAgent:
    """
    Analyzes a ContextBundle and returns a RootCauseResult.
    Temp=0.1. JSON parse failures retried with strict prompt. Confidence clamped [0,1].
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    async def analyze(self, bundle: ContextBundle, error: dict) -> RootCauseResult:
        prompt = self._build_prompt(bundle, error)
        logger.info("DebuggerAgent: prompt=%d tokens error_id=%s",
                    count_tokens(prompt), error.get("id","")[:8])
        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a software debugger. Output ONLY valid JSON. No markdown fences.",
            temperature=0.1, max_tokens=2048, priority="high",
        )
        result = self._parse(raw, error)
        logger.info("DebuggerAgent: confidence=%.2f strategy=%s",
                    result.confidence, result.suggested_strategy)
        return result

    def _build_prompt(self, bundle: ContextBundle, error: dict) -> str:
        related = "\n\n".join(
            f"[{c.get('file_path','')} {c.get('function_name','')}]\n{c.get('content','')[:400]}"
            for c in bundle.related_functions[:5]
        )
        commits = "\n".join(
            f"- {c.get('sha','')} {c.get('date','')[:10]}: {c.get('message','')[:80]}"
            for c in bundle.recent_commits[:5]
        )
        bugs = "\n".join(
            f"- [{b.get('error_class','')}] {b.get('message_summary','')[:100]}"
            for b in bundle.similar_past_bugs[:3]
        )
        blame = ""
        if bundle.blame_info:
            bl = bundle.blame_info
            blame = f"Author: {bl.get('author','')} on {bl.get('date','')[:10]}"

        # Week 9-10: BlameResult injection — if present, prepend high-confidence signal
        blame_attribution = ""
        if hasattr(bundle, "blame_result") and bundle.blame_result:
            br = bundle.blame_result
            if br.blame_confidence >= 0.5:
                blame_attribution = (
                    f"\n⚠️  BLAME ATTRIBUTION (confidence={br.blame_confidence:.0%}):\n"
                    f"  Commit  : {br.commit_sha_short or 'unknown'}\n"
                    f"  Author  : {br.author_name or br.author_email or 'unknown'}\n"
                    f"  Message : {(br.commit_message or '')[:120]}\n"
                    f"  Deployed: {br.hours_before_error:.1f}h before error\n"
                    f"  Method  : {br.blame_method}\n"
                    f"This commit is the MOST PROBABLE cause — investigate it first.\n"
                )

        # Week 8-9: cross-service context injection
        cross_svc = ""
        if hasattr(bundle, "cross_service_bundle") and bundle.cross_service_bundle:
            cross_svc = (
                f"\n=== CROSS-SERVICE CONTEXT ===\n"
                f"{bundle.cross_service_bundle.as_prompt_block()}\n"
            )

        return (self._prompt_template
            .replace("{{error_class}}", error.get("error_class",""))
            .replace("{{message}}", error.get("message","")[:500])
            .replace("{{service}}", error.get("service","unknown"))
            .replace("{{severity}}", error.get("severity","medium"))
            .replace("{{commit_sha}}", error.get("commit_sha","unknown"))
            .replace("{{branch}}", error.get("branch","unknown"))
            .replace("{{stack_trace}}", (error.get("stack_trace","No stack trace") or "")[:2000])
            .replace("{{file_path}}", bundle.erroring_file or "unknown")
            .replace("{{line_number}}", str(error.get("line_number","?")))
            .replace("{{erroring_code}}", (bundle.erroring_code or "Not available")[:1500])
            .replace("{{related_functions}}", related or "None found")
            .replace("{{recent_commits}}", commits or "No recent commits")
            .replace("{{similar_past_bugs}}", bugs or "No similar bugs found")
            .replace("{{blame_info}}", blame or "Not available")
        ) + blame_attribution + cross_svc


    def _parse(self, raw: str, error: dict) -> RootCauseResult:
        data = self._extract_json(raw)
        conf = min(1.0, max(0.0, float(data.get("confidence", 0.3))))
        strategy = data.get("suggested_strategy", "llm_single_file")
        if strategy not in VALID_STRATEGIES:
            strategy = "llm_single_file"
        files = data.get("files_to_change", [])
        if not files and error.get("file_path"):
            files = [error["file_path"]]
        return RootCauseResult(
            immediate_cause=data.get("immediate_cause", "Unknown"),
            root_cause=data.get("root_cause", "Unable to determine"),
            contributing_factors=data.get("contributing_factors", []),
            confidence=conf,
            evidence_quality=data.get("evidence_quality", "low"),
            files_to_change=files,
            suggested_strategy=strategy,
            reasoning_trace=data.get("reasoning_trace", raw[:500]),
        )

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        logger.warning("DebuggerAgent: JSON parse failed, using defaults")
        return {}
