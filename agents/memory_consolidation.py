"""
DARA — Memory Consolidation Agent (Week 14-15)
================================================
Writes every accepted/rejected fix outcome into the Neo4j Institutional
Memory Graph. Called by PatternMemory as a fire-and-forget async task
after every pipeline decision.

Graph mutations made on ACCEPT:
  1. MERGE ErrorPattern {signature_hash}  → increment frequency, avg_confidence
  2. MERGE FixTemplate  {template_id}     → update last_used
  3. link ErrorPattern -[:RESOLVED_BY]->  FixTemplate   (confidence, outcome)
  4. increment_pattern_acceptance (ErrorPattern counter)
  5. update_fix_template_outcome (FixTemplate acceptance counter)
  6. Optionally: MERGE CodePattern + ASSOCIATED_WITH edge (AST structural pattern)
  7. Mark pipeline_run.memory_consolidated = True

Graph mutations made on REJECT:
  1. MERGE ErrorPattern (ensure it exists)
  2. increment_pattern_rejection (auto-flips needs_refresh if rate > 0.40)
  3. update_fix_template_outcome (rejection counter)

Signature hash: sha256(error_class + "|" + root_cause_str[:200])
Template ID:    fix.fix_id or sha256(error_class + fix_explanation[:200])

Usage (called from agents/memory.py):
    from agents.memory_consolidation import MemoryConsolidationAgent
    consolidation = MemoryConsolidationAgent(neo4j=..., postgres=...)
    asyncio.create_task(consolidation.on_fix_accepted(error, fix, root_cause, bundle))
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Rejection rate that triggers needs_refresh flag on ErrorPattern
REFRESH_THRESHOLD = 0.40


@dataclass
class CodePatternData:
    """Extracted AST structural pattern from an erroring function."""
    ast_hash: str
    language: str
    pattern_type: str
    description: str


class MemoryConsolidationAgent:
    """
    Records every fix outcome into the Neo4j Institutional Memory Graph.
    Dual-writes: Neo4j graph (rich semantics) + Postgres (fast analytics).
    """

    def __init__(self, neo4j=None, postgres=None) -> None:
        self._neo4j = neo4j
        self._postgres = postgres

    def _get_neo4j(self):
        if self._neo4j:
            return self._neo4j
        from storage.neo4j_client import get_neo4j
        return get_neo4j()

    def _get_pg(self):
        if self._postgres:
            return self._postgres
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public API ─────────────────────────────────────────────

    async def on_fix_accepted(
        self,
        error: dict,
        fix,        # Fix dataclass from agent_schemas
        root_cause, # RootCauseResult
        bundle,     # ContextBundle
        pipeline_run_id: str | None = None,
    ) -> None:
        """
        Called after human/Reviewer approves a fix.
        Writes ErrorPattern + FixTemplate + RESOLVED_BY edge into Neo4j.
        """
        try:
            error_class = error.get("error_class", "unknown")
            sig_hash = self._compute_signature_hash(error, root_cause)
            template_id = self._compute_template_id(fix, error_class)
            strategy = getattr(fix, "strategy", root_cause.suggested_strategy if root_cause else "unknown")
            confidence = getattr(fix, "confidence_retained", 0.75)
            fix_explanation = getattr(fix, "fix_explanation", "")

            neo4j = self._get_neo4j()

            # 1. Upsert pattern node
            await neo4j.upsert_error_pattern(
                signature_hash=sig_hash,
                error_class=error_class,
                confidence=float(confidence),
                strategy=strategy,
            )

            # 2. Upsert template node
            await neo4j.upsert_fix_template(
                template_id=template_id,
                error_class=error_class,
                strategy=strategy,
                fix_body=fix_explanation[:2000],
            )

            # 3. Link: ErrorPattern -[:RESOLVED_BY]-> FixTemplate
            await neo4j.link_fix_to_pattern(
                signature_hash=sig_hash,
                template_id=template_id,
                confidence=float(confidence),
                outcome="accepted",
                strategy_used=strategy,
            )

            # 4. Update counters
            await neo4j.increment_pattern_acceptance(sig_hash)
            await neo4j.update_fix_template_outcome(
                template_id=template_id, accepted=1, rejected=0
            )

            # 5. Optionally extract and store code pattern
            code_pattern = self._extract_code_pattern(bundle)
            if code_pattern:
                await neo4j.upsert_code_pattern(
                    ast_hash=code_pattern.ast_hash,
                    language=code_pattern.language,
                    pattern_type=code_pattern.pattern_type,
                    description=code_pattern.description,
                )
                await neo4j.link_code_to_error(
                    ast_hash=code_pattern.ast_hash,
                    signature_hash=sig_hash,
                )

            # 6. Mark pipeline run as consolidated
            if pipeline_run_id:
                await self._mark_consolidated(pipeline_run_id)

            logger.info(
                "MemoryConsolidation: ACCEPTED %s sig=%s template=%s",
                error_class, sig_hash[:12], template_id[:12],
            )

        except Exception as e:
            logger.warning("MemoryConsolidationAgent.on_fix_accepted failed: %s", e)

    async def on_fix_rejected(
        self,
        error: dict,
        fix,
        root_cause,
        reason: str,
        pipeline_run_id: str | None = None,
    ) -> None:
        """
        Called after human/Reviewer rejects a fix.
        Increments rejection counter. Auto-flags pattern if rate > 40%.
        """
        try:
            error_class = error.get("error_class", "unknown")
            sig_hash = self._compute_signature_hash(error, root_cause)
            template_id = self._compute_template_id(fix, error_class)

            neo4j = self._get_neo4j()

            # Ensure pattern node exists
            strategy = getattr(fix, "strategy", "unknown") if fix else "unknown"
            confidence = getattr(fix, "confidence_retained", 0.0) if fix else 0.0
            await neo4j.upsert_error_pattern(
                signature_hash=sig_hash,
                error_class=error_class,
                confidence=float(confidence),
                strategy=strategy,
            )

            # Increment rejection (auto-flags needs_refresh at threshold)
            await neo4j.increment_pattern_rejection(
                sig_hash, threshold=REFRESH_THRESHOLD
            )

            # Update template rejection counter if template exists
            if template_id:
                await neo4j.update_fix_template_outcome(
                    template_id=template_id, accepted=0, rejected=1
                )

            # Link pattern to template with rejected outcome
            if template_id:
                await neo4j.link_fix_to_pattern(
                    signature_hash=sig_hash,
                    template_id=template_id,
                    confidence=float(confidence),
                    outcome="rejected",
                    strategy_used=strategy,
                )

            # Mark consolidated
            if pipeline_run_id:
                await self._mark_consolidated(pipeline_run_id)

            logger.info(
                "MemoryConsolidation: REJECTED %s sig=%s reason=%s",
                error_class, sig_hash[:12], reason[:100],
            )

        except Exception as e:
            logger.warning("MemoryConsolidationAgent.on_fix_rejected failed: %s", e)

    # ── Private helpers ────────────────────────────────────────

    def _compute_signature_hash(self, error: dict, root_cause) -> str:
        """
        Deterministic hash of (error_class, root_cause_summary).
        Same logical bug always maps to the same node in Neo4j.
        """
        error_class = error.get("error_class", "unknown")
        rc_str = ""
        if root_cause:
            rc_str = getattr(root_cause, "root_cause", str(root_cause))[:200]
        raw = f"{error_class}|{rc_str}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _compute_template_id(self, fix, error_class: str) -> str:
        """
        Derives a stable template ID from the fix.
        Uses fix.fix_id if available (UUID), else hashes the explanation.
        """
        if fix is None:
            return hashlib.sha256(
                f"{error_class}|no_fix".encode()
            ).hexdigest()[:20]
        fix_id = getattr(fix, "fix_id", None) or getattr(fix, "error_id", None)
        if fix_id:
            return str(fix_id)[:36]
        explanation = getattr(fix, "fix_explanation", "")[:200]
        return hashlib.sha256(
            f"{error_class}|{explanation}".encode()
        ).hexdigest()[:20]

    def _extract_code_pattern(self, bundle) -> CodePatternData | None:
        """
        Extract a structural AST pattern from the erroring code snippet.
        Returns None if code is unavailable or too short to analyze.
        """
        if not bundle:
            return None
        code = getattr(bundle, "erroring_code", None)
        language = getattr(bundle, "language", "python") or "python"
        if not code or len(code.strip()) < 20:
            return None
        try:
            # Structural hash: hash the AST node types string
            # (avoids variable name noise; two structurally identical
            #  functions always produce the same hash even if renamed)
            import ast as pyast
            tree = pyast.parse(code)
            node_types = "|".join(type(n).__name__ for n in pyast.walk(tree))
            ast_hash = hashlib.sha256(node_types.encode()).hexdigest()[:24]

            # Classify pattern type heuristically
            pattern_type = "generic"
            if "Try" in node_types:
                pattern_type = "try_except"
            elif "With" in node_types:
                pattern_type = "context_manager"
            elif "AsyncFunctionDef" in node_types:
                pattern_type = "async_function"
            elif "ClassDef" in node_types:
                pattern_type = "class_method"

            func_name = getattr(bundle, "erroring_function", None) or "unknown"
            return CodePatternData(
                ast_hash=ast_hash,
                language=language,
                pattern_type=pattern_type,
                description=f"{pattern_type} in {func_name}",
            )
        except Exception:
            # Non-Python code or parse error — use raw line hash
            line_hash = hashlib.sha256(code[:500].encode()).hexdigest()[:24]
            return CodePatternData(
                ast_hash=line_hash,
                language=language,
                pattern_type="raw",
                description=f"raw/{language} pattern",
            )

    async def _mark_consolidated(self, pipeline_run_id: str) -> None:
        """Update pipeline_runs.memory_consolidated = true."""
        try:
            from sqlalchemy import update

            from storage.models import PipelineRun
            pg = self._get_pg()
            async with pg.session() as sess:
                await sess.execute(
                    update(PipelineRun)
                    .where(PipelineRun.id == uuid.UUID(pipeline_run_id))
                    .values(
                        memory_consolidated=True,
                        memory_consolidated_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as e:
            logger.debug("_mark_consolidated: %s", e)


def get_consolidation_agent(neo4j=None, postgres=None) -> MemoryConsolidationAgent:
    """Factory — lazy-instantiate shared agent."""
    return MemoryConsolidationAgent(neo4j=neo4j, postgres=postgres)
