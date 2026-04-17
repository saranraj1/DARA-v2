from __future__ import annotations
import logging
from api.models.agent_schemas import Fix, RootCauseResult
from storage.postgres import get_postgres

logger = logging.getLogger(__name__)


class PatternMemory:
    """
    Manages pattern_library: known error patterns + fix templates.
    find_template() -> quick path for seen errors (skips LLM agents).
    record_success() -> builds institutional memory from accepted fixes.
    """

    async def find_template(self, error: dict) -> dict | None:
        try:
            t = await get_postgres().get_fix_template(
                error_class=error.get("error_class",""),
                service=error.get("service"),
            )
            if t:
                logger.info("PatternMemory: hit for %s (rate=%.2f)",
                            error.get("error_class"), float(t.success_rate or 0))
                return {"template_id": str(t.id), "fix_template": t.fix_template,
                        "success_rate": float(t.success_rate or 0), "example_fix": t.example_fix}
        except Exception as e:
            logger.warning("PatternMemory.find_template: %s", e)
        return None

    async def record_success(self, error: dict, fix: Fix,
                              root_cause: RootCauseResult, outcome: str = "accepted") -> None:
        if outcome != "accepted" or fix.confidence_retained < 0.7:
            return
        try:
            await get_postgres().upsert_pattern({
                "error_class": error.get("error_class",""),
                "service": error.get("service"),
                "root_cause_pattern": root_cause.root_cause[:500],
                "fix_strategy": root_cause.suggested_strategy,
                "confidence_threshold": root_cause.confidence,
                "example_fix": fix.fix_explanation,
            })
            logger.info("PatternMemory: stored pattern for %s", error.get("error_class"))
        except Exception as e:
            logger.warning("PatternMemory.record_success: %s", e)
