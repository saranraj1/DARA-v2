"""
DARA — Strategy Generator (Week 16-17)
========================================
When StrategyMonitor flags an error class as failing, StrategyGenerator
calls Groq LLM to produce 2 alternative fix generation prompt strategies
that directly address the observed failure patterns.

The meta-prompt is self-referential:
  - It feeds the failure reasons back into the generation prompt
  - It provides successful fix examples for contrast
  - It asks Groq to be concrete and structurally different from the current approach

Generated variants are saved to strategy_variants(status='draft') and
handed off to the StrategyEvaluator for A/B testing.

StrategyRouter db-driven override:
  get_active_strategy(error_class) → checks strategy_variants WHERE status='active'
  before falling back to the hard-coded specialist strategy. This allows
  new strategies to go live WITHOUT a code deploy.

Usage (Celery task, triggered by StrategyMonitor):
    generator = StrategyGenerator(llm_router=..., postgres=...)
    variants = await generator.generate_variants(
        error_class="null_reference",
        failing_stats=fc,  # FailingClass from StrategyMonitor
    )
"""
from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger(__name__)

# How many alternative variants to generate per failing class
NUM_VARIANTS = 2


class StrategyGenerator:
    """
    LLM-powered strategy variant generator.
    Produces alternative fix prompts for error classes that are repeatedly failing.
    """

    def __init__(self, llm_router=None, postgres=None) -> None:
        self._llm = llm_router
        self._postgres = postgres

    def _get_llm(self):
        if self._llm:
            return self._llm
        from config.llm_router import get_llm_router
        return get_llm_router()

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def generate_variants(
        self,
        error_class: str,
        failing_stats,  # FailingClass from StrategyMonitor
        n_variants: int = NUM_VARIANTS,
    ) -> list[dict]:
        """
        Generate n alternative prompt strategies for the given error_class.
        Returns list of dicts with template + hypothesis. Also saves to DB.
        """
        # Gather failure history for context
        failure_history = await self._fetch_failure_history(error_class)
        success_examples = await self._fetch_success_examples(error_class)

        # Current active strategy
        current_strategy = getattr(failing_stats, "dominant_strategy", "unknown")

        prompt = self._build_generation_prompt(
            error_class=error_class,
            current_strategy=current_strategy,
            failure_rate=getattr(failing_stats, "rejection_rate", 0.0),
            sample_count=getattr(failing_stats, "sample_count", 0),
            failure_history=failure_history,
            success_examples=success_examples,
            n_variants=n_variants,
        )

        logger.info(
            "StrategyGenerator: generating %d variants for %s (rate=%.0f%%)",
            n_variants, error_class, getattr(failing_stats, "rejection_rate", 0) * 100,
        )

        raw = await self._get_llm().complete(
            prompt=prompt,
            system=(
                "You are a meta-prompt engineer for an AI debugging system. "
                "Output ONLY valid JSON. No markdown, no commentary."
            ),
            temperature=0.7,
            max_tokens=3000,
        )

        variants = self._parse_variants(raw, error_class, n_variants)
        if variants:
            saved = await self._save_variants(variants, error_class)
            return saved
        logger.warning("StrategyGenerator: LLM returned no parseable variants for %s", error_class)
        return []

    async def get_active_strategy_prompt(self, error_class: str) -> str | None:
        """
        Return the prompt_template for the ACTIVE variant for this error_class.
        Returns None if no active variant — caller falls back to static strategy.
        This makes StrategyRouter DB-driven without a code deploy.
        """
        try:
            from sqlalchemy import select

            from storage.models import StrategyVariant
            pg = self._get_pg()
            async with pg.session() as sess:
                row = (await sess.execute(
                    select(StrategyVariant)
                    .where(
                        StrategyVariant.error_class == error_class,
                        StrategyVariant.status == "active",
                    )
                    .order_by(StrategyVariant.promoted_at.desc())
                    .limit(1)
                )).scalars().first()
                if row:
                    return row.prompt_template
        except Exception as e:
            logger.debug("get_active_strategy_prompt: %s", e)
        return None

    async def list_variants_for_class(self, error_class: str) -> list[dict]:
        """Return all variants (any status) for an error_class."""
        try:
            from sqlalchemy import select

            from storage.models import StrategyVariant
            pg = self._get_pg()
            async with pg.session() as sess:
                rows = (await sess.execute(
                    select(StrategyVariant)
                    .where(StrategyVariant.error_class == error_class)
                    .order_by(StrategyVariant.created_at.desc())
                )).scalars().all()
                return [
                    {
                        "id": str(r.id),
                        "variant_name": r.variant_name,
                        "status": r.status,
                        "success_rate": r.success_rate,
                        "total_uses": r.total_uses,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
        except Exception as e:
            logger.warning("list_variants_for_class: %s", e)
            return []

    # ── Private helpers ────────────────────────────────────────

    def _build_generation_prompt(
        self,
        error_class: str,
        current_strategy: str,
        failure_rate: float,
        sample_count: int,
        failure_history: list[str],
        success_examples: list[str],
        n_variants: int,
    ) -> str:
        failure_bullets = "\n".join(
            f"  - {r}" for r in failure_history[:5]
        ) or "  - No specific failure reasons recorded yet."

        success_bullets = "\n".join(
            f"  - {e[:200]}" for e in success_examples[:3]
        ) or "  - No successful examples yet."

        return f"""You are a meta-prompt engineer for DARA, an AI-powered software debugger.

CONTEXT:
  Error class    : {error_class}
  Current strategy: {current_strategy}
  Rejection rate : {failure_rate:.0%} over {sample_count} cases (too high — needs fix)

OBSERVED FAILURE REASONS from rejected fixes:
{failure_bullets}

SUCCESSFUL FIX EXAMPLES (for contrast):
{success_bullets}

YOUR TASK:
Generate {n_variants} ALTERNATIVE fix generation prompts for the '{error_class}' error class.

Each alternative must:
1. Directly address the failure reasons listed above
2. Use a DIFFERENT Chain-of-Thought structure than the current approach
3. Include specific, concrete transformation examples relevant to {error_class}
4. Be actionable — the LLM must know EXACTLY what to do step-by-step
5. Be self-contained — the prompt should work without additional context
6. End with: output the COMPLETE fixed file then a JSON object with "fixed_code" and "fix_explanation" keys

IMPORTANT CONSTRAINTS:
- Do NOT just rephrase the existing strategy; create genuinely different approaches
- Keep each prompt under 800 words
- Each variant must have a clear hypothesis about WHY it will outperform the current approach

OUTPUT FORMAT (strict JSON array, no markdown):
[
  {{
    "variant_name": "descriptive_name_for_{error_class}_v1",
    "prompt_template": "Full prompt text here...",
    "hypothesis": "This works better because..."
  }},
  {{
    "variant_name": "descriptive_name_for_{error_class}_v2",
    "prompt_template": "Full prompt text here...",
    "hypothesis": "This works better because..."
  }}
]"""

    def _parse_variants(
        self, raw: str, error_class: str, n_variants: int
    ) -> list[dict]:
        """Parse LLM JSON response into variant dicts. Robust to markdown wrapping."""
        if not raw:
            return []
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                valid = []
                for item in parsed[:n_variants]:
                    if isinstance(item, dict) and item.get("prompt_template"):
                        valid.append({
                            "variant_name": item.get("variant_name", f"{error_class}_variant"),
                            "prompt_template": item["prompt_template"],
                            "hypothesis": item.get("hypothesis", ""),
                        })
                return valid
        except json.JSONDecodeError as e:
            # Try to extract JSON array from mixed response
            import re
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    return self._parse_variants(match.group(0), error_class, n_variants)
                except Exception:
                    pass
            logger.warning("StrategyGenerator: JSON parse failed: %s", e)
        return []

    async def _save_variants(
        self, variants: list[dict], error_class: str
    ) -> list[dict]:
        """Persist generated variants to strategy_variants table as 'draft'."""
        from storage.models import StrategyVariant
        pg = self._get_pg()
        saved: list[dict] = []
        try:
            async with pg.session() as sess:
                for v in variants:
                    row = StrategyVariant(
                        id=uuid.uuid4(),
                        error_class=error_class,
                        variant_name=v["variant_name"][:200],
                        prompt_template=v["prompt_template"],
                        hypothesis=v.get("hypothesis", ""),
                        status="draft",
                        generated_by="llm",
                    )
                    sess.add(row)
                    saved.append({
                        "id": str(row.id),
                        "variant_name": row.variant_name,
                        "status": "draft",
                        "error_class": error_class,
                    })
            logger.info(
                "StrategyGenerator: saved %d draft variants for %s",
                len(saved), error_class,
            )
        except Exception as e:
            logger.warning("StrategyGenerator._save_variants: %s", e)
        return saved

    async def _fetch_failure_history(self, error_class: str) -> list[str]:
        """Fetch recent rejection reasons from pipeline_runs for this error class."""
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            sql = text("""
                SELECT pr.fix_explanation
                FROM pipeline_runs pr
                JOIN errors e ON e.id = pr.error_id
                WHERE e.error_class = :ec
                  AND pr.outcome    = 'rejected'
                  AND pr.started_at > NOW() - INTERVAL '14 days'
                ORDER BY pr.started_at DESC
                LIMIT 5
            """)
            async with pg.session() as sess:
                rows = (await sess.execute(sql, {"ec": error_class})).all()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []

    async def _fetch_success_examples(self, error_class: str) -> list[str]:
        """Fetch recent accepted fix explanations to give the LLM contrast context."""
        try:
            from sqlalchemy import text
            pg = self._get_pg()
            sql = text("""
                SELECT pr.fix_explanation
                FROM pipeline_runs pr
                JOIN errors e ON e.id = pr.error_id
                WHERE e.error_class = :ec
                  AND pr.outcome    = 'accepted'
                ORDER BY pr.started_at DESC
                LIMIT 3
            """)
            async with pg.session() as sess:
                rows = (await sess.execute(sql, {"ec": error_class})).all()
            return [r[0] for r in rows if r[0]]
        except Exception:
            return []
