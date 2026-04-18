from __future__ import annotations
import logging
from api.models.agent_schemas import Fix, RootCauseResult

logger = logging.getLogger(__name__)


class PatternMemory:
    """
    Manages pattern_library: known error patterns + fix templates.
    find_template() -> quick path for seen errors (skips LLM agents).
    record_success() -> builds institutional memory from accepted fixes.
    record_failure() -> tracks rejected/failed fixes to avoid repeating mistakes.
    """

    def __init__(self, postgres=None, llm_router=None) -> None:
        self._pg = postgres
        self._llm = llm_router

    def _get_pg(self):
        if self._pg:
            return self._pg
        from storage.postgres import get_postgres
        return get_postgres()

    async def find_template(self, error: dict) -> dict | None:
        try:
            t = await self._get_pg().get_fix_template(
                error_class=error.get("error_class", ""),
                service=error.get("service"),
            )
            if t:
                logger.info(
                    "PatternMemory: hit for %s (rate=%.2f)",
                    error.get("error_class"), float(t.success_rate or 0)
                )
                return {
                    "template_id": str(t.id),
                    "fix_template": t.fix_template,
                    "success_rate": float(t.success_rate or 0),
                    "example_fix": t.example_fix,
                }
        except Exception as e:
            logger.warning("PatternMemory.find_template: %s", e)
        return None

    async def record_success(
        self,
        # Agent-object form (from Orchestrator)
        error: dict | None = None,
        fix: Fix | None = None,
        root_cause: RootCauseResult | None = None,
        outcome: str = "accepted",
        # String form (from HITL webhook)
        error_class: str = "",
        root_cause_str: str = "",
        fix_summary: str = "",
        fix_id: str = "",
    ) -> None:
        """Record a successful fix into the pattern library."""
        ec = error.get("error_class", "") if error else error_class
        rc = root_cause.root_cause[:500] if root_cause else root_cause_str[:500]
        strat = root_cause.suggested_strategy if root_cause else "llm_single_file"
        conf = root_cause.confidence if root_cause else 0.75
        example = fix.fix_explanation if fix else fix_summary
        svc = error.get("service") if error else None

        if fix and fix.confidence_retained < 0.7:
            return
        if not ec:
            return

        try:
            await self._get_pg().upsert_pattern({
                "error_class": ec,
                "service": svc,
                "root_cause_pattern": rc,
                "fix_strategy": strat,
                "confidence_threshold": conf,
                "example_fix": example,
            })
            logger.info("PatternMemory: stored success pattern for %s (fix_id=%s)", ec, fix_id)
        except Exception as e:
            logger.warning("PatternMemory.record_success: %s", e)

    async def record_failure(
        self,
        error_class: str,
        root_cause: str,
        failure_reason: str,
        fix_id: str = "",
    ) -> None:
        """Track rejected fixes. Lowers success_rate for this pattern."""
        try:
            pg = self._get_pg()
            t = await pg.get_fix_template(error_class=error_class, service=None)
            if t:
                await pg.upsert_pattern({
                    "error_class": error_class,
                    "service": None,
                    "root_cause_pattern": root_cause[:500],
                    "fix_strategy": "human_escalation",
                    "confidence_threshold": 0.0,
                    "example_fix": f"REJECTED: {failure_reason[:200]}",
                })
            logger.info(
                "PatternMemory: recorded failure for %s (fix_id=%s) reason=%s",
                error_class, fix_id, failure_reason[:80],
            )
        except Exception as e:
            logger.warning("PatternMemory.record_failure: %s", e)
