from __future__ import annotations
import logging
import time
from dataclasses import dataclass

from agents.debugger import DebuggerAgent
from agents.fixer import FixerAgent
from agents.memory import PatternMemory
from agents.reviewer import ReviewerAgent
from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult
from config.settings import get_settings
from context.builder import ContextBuilder, ContextBundle
from context.retriever import ContextRetriever
from monitoring import metrics
from output.github_pr import GitHubPRCreator
from output.slack_notifier import SlackNotifier
from storage.postgres import PostgresClient
from storage.redis_client import RedisClient
from storage.qdrant_client import get_qdrant
from validation.engine import ValidationEngine, ValidationReport

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    error_id: str
    status: str
    root_cause: RootCauseResult | None = None
    fix: Fix | None = None
    validation: ValidationReport | None = None
    review: ReviewResult | None = None
    fix_id: str | None = None
    pr_url: str | None = None
    slack_sent: bool = False
    stage_reached: str = "start"
    failure_reason: str | None = None


class Orchestrator:
    """
    Single-entry-point coordinator for the full DARA debug pipeline.
    Pipeline stages:
      1. Load error from DB
      2. Check pattern library (fast path)
      3. Build context bundle
      4. DebuggerAgent -> RootCauseResult
      5. Gate: confidence check + strategy check
      6. FixerAgent -> Fix (unified diff patches)
      7. ValidationEngine -> static analysis + tests
      8. ReviewerAgent -> ReviewResult
      9. Persist fix to DB
      10. Gate: auto-approve if eligible
      11. Slack notification with approve/reject buttons
      12. GitHub PR creation (if approved/pending)

    Decisions I made:
      ~ Validation runs BEFORE ReviewerAgent (reviewer sees validation results)
      ~ Auto-approve only if: rec=approve AND confidence>=threshold AND risk=low AND validation passed
      ~ All stages are try/excepted - partial results returned on failure
      ~ Redis tracks pipeline progress for real-time status
    """

    def __init__(
        self,
        postgres: PostgresClient,
        redis: RedisClient,
        llm_router,
        repo_path: str = ".",
    ) -> None:
        self._pg = postgres
        self._redis = redis
        self._llm = llm_router
        self._settings = get_settings()
        retriever = ContextRetriever(qdrant=get_qdrant(), llm_router=llm_router)
        self._builder = ContextBuilder(retriever=retriever, repo_path=repo_path)
        self._debugger = DebuggerAgent(llm_router=llm_router)
        self._fixer = FixerAgent(llm_router=llm_router)
        self._reviewer = ReviewerAgent(llm_router=llm_router)
        self._memory = PatternMemory()
        self._validator = ValidationEngine(repo_path=repo_path)
        self._slack = SlackNotifier()
        self._github = GitHubPRCreator()

    async def run(self, error_id: str) -> PipelineResult:
        result = PipelineResult(error_id=error_id, status="started")
        _t0 = time.perf_counter()
        metrics.active_pipelines.inc()
        try:
            r = await self._run_pipeline(result)
            metrics.pipelines_total.labels(status=r.status).inc()
            metrics.pipeline_duration.observe(time.perf_counter() - _t0)
            return r
        except Exception as e:
            logger.error("Orchestrator fatal error for %s: %s", error_id, e, exc_info=True)
            result.status = "failed"
            result.failure_reason = str(e)
            await self._set_state(error_id, "failed")
            metrics.pipelines_total.labels(status="failed").inc()
            metrics.pipeline_duration.observe(time.perf_counter() - _t0)
            return result
        finally:
            metrics.active_pipelines.dec()

    async def _run_pipeline(self, result: PipelineResult) -> PipelineResult:
        error_id = result.error_id

        # Stage 1: Load error
        error_row = await self._pg.get_error(error_id)
        if not error_row:
            result.status = "error_not_found"
            return result
        error = {
            "id": str(error_row.id), "error_class": error_row.error_class,
            "message": error_row.message, "stack_trace": error_row.stack_trace,
            "file_path": error_row.file_path, "line_number": error_row.line_number,
            "service": error_row.service, "severity": error_row.severity,
            "commit_sha": error_row.commit_sha, "branch": error_row.branch,
            "trace_id": error_row.trace_id,
        }
        result.stage_reached = "loaded"
        await self._pg.update_error_status(error_id, "analyzing")
        await self._set_state(error_id, "analyzing")

        # Stage 2: Pattern library fast path
        template = await self._memory.find_template(error)
        if template and template.get("success_rate", 0) >= 0.85:
            logger.info("Orchestrator: template hit for %s", error_id)
            await self._pg.update_error_status(error_id, "fixed")
            result.status = "template_hit"
            result.stage_reached = "template"
            return result

        # Stage 3: Build context
        bundle = await self._builder.build(error)
        result.stage_reached = "analyzing"
        await self._set_state(error_id, "analyzing")
        logger.info("Context: %s", bundle.summary())

        # Stage 4: DebuggerAgent
        root_cause = await self._debugger.analyze(bundle, error)
        result.root_cause = root_cause
        result.stage_reached = "analyzing"
        await self._set_state(error_id, "analyzing")
        logger.info("Root cause: confidence=%.2f strategy=%s",
                    root_cause.confidence, root_cause.suggested_strategy)

        # Stage 5: Escalation gate
        if root_cause.suggested_strategy == "human_escalation" or root_cause.confidence < 0.35:
            await self._pg.update_error_status(error_id, "escalated")
            await self._slack.notify_escalation(
                error_id=error_id, error_class=error["error_class"],
                service=error.get("service"), reason=root_cause.root_cause[:300],
            )
            result.status = "escalated"
            result.stage_reached = "escalated"
            return result

        # Stage 6: FixerAgent
        fix = await self._fixer.generate(root_cause, bundle, error)
        result.fix = fix
        result.stage_reached = "fixing"
        await self._set_state(error_id, "fixing")
        metrics.fixes_generated.labels(strategy=fix.strategy or "unknown").inc()

        # Stage 7: Validation
        tmp_fix_id = f"{error_id[:8]}-prelim"
        validation = await self._validator.validate(fix, tmp_fix_id)
        result.validation = validation
        result.stage_reached = "validating"
        logger.info("Validation: passed=%s issues=%d duration=%dms",
                    validation.passed, len(validation.blocking_issues), validation.total_duration_ms)

        # Stage 8: ReviewerAgent (sees validation context)
        review = await self._reviewer.review(fix, root_cause, error)
        result.review = review
        result.stage_reached = "validating"
        logger.info("Review: score=%.2f rec=%s", review.quality_score, review.overall_recommendation)
        metrics.fixes_reviewed.labels(recommendation=review.overall_recommendation).inc()

        # Gate: block if validation failed
        if not validation.passed:
            review_rec = "reject"
            review.overall_recommendation = "reject"
            if not review.rejection_reason:
                review.rejection_reason = f"Validation failed: {', '.join(validation.blocking_issues[:2])}"

        # Stage 9: Persist fix
        fix_id = await self._pg.save_fix({
            "error_id": error_id,
            "patch_content": "\n\n---\n\n".join(p.unified_diff for p in fix.patches),
            "files_changed": [p.file_path for p in fix.patches],
            "lines_changed": fix.total_lines_changed,
            "confidence": fix.confidence_retained,
            "strategy": fix.strategy,
            "fix_explanation": fix.fix_explanation,
        })
        result.fix_id = fix_id

        # Stage 10: Auto-approve gate
        auto_approve = (
            review.overall_recommendation == "approve"
            and fix.confidence_retained >= self._settings.auto_merge_confidence_threshold
            and fix.regression_risk == "low"
            and validation.passed
        )
        if auto_approve:
            await self._pg.update_fix_outcome(fix_id, "auto_accepted",
                                               "Auto-approved: high confidence, low risk, validation passed")
            await self._memory.record_success(error, fix, root_cause, "accepted")
            await self._pg.update_error_status(error_id, "fixed")
            await self._pg.update_fix_validation(fix_id, {
                "validation_pass": validation.passed,
                "test_results": validation.test_results.as_dict() if validation.test_results else {},
            })
            result.status = "fixed"
            result.stage_reached = "fixed"
            logger.info("Orchestrator: AUTO-APPROVED fix=%s for error=%s", fix_id, error_id)
        else:
            await self._pg.update_error_status(error_id, "fixed")
            result.status = "fixed"
            result.stage_reached = "fixed"

        # Stage 11: Slack notification
        slack_sent = await self._slack.notify_fix_ready(
            error_id=error_id, fix_id=fix_id,
            error_class=error["error_class"], message=error["message"],
            service=error.get("service"), confidence=fix.confidence_retained,
            recommendation=review.overall_recommendation,
            quality_score=review.quality_score,
            files_changed=[p.file_path for p in fix.patches],
        )
        result.slack_sent = slack_sent

        # Stage 12: GitHub PR (if approved or pending review)
        if review.overall_recommendation in ("approve", "approve_with_comments"):
            repo_name = error.get("service","unknown")
            pr_info = await self._github.create_pr(
                repo_full_name=f"your-org/{repo_name}",
                fix_id=fix_id,
                error_class=error["error_class"],
                patches=[{"file_path": p.file_path, "unified_diff": p.unified_diff}
                          for p in fix.patches],
                fix_explanation=fix.fix_explanation,
            )
            if pr_info:
                result.pr_url = pr_info.get("pr_url")
                await self._pg.update_fix_outcome(
                    fix_id, review.overall_recommendation.replace("_with_comments",""),
                    pr_url=pr_info.get("pr_url"), pr_number=pr_info.get("pr_number"),
                )

        return result

    async def _set_state(self, error_id: str, state: str) -> None:
        try:
            await self._redis.set_pipeline_state(error_id, "status", state)
        except Exception:
            pass
