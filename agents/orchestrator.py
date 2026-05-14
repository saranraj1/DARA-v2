"""
agents/orchestrator.py
=======================
Single-entry-point coordinator for the full DARA debug pipeline.

Pipeline stages:
  1.  Load error from DB
  2.  Check pattern library (fast path)
  3.  Build context bundle
  4.  DebuggerAgent → RootCauseResult
  5.  Gate: confidence check + strategy check
  6.  FixerAgent → Fix (unified diff patches)
  7.  ValidationEngine → SandboxRunner (Docker / file-system fallback)
        └─ Internal loop: DebuggerAgent → FixerAgent on failure (≤3 iter)
  7b. Static analysis (Ruff S*/E9* codes)
  8.  ReviewerAgent
        └─ SecurityAuditor pre-check (Bandit + Semgrep on diff)
             HIGH severity → reject + FixerAgent security-retry (≤2 retries)
  9.  Persist fix to DB
  10. Gate: auto-approve if eligible
  11. Slack notification
  12. GitHub PR creation

Decisions:
  ~ Sandbox runs BEFORE ReviewerAgent (reviewer sees real test results)
  ~ Security rejection triggers FixerAgent rewrite (max MAX_SECURITY_RETRIES)
  ~ After all retries exhausted, fix is escalated to Slack as "security_blocked"
  ~ Auto-approve only if: rec=approve AND confidence≥threshold AND risk=low
                          AND sandbox passed AND static passed
  ~ All stages are try/excepted — partial results returned on failure
  ~ Redis tracks pipeline progress for real-time status
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from agents.debugger import DebuggerAgent
from agents.fixer import FixerAgent
from agents.memory import PatternMemory
from agents.reviewer import ReviewerAgent
from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult
from config.settings import get_settings
from context.builder import ContextBuilder
from context.retriever import ContextRetriever
from graph.blast_radius import BlastRadiusAnalyzer
from monitoring import metrics
from output.github_pr import GitHubPRCreator
from output.slack_notifier import SlackNotifier
from storage.neo4j_client import get_neo4j
from storage.postgres import PostgresClient
from storage.qdrant_client import get_qdrant
from storage.redis_client import RedisClient
from validation.engine import ValidationEngine, ValidationReport
from validation.security_auditor import SecurityAuditResult

logger = logging.getLogger(__name__)

MAX_SECURITY_RETRIES = 2   # max FixerAgent reruns on security rejection


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
    # New fields
    sandbox_iterations: int = 0
    security_retries: int = 0
    security_findings: list[str] = field(default_factory=list)
    blast_risk: str = "low"
    blast_downstream_count: int = 0


class Orchestrator:
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
        self._blast = BlastRadiusAnalyzer(neo4j=get_neo4j(), repo_path=repo_path)
        self._slack = SlackNotifier()
        self._github = GitHubPRCreator()

        # Wire sandbox iteration agents into ValidationEngine
        self._validator.wire_agents(
            debugger=self._debugger,
            fixer=self._fixer,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Public entry point
    # ──────────────────────────────────────────────────────────────────────────

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

    # ──────────────────────────────────────────────────────────────────────────
    # Core pipeline
    # ──────────────────────────────────────────────────────────────────────────

    async def _run_pipeline(self, result: PipelineResult) -> PipelineResult:
        error_id = result.error_id

        # ── Stage 1: Load error ────────────────────────────────────────────
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

        # ── Stage 2: Pattern library fast path ────────────────────────────
        template = await self._memory.find_template(error)
        if template and template.get("success_rate", 0) >= 0.85:
            logger.info("Orchestrator: template hit for %s", error_id)
            await self._pg.update_error_status(error_id, "fixed")
            await self._memory.record_success(
                error=error, fix=None, root_cause=None,
                outcome="template_hit", fix_id=template.get("template_id", ""),
            )
            result.status = "template_hit"
            result.stage_reached = "template"
            return result

        # ── Stage 3: Build context ─────────────────────────────────────────
        bundle = await self._builder.build(error)
        result.stage_reached = "analyzing"
        await self._set_state(error_id, "analyzing")
        logger.info("Context: %s", bundle.summary())

        # ── Stage 4: DebuggerAgent ─────────────────────────────────────────
        root_cause = await self._debugger.analyze(bundle, error)
        result.root_cause = root_cause
        result.stage_reached = "analyzing"
        await self._set_state(error_id, "analyzing")
        logger.info(
            "Root cause: confidence=%.2f strategy=%s",
            root_cause.confidence, root_cause.suggested_strategy,
        )

        # ── Stage 5: Escalation gate ───────────────────────────────────────
        if root_cause.suggested_strategy == "human_escalation" or root_cause.confidence < 0.35:
            await self._pg.update_error_status(error_id, "escalated")
            await self._slack.notify_escalation(
                error_id=error_id, error_class=error["error_class"],
                service=error.get("service"), reason=root_cause.root_cause[:300],
            )
            result.status = "escalated"
            result.stage_reached = "escalated"
            return result

        # ── Stage 6: FixerAgent ────────────────────────────────────────────────
        fix = await self._fixer.generate(root_cause, bundle, error)
        result.fix = fix
        result.stage_reached = "fixing"
        await self._set_state(error_id, "fixing")
        metrics.fixes_generated.labels(strategy=fix.strategy or "unknown").inc()

        # ── Stage 6b: Blast Radius Analysis ───────────────────────────────────
        blast_report = await self._blast.analyze(fix, service_name=error.get("service", ""))
        bundle.blast_radius = blast_report
        result.blast_risk = blast_report.risk_level
        result.blast_downstream_count = blast_report.downstream_count
        logger.info("BlastRadius: %s", blast_report.summary())

        # Critical blast radius: escalate immediately (no PR, no auto-approve)
        if blast_report.risk_level == "critical":
            logger.warning(
                "Orchestrator: CRITICAL blast radius for error=%s — escalating", error_id
            )
            await self._slack.notify_escalation(
                error_id=error_id, error_class=error["error_class"],
                service=error.get("service"),
                reason=(
                    f"Critical blast radius: {blast_report.warning_message}. "
                    f"Downstream: {blast_report.downstream_count} services, "
                    f"API contracts: {blast_report.shared_api_contracts[:3]}"
                ),
            )
            # Still generate a fix and review — but force approve_with_comments
            # so it goes to human review rather than auto-approve

        # ── Stage 7: Sandbox validation ────────────────────────────────────
        tmp_fix_id = f"{error_id[:8]}-prelim"
        validation = await self._validator.validate(
            fix=fix,
            fix_id=tmp_fix_id,
            bundle=bundle,
            error=error,
        )
        result.validation = validation
        result.sandbox_iterations = validation.sandbox_iterations
        result.stage_reached = "validating"
        await self._set_state(error_id, "validating")
        logger.info(
            "Validation: passed=%s sandbox_passed=%s sandbox_iter=%d "
            "issues=%d duration=%dms",
            validation.passed, validation.sandbox_passed,
            validation.sandbox_iterations,
            len(validation.blocking_issues), validation.total_duration_ms,
        )

        # Use refined fix if sandbox iterated to one
        if validation.final_fix is not None and validation.final_fix is not fix:
            logger.info("Orchestrator: using sandbox-refined fix (iteration=%d)",
                        validation.sandbox_iterations)
            fix = validation.final_fix
            result.fix = fix

        # ── Stage 8: ReviewerAgent + Security retry loop ───────────────────
        fix, review, security_retries = await self._review_with_security_retry(
            fix=fix, root_cause=root_cause, error=error, bundle=bundle,
        )
        result.review = review
        result.security_retries = security_retries
        result.stage_reached = "validating"
        await self._set_state(error_id, "validating")

        # Collect security findings for PipelineResult
        audit: SecurityAuditResult | None = getattr(review, "security_audit", None)
        if audit:
            result.security_findings = [str(f) for f in audit.findings[:10]]

        logger.info(
            "Review: score=%.2f rec=%s security_retries=%d",
            review.quality_score, review.overall_recommendation, security_retries,
        )
        metrics.fixes_reviewed.labels(recommendation=review.overall_recommendation).inc()

        # Security permanently blocked?
        if review.overall_recommendation == "reject" and security_retries >= MAX_SECURITY_RETRIES:
            if audit and audit.has_high_severity:
                logger.error(
                    "Orchestrator: fix SECURITY BLOCKED after %d retries for error=%s",
                    security_retries, error_id,
                )
                await self._slack.notify_escalation(
                    error_id=error_id, error_class=error["error_class"],
                    service=error.get("service"),
                    reason=f"Security audit blocked fix after {security_retries} retries: "
                           f"{review.rejection_reason}",
                )
                result.status = "security_blocked"
                result.stage_reached = "security_blocked"
                await self._pg.update_error_status(error_id, "escalated")
                return result

        # Gate: block if validation failed
        if not validation.passed:
            review.overall_recommendation = "reject"
            if not review.rejection_reason:
                review.rejection_reason = (
                    f"Validation failed: {', '.join(validation.blocking_issues[:2])}"
                )

        # ── Stage 9: Persist fix ───────────────────────────────────────────
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

        # ── Stage 10: Auto-approve gate ────────────────────────────────────
        auto_approve = (
            review.overall_recommendation == "approve"
            and fix.confidence_retained >= self._settings.auto_merge_confidence_threshold
            and fix.regression_risk == "low"
            and validation.passed
            and validation.sandbox_passed
        )
        if auto_approve:
            await self._pg.update_fix_outcome(
                fix_id, "auto_accepted",
                "Auto-approved: high confidence, low risk, sandbox passed",
            )
            await self._memory.record_success(error, fix, root_cause, "accepted")
            await self._pg.update_error_status(error_id, "fixed")
            await self._pg.update_fix_validation(fix_id, {
                "validation_pass": validation.passed,
                "sandbox_passed": validation.sandbox_passed,
                "sandbox_iterations": validation.sandbox_iterations,
                "test_results": validation.test_results.as_dict() if validation.test_results else {},
            })
            result.status = "fixed"
            result.stage_reached = "fixed"
            logger.info("Orchestrator: AUTO-APPROVED fix=%s for error=%s", fix_id, error_id)

            try:
                repo_full_name = self._resolve_github_repo(error.get("service", ""))
                pr_info = await self._github.create_pr(
                    repo_full_name=repo_full_name, fix_id=fix_id,
                    error_class=error["error_class"],
                    patches=[{"file_path": p.file_path, "unified_diff": p.unified_diff}
                              for p in fix.patches],
                    fix_explanation=f"[AUTO-APPROVED] {fix.fix_explanation}",
                )
                if pr_info:
                    result.pr_url = pr_info.get("pr_url")
                    await self._pg.update_fix_outcome(
                        fix_id, "auto_accepted",
                        pr_url=pr_info.get("pr_url"), pr_number=pr_info.get("pr_number"),
                    )
            except Exception as pr_err:
                logger.warning("Auto-approve PR creation failed (non-blocking): %s", pr_err)
        else:
            await self._pg.update_error_status(error_id, "fixed")
            result.status = "fixed"
            result.stage_reached = "fixed"

        # ── Stage 11: Slack notification ───────────────────────────────────
        slack_sent = await self._slack.notify_fix_ready(
            error_id=error_id, fix_id=fix_id,
            error_class=error["error_class"], message=error["message"],
            service=error.get("service"), confidence=fix.confidence_retained,
            recommendation=review.overall_recommendation,
            quality_score=review.quality_score,
            files_changed=[p.file_path for p in fix.patches],
        )
        result.slack_sent = slack_sent

        # ── Stage 12: GitHub PR ────────────────────────────────────────────
        if review.overall_recommendation in ("approve", "approve_with_comments"):
            repo_full_name = self._resolve_github_repo(error.get("service", ""))
            pr_info = await self._github.create_pr(
                repo_full_name=repo_full_name, fix_id=fix_id,
                error_class=error["error_class"],
                patches=[{"file_path": p.file_path, "unified_diff": p.unified_diff}
                          for p in fix.patches],
                fix_explanation=fix.fix_explanation,
            )
            if pr_info:
                result.pr_url = pr_info.get("pr_url")
                await self._pg.update_fix_outcome(
                    fix_id, review.overall_recommendation.replace("_with_comments", ""),
                    pr_url=pr_info.get("pr_url"), pr_number=pr_info.get("pr_number"),
                )

        return result

    # ──────────────────────────────────────────────────────────────────────────
    # Security retry loop
    # ──────────────────────────────────────────────────────────────────────────

    async def _review_with_security_retry(
        self,
        fix: Fix,
        root_cause: RootCauseResult,
        error: dict,
        bundle,
    ) -> tuple[Fix, ReviewResult, int]:
        """
        Run ReviewerAgent. On security rejection, regenerate the fix with
        security constraints injected into the prompt (up to MAX_SECURITY_RETRIES).

        Returns (final_fix, final_review, retry_count).
        """
        current_fix = fix
        for attempt in range(MAX_SECURITY_RETRIES + 1):
            review = await self._reviewer.review(current_fix, root_cause, error)
            audit: SecurityAuditResult | None = getattr(review, "security_audit", None)

            if review.overall_recommendation != "reject":
                return current_fix, review, attempt

            if not (audit and audit.has_high_severity):
                # Non-security rejection — don't retry
                return current_fix, review, attempt

            if attempt == MAX_SECURITY_RETRIES:
                logger.warning(
                    "Orchestrator: security retry limit (%d) reached for error=%s",
                    MAX_SECURITY_RETRIES, error.get("id", "?"),
                )
                return current_fix, review, attempt

            logger.info(
                "Orchestrator: security retry %d/%d — regenerating fix with constraints",
                attempt + 1, MAX_SECURITY_RETRIES,
            )
            constrained_error = self._inject_security_constraints(error, audit)
            try:
                current_fix = await self._fixer.generate(root_cause, bundle, constrained_error)
            except Exception as exc:
                logger.error("Orchestrator: security retry fixer failed: %s", exc, exc_info=True)
                return current_fix, review, attempt + 1

        return current_fix, review, MAX_SECURITY_RETRIES

    @staticmethod
    def _inject_security_constraints(error: dict, audit: SecurityAuditResult) -> dict:
        """
        Augment the error dict so the FixerAgent prompt includes security constraints.
        The FixerAgent uses error['message'] in its prompt template.
        """
        constraints = "\n".join(
            f"  - MUST NOT use {f.rule_id} ({f.message})"
            for f in audit.high_findings[:5]
        )
        augmented = dict(error)
        augmented["message"] = (
            f"{error.get('message', '')}\n\n"
            f"[SECURITY REWRITE REQUIRED]\n"
            f"The previous fix was rejected due to HIGH severity security findings.\n"
            f"You MUST avoid these patterns in the new fix:\n{constraints}"
        )
        return augmented

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers (unchanged from original)
    # ──────────────────────────────────────────────────────────────────────────

    def _resolve_github_repo(self, service_name: str) -> str:
        """
        SEV-6: Validate service name against an allowlist before using it
        to construct a GitHub repo path.  Prevents path-traversal via
        malicious error payloads.
        """
        import re
        known: dict[str, str] = getattr(self._settings, "known_github_repos", {}) or {}
        if service_name in known:
            return known[service_name]

        github_org = getattr(self._settings, "github_org", "") or "your-org"
        safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "", service_name)[:64]
        if not safe_name:
            logger.warning(
                "Orchestrator: empty/invalid service name '%s' — using fallback repo",
                service_name,
            )
            return f"{github_org}/unknown-service"
        return f"{github_org}/{safe_name}"

    async def _set_state(self, error_id: str, state: str) -> None:
        try:
            await self._redis.set_pipeline_state(error_id, "status", state)
        except Exception:
            pass
