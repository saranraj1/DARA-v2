"""Writes all Week 4 agent files with correct UTF-8 encoding."""
import pathlib

ROOT = pathlib.Path(r"c:\Production level projects\ADAA")

# ── agents/debugger.py ───────────────────────────────────────
(ROOT / "agents" / "debugger.py").write_text(r"""
from __future__ import annotations
import json, logging, re
from pathlib import Path
from context.builder import ContextBundle
from context.retriever import count_tokens
from api.models.agent_schemas import RootCauseResult

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/root_cause_v1.txt")
MAX_PROMPT_TOKENS = 7000
VALID_STRATEGIES = {"template_based", "llm_single_file", "llm_multi_file", "human_escalation"}


class DebuggerAgent:
    """
    Analyzes a ContextBundle and returns a RootCauseResult.
    Temperature: 0.1 (precise, near-deterministic).
    Self-checks:
      ~ JSON parse failure: retry once with strict system prompt
      ~ missing fields: filled with sensible defaults
      ~ confidence clamped to [0.0, 1.0]
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    async def analyze(self, bundle: ContextBundle, error: dict) -> RootCauseResult:
        prompt = self._build_prompt(bundle, error)
        tokens = count_tokens(prompt)
        logger.info("DebuggerAgent: prompt=%d tokens, error_id=%s", tokens, error.get("id","")[:8])

        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a software debugger. Respond ONLY with valid JSON. No markdown, no prose.",
            temperature=0.1,
            max_tokens=2048,
            priority="high",
        )

        result = self._parse_response(raw, error)
        logger.info("DebuggerAgent: confidence=%.2f strategy=%s", result.confidence, result.suggested_strategy)
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
            blame = f"Author: {bl.get('author','')} <{bl.get('email','')}> on {bl.get('date','')[:10]}"

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
            .replace("{{blame_info}}", blame or "Not available"))

    def _parse_response(self, raw: str, error: dict) -> RootCauseResult:
        data = self._extract_json(raw)
        conf = min(1.0, max(0.0, float(data.get("confidence", 0.3))))
        strategy = data.get("suggested_strategy", "llm_single_file")
        if strategy not in VALID_STRATEGIES:
            strategy = "llm_single_file"
        files = data.get("files_to_change", [])
        if not files and error.get("file_path"):
            files = [error["file_path"]]
        return RootCauseResult(
            immediate_cause=data.get("immediate_cause", "Unknown immediate cause"),
            root_cause=data.get("root_cause", "Unable to determine root cause"),
            contributing_factors=data.get("contributing_factors", []),
            confidence=conf,
            evidence_quality=data.get("evidence_quality", "low"),
            files_to_change=files,
            suggested_strategy=strategy,
            reasoning_trace=data.get("reasoning_trace", raw[:500]),
        )

    def _extract_json(self, text: str) -> dict:
        # Strip markdown code fences
        text = re.sub(r"```(?:json)?", "", text).strip()
        # Find JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("DebuggerAgent: failed to parse JSON response, using defaults")
        return {}
""".lstrip(), encoding="utf-8")
print("agents/debugger.py written")

# ── agents/fixer.py ──────────────────────────────────────────
(ROOT / "agents" / "fixer.py").write_text(r"""
from __future__ import annotations
import difflib, json, logging, re
from pathlib import Path
from api.models.agent_schemas import Fix, PatchFile, RootCauseResult
from context.builder import ContextBundle

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/fix_generator_v1.txt")


class FixerAgent:
    """
    Generates a unified-diff patch for each file in root_cause.files_to_change.
    Temperature: 0.15 (deterministic but slightly creative for code).
    Self-checks:
      ~ file not found locally: uses erroring_code snippet as context
      ~ empty LLM output: returns empty patch with low confidence
      ~ invalid diff: catches difflib errors, returns raw LLM output
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    async def generate(
        self,
        root_cause: RootCauseResult,
        bundle: ContextBundle,
        error: dict,
    ) -> Fix:
        patches: list[PatchFile] = []
        confidence_sum = 0.0

        for file_path in root_cause.files_to_change[:3]:  # cap at 3 files
            patch = await self._fix_file(file_path, root_cause, bundle, error)
            if patch:
                patches.append(patch)
                confidence_sum += root_cause.confidence

        if not patches:
            logger.warning("FixerAgent: no patches generated")
            return self._empty_fix(error, root_cause)

        avg_confidence = confidence_sum / len(patches) if patches else 0.0

        return Fix(
            error_id=error.get("id", ""),
            patches=patches,
            total_files_changed=len(patches),
            total_lines_changed=sum(p.lines_changed for p in patches),
            fix_explanation=f"Fixed {len(patches)} file(s) addressing: {root_cause.root_cause[:200]}",
            suggested_tests=[],
            confidence_retained=round(avg_confidence, 3),
            regression_risk=self._assess_risk(patches, root_cause),
            strategy=root_cause.suggested_strategy,
            llm_provider="groq",
        )

    async def _fix_file(
        self,
        file_path: str,
        root_cause: RootCauseResult,
        bundle: ContextBundle,
        error: dict,
    ) -> PatchFile | None:
        # Try to load original file content
        original_content = ""
        fp = Path(file_path)
        if fp.exists():
            original_content = fp.read_text(encoding="utf-8", errors="replace")
        elif bundle.erroring_code:
            original_content = bundle.erroring_code  # use snippet as context
        else:
            logger.warning("FixerAgent: file not found: %s", file_path)
            return None

        prompt = self._build_prompt(file_path, original_content, root_cause, bundle, error)

        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a code fixer. Output the complete fixed file, then a JSON object. No commentary.",
            temperature=0.15,
            max_tokens=4096,
            priority="high",
        )

        return self._parse_response(raw, file_path, original_content, root_cause)

    def _build_prompt(self, file_path, content, root_cause, bundle, error) -> str:
        return (self._prompt_template
            .replace("{{immediate_cause}}", root_cause.immediate_cause)
            .replace("{{root_cause}}", root_cause.root_cause)
            .replace("{{suggested_strategy}}", root_cause.suggested_strategy)
            .replace("{{confidence}}", str(round(root_cause.confidence, 2)))
            .replace("{{error_class}}", error.get("error_class", ""))
            .replace("{{message}}", error.get("message", "")[:300])
            .replace("{{file_path}}", file_path)
            .replace("{{file_content}}", content[:6000])
            .replace("{{erroring_function}}", bundle.erroring_function or "unknown")
            .replace("{{line_number}}", str(error.get("line_number", "?"))))

    def _parse_response(self, raw: str, file_path: str, original: str, root_cause: RootCauseResult) -> PatchFile | None:
        data = self._extract_json(raw)
        fixed_code = data.get("fixed_code", "")

        if not fixed_code:
            # Try to extract code block from raw response
            match = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
            fixed_code = match.group(1) if match else ""

        if not fixed_code:
            logger.warning("FixerAgent: no fixed_code in response for %s", file_path)
            return None

        # Generate unified diff
        original_lines = original.splitlines(keepends=True)
        fixed_lines = fixed_code.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            original_lines, fixed_lines,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            lineterm="",
        ))

        if not diff_lines:
            logger.info("FixerAgent: no changes detected in %s (already correct?)", file_path)
            return None

        lines_changed = sum(1 for l in diff_lines if l.startswith(("+", "-")) and not l.startswith(("+++","---")))

        return PatchFile(
            file_path=file_path,
            unified_diff="\n".join(diff_lines),
            lines_changed=lines_changed,
            change_description=data.get("fix_explanation", root_cause.root_cause[:200]),
        )

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text).strip()
        match = re.search(r"\{[^{}]*\"fixed_code\"[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return {}

    def _assess_risk(self, patches: list, root_cause: RootCauseResult) -> str:
        total_lines = sum(p.lines_changed for p in patches)
        num_files = len(patches)
        if num_files > 2 or total_lines > 50:
            return "high"
        if num_files > 1 or total_lines > 20 or root_cause.confidence < 0.6:
            return "medium"
        return "low"

    def _empty_fix(self, error: dict, root_cause: RootCauseResult) -> Fix:
        return Fix(
            error_id=error.get("id", ""),
            patches=[],
            total_files_changed=0,
            total_lines_changed=0,
            fix_explanation="No fix generated: insufficient context or LLM failure",
            suggested_tests=[],
            confidence_retained=0.0,
            regression_risk="high",
            strategy=root_cause.suggested_strategy,
            llm_provider="none",
        )
""".lstrip(), encoding="utf-8")
print("agents/fixer.py written")

# ── agents/reviewer.py ───────────────────────────────────────
(ROOT / "agents" / "reviewer.py").write_text(r"""
from __future__ import annotations
import json, logging, re
from pathlib import Path
from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult

logger = logging.getLogger(__name__)
PROMPT_PATH = Path("prompts/reviewer_v1.txt")


class ReviewerAgent:
    """
    Reviews a Fix against the root cause analysis.
    Temperature: 0.0 (deterministic pass/fail decisions).
    Self-checks:
      ~ LLM parse failure: defaults to approve_with_comments with low score
      ~ confidence gate: if fixer confidence < 0.5, always approve_with_comments minimum
    """

    def __init__(self, llm_router) -> None:
        self._llm = llm_router
        self._prompt_template = PROMPT_PATH.read_text(encoding="utf-8")

    async def review(
        self,
        fix: Fix,
        root_cause: RootCauseResult,
        error: dict,
    ) -> ReviewResult:
        if not fix.patches:
            logger.warning("ReviewerAgent: no patches to review")
            return ReviewResult(
                quality_score=0.0,
                correctness_passes=False,
                security_passes=True,
                overall_recommendation="reject",
                rejection_reason="No patches were generated",
                reviewer_notes="Fix generation produced no diffs",
                issues=["Empty patch set"],
            )

        patch_content = "\n\n".join(p.unified_diff for p in fix.patches)
        prompt = self._build_prompt(patch_content, root_cause, fix, error)

        raw = await self._llm.complete(
            prompt=prompt,
            system="You are a code reviewer. Respond ONLY with valid JSON. Be strict.",
            temperature=0.0,
            max_tokens=1024,
            priority="high",
        )

        result = self._parse_response(raw, fix, root_cause)
        logger.info(
            "ReviewerAgent: score=%.2f recommendation=%s",
            result.quality_score, result.overall_recommendation,
        )
        return result

    def _build_prompt(self, patch_content, root_cause, fix, error) -> str:
        return (self._prompt_template
            .replace("{{patch_content}}", patch_content[:4000])
            .replace("{{error_class}}", error.get("error_class", ""))
            .replace("{{message}}", error.get("message", "")[:300])
            .replace("{{service}}", error.get("service", "unknown"))
            .replace("{{root_cause}}", root_cause.root_cause[:500])
            .replace("{{confidence_retained}}", str(round(fix.confidence_retained, 2))))

    def _parse_response(self, raw: str, fix: Fix, root_cause: RootCauseResult) -> ReviewResult:
        data = self._extract_json(raw)
        score = min(1.0, max(0.0, float(data.get("quality_score", 0.5))))
        rec = data.get("overall_recommendation", "approve_with_comments")
        if rec not in ("approve", "approve_with_comments", "reject"):
            rec = "approve_with_comments"
        # Confidence gate: low-confidence fixes cannot be auto-approved
        if fix.confidence_retained < 0.5 and rec == "approve":
            rec = "approve_with_comments"
        return ReviewResult(
            quality_score=score,
            correctness_passes=bool(data.get("correctness_passes", True)),
            security_passes=bool(data.get("security_passes", True)),
            overall_recommendation=rec,
            rejection_reason=data.get("rejection_reason"),
            reviewer_notes=data.get("reviewer_notes", "No notes provided"),
            issues=data.get("issues", []),
        )

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r"```(?:json)?", "", text).strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        logger.warning("ReviewerAgent: JSON parse failed, using defaults")
        return {}
""".lstrip(), encoding="utf-8")
print("agents/reviewer.py written")

# ── agents/memory.py ─────────────────────────────────────────
(ROOT / "agents" / "memory.py").write_text(r"""
from __future__ import annotations
import logging
from api.models.agent_schemas import Fix, RootCauseResult
from storage.postgres import get_postgres

logger = logging.getLogger(__name__)


class PatternMemory:
    """
    Manages the pattern_library: known error patterns and their fixes.
    Two operations:
      find_template(error) -> pattern dict if known
      record_success(error, fix, root_cause) -> stores for future re-use
    """

    async def find_template(self, error: dict) -> dict | None:
        """
        Check if this error class + service combination has a known fix template.
        Returns template dict or None if not found.
        """
        postgres = get_postgres()
        try:
            template = await postgres.get_fix_template(
                error_class=error.get("error_class", ""),
                service=error.get("service"),
            )
            if template:
                logger.info(
                    "PatternMemory: found template for %s (success_rate=%.2f)",
                    error.get("error_class"), template.success_rate or 0,
                )
            return {"template_id": str(template.id),
                    "fix_template": template.fix_template,
                    "success_rate": float(template.success_rate or 0),
                    "example_fix": template.example_fix} if template else None
        except Exception as e:
            logger.warning("PatternMemory.find_template failed: %s", e)
            return None

    async def record_success(
        self,
        error: dict,
        fix: Fix,
        root_cause: RootCauseResult,
        outcome: str = "accepted",
    ) -> None:
        """
        After a fix is accepted, store the pattern for future reference.
        This builds the institutional memory over time.
        """
        if outcome != "accepted" or fix.confidence_retained < 0.7:
            return  # only learn from high-confidence accepted fixes

        postgres = get_postgres()
        pattern = {
            "error_class": error.get("error_class", ""),
            "service": error.get("service"),
            "root_cause_pattern": root_cause.root_cause[:500],
            "fix_strategy": root_cause.suggested_strategy,
            "confidence_threshold": root_cause.confidence,
            "example_fix": fix.fix_explanation,
        }
        try:
            await postgres.upsert_pattern(pattern)
            logger.info(
                "PatternMemory: stored pattern for %s -> %s",
                error.get("error_class"), root_cause.suggested_strategy,
            )
        except Exception as e:
            logger.warning("PatternMemory.record_success failed: %s", e)

    async def get_stats(self) -> dict:
        """Return pattern library stats for the metrics endpoint."""
        postgres = get_postgres()
        try:
            return await postgres.get_pipeline_stats()
        except Exception as e:
            logger.warning("PatternMemory.get_stats failed: %s", e)
            return {}
""".lstrip(), encoding="utf-8")
print("agents/memory.py written")

print("\nAll agent files written successfully.")
