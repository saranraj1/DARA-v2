"""
DARA — Admin REST API
======================
Internal API for dashboard, operations, and Grafana.
All endpoints require the X-Admin-Token header.

Endpoints:
  GET  /api/v1/admin/stats                     — pipeline KPIs
  GET  /api/v1/admin/errors                    — paginated errors list (filterable)
  GET  /api/v1/admin/fixes                     — paginated fixes list (filterable)
  GET  /api/v1/admin/patterns                  — active pattern library entries
  POST /api/v1/admin/reindex                   — trigger Qdrant re-indexing
  GET  /api/v1/admin/pipeline/{error_id}       — full pipeline state for one error
  GET  /api/v1/admin/audit                     — last N audit events
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.security import APIKeyHeader

from config.settings import get_settings
from storage.postgres import get_postgres
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_admin_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def _require_admin(
    request: "Request",
    token: str | None = Depends(_admin_key_header),
) -> None:
    """
    Accepts either:
      a) A valid X-Admin-Token header (Swagger UI / legacy scripts), or
      b) A JWT Bearer token already validated by verify_token middleware
         (request.state.jwt_user is set in that case).
    """
    if getattr(request.state, "jwt_user", None):
        return  # JWT already validated by middleware — allow

    settings = get_settings()
    expected = getattr(settings, "admin_api_key", None) or "dara-admin-secret"
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token",
        )


def _get_org_id(request: "Request") -> str:
    """
    Extract the active org_id for multi-tenant DB scoping.
    Priority: X-Org-Id header → JWT org claim → default.
    Frontend api.ts always sends X-Org-Id from localStorage.
    """
    org = request.headers.get("X-Org-Id", "").strip()
    if org:
        return org
    # Fallback: JWT org claim attached by verify_token middleware
    org = getattr(request.state, "jwt_org", "").strip()
    if org:
        return org
    return get_settings().default_org_id


# ── Helpers ────────────────────────────────────────────────────

async def _cached_stats(redis, postgres) -> dict:
    """Try Redis cache first, fall back to DB query."""
    import json
    try:
        raw = await redis.client.get("dara:stats:cache")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return await postgres.get_pipeline_stats()


# ── Endpoints ──────────────────────────────────────────────────

@router.get("/stats", summary="Pipeline KPI stats", dependencies=[Depends(_require_admin)])
async def admin_stats() -> dict:
    """Returns total errors, fixes, accepted fixes, avg confidence, and active patterns."""
    postgres = get_postgres()
    redis = get_redis()
    stats = await _cached_stats(redis, postgres)

    # Add pattern count
    from sqlalchemy import func, select

    from storage.models import PatternLibrary
    async with postgres.session() as sess:
        pattern_count = await sess.scalar(
            select(func.count(PatternLibrary.id)).where(PatternLibrary.is_active == True)
        )
    stats["active_patterns"] = pattern_count or 0
    return stats


@router.get("/errors", summary="List errors (paginated)", dependencies=[Depends(_require_admin)])
async def admin_errors(
    status_filter: str | None = Query(None, alias="status"),
    service: str | None = Query(None),
    severity: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    """Paginated errors list with optional filtering."""
    from sqlalchemy import select

    from storage.models import Error

    postgres = get_postgres()
    offset = (page - 1) * page_size

    async with postgres.session() as sess:
        q = select(Error).order_by(Error.created_at.desc())
        if status_filter:
            q = q.where(Error.status == status_filter)
        if service:
            q = q.where(Error.service == service)
        if severity:
            q = q.where(Error.severity == severity)

        total_q = select(Error)
        if status_filter:
            total_q = total_q.where(Error.status == status_filter)
        if service:
            total_q = total_q.where(Error.service == service)
        if severity:
            total_q = total_q.where(Error.severity == severity)

        from sqlalchemy import func
        total = await sess.scalar(select(func.count()).select_from(total_q.subquery()))
        rows = (await sess.execute(q.offset(offset).limit(page_size))).scalars().all()

    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(r.id), "error_class": r.error_class,
                "message": r.message[:200], "service": r.service,
                "severity": r.severity, "status": r.status,
                "created_at": r.created_at.isoformat(),
                "signature": r.signature,
                # XAI fields
                "file_path": r.file_path,
                "line_number": r.line_number,
                "stack_trace": (r.stack_trace or "")[:1500],
                "environment": r.environment,
                "source": r.source,
                "commit_sha": r.commit_sha,
                "branch": r.branch,
            }
            for r in rows
        ],
    }


@router.get("/errors/{error_id}", summary="Full error detail", dependencies=[Depends(_require_admin)])
async def admin_error_detail(error_id: str) -> dict:
    """Returns the complete error record including stack trace and all metadata."""
    from sqlalchemy import select
    from storage.models import Error, Fix
    postgres = get_postgres()
    async with postgres.session() as sess:
        e = await sess.get(Error, error_id)
        if not e:
            raise HTTPException(status_code=404, detail="Error not found")
        # Count linked fixes
        from sqlalchemy import func
        fix_count = await sess.scalar(select(func.count(Fix.id)).where(Fix.error_id == e.id))
        fixes = (await sess.execute(
            select(Fix).where(Fix.error_id == e.id).order_by(Fix.created_at.desc()).limit(5)
        )).scalars().all()
    return {
        "id": str(e.id), "error_class": e.error_class, "message": e.message,
        "service": e.service, "severity": e.severity, "status": e.status,
        "signature": e.signature, "environment": e.environment, "source": e.source,
        "file_path": e.file_path, "line_number": e.line_number,
        "stack_trace": e.stack_trace or "",
        "commit_sha": e.commit_sha, "branch": e.branch, "deploy_id": e.deploy_id,
        "created_at": e.created_at.isoformat(),
        "fix_count": fix_count or 0,
        "fixes": [
            {
                "id": str(f.id), "strategy": f.strategy, "confidence": f.confidence,
                "outcome": f.outcome, "pr_url": f.pr_url,
                "created_at": f.created_at.isoformat(),
            } for f in fixes
        ],
    }



@router.get("/fixes", summary="List fixes (paginated)", dependencies=[Depends(_require_admin)])
async def admin_fixes(
    outcome: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    """Paginated fixes list with optional outcome filter."""
    from sqlalchemy import func, select

    from storage.models import Fix

    postgres = get_postgres()
    offset = (page - 1) * page_size

    async with postgres.session() as sess:
        q = select(Fix).order_by(Fix.created_at.desc())
        if outcome:
            q = q.where(Fix.outcome == outcome)

        count_q = select(func.count()).select_from(
            (q if not outcome else select(Fix).where(Fix.outcome == outcome)).subquery()
        )
        total = await sess.scalar(count_q)
        rows = (await sess.execute(q.offset(offset).limit(page_size))).scalars().all()

    return {
        "total": total or 0,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": str(r.id), "error_id": str(r.error_id),
                "strategy": r.strategy, "confidence": r.confidence,
                "outcome": r.outcome, "pr_url": r.pr_url,
                "files_changed": r.files_changed,
                "created_at": r.created_at.isoformat(),
                # XAI fields
                "lines_changed": r.lines_changed,
                "validation_pass": r.validation_pass,
                "reviewer_notes": r.reviewer_notes or "",
                "llm_provider": r.llm_provider_used or "",
                "coverage_delta": r.coverage_delta,
                "patch_preview": (r.patch_content or "")[:800],
                "test_results": r.test_results or {},
            }
            for r in rows
        ],
    }


@router.get("/fixes/{fix_id}", summary="Full fix detail with XAI", dependencies=[Depends(_require_admin)])
async def admin_fix_detail(fix_id: str) -> dict:
    """Returns the complete fix record including patch content and validation results."""
    from sqlalchemy import select
    from storage.models import Fix, Error, AuditLog
    postgres = get_postgres()
    async with postgres.session() as sess:
        f = await sess.get(Fix, fix_id)
        if not f:
            raise HTTPException(status_code=404, detail="Fix not found")
        # Linked error
        error = await sess.get(Error, f.error_id)
        # Audit entries for this fix
        audit_rows = (await sess.execute(
            select(AuditLog)
            .where(AuditLog.resource_id == fix_id)
            .order_by(AuditLog.created_at.desc()).limit(10)
        )).scalars().all()
    # Build confidence breakdown heuristic
    conf = f.confidence or 0
    strategy_weights = {
        "llm_single_file": {"context_match": round(conf * 0.4, 2), "pattern_match": round(conf * 0.35, 2), "syntax_valid": round(conf * 0.25, 2)},
        "llm_multi_file": {"context_match": round(conf * 0.35, 2), "cross_file_consistency": round(conf * 0.4, 2), "test_pass": round(conf * 0.25, 2)},
        "pattern_match": {"pattern_hit": round(conf * 0.6, 2), "similarity_score": round(conf * 0.4, 2)},
    }
    breakdown = strategy_weights.get(f.strategy or "", {"overall": round(conf, 2)})
    return {
        "id": str(f.id), "error_id": str(f.error_id),
        "strategy": f.strategy, "confidence": f.confidence,
        "outcome": f.outcome, "pr_url": f.pr_url,
        "files_changed": f.files_changed or [],
        "lines_changed": f.lines_changed,
        "validation_pass": f.validation_pass,
        "coverage_delta": f.coverage_delta,
        "reviewer_notes": f.reviewer_notes or "",
        "llm_provider": f.llm_provider_used or "",
        "patch_content": f.patch_content or "",
        "test_results": f.test_results or {},
        "semgrep_results": f.semgrep_results or {},
        "created_at": f.created_at.isoformat(),
        "approved_at": f.approved_at.isoformat() if f.approved_at else None,
        # XAI
        "confidence_breakdown": breakdown,
        "strategy_rationale": _strategy_rationale(f.strategy, f.confidence),
        "error_context": {
            "class": error.error_class if error else "",
            "service": error.service if error else "",
            "message": error.message[:200] if error else "",
        } if error else {},
        "decision_trail": [
            {
                "action": a.action, "actor": a.actor,
                "comment": (a.extra_data or {}).get("comment", ""),
                "at": a.created_at.isoformat(),
            } for a in audit_rows
        ],
    }


def _strategy_rationale(strategy: str | None, confidence: float | None) -> str:
    conf = int((confidence or 0) * 100)
    rationales = {
        "llm_single_file": (
            f"The LLM analysed the error stack trace and identified a single-file root cause. "
            f"Confidence {conf}% reflects the model's certainty that the fix is complete within "
            f"the identified file without cross-module side effects."
        ),
        "llm_multi_file": (
            f"The error required coordinated changes across multiple files. The LLM performed "
            f"cross-file dependency analysis and generated consistent patches. "
            f"Confidence {conf}% accounts for the higher complexity of multi-file changes."
        ),
        "pattern_match": (
            f"A known error pattern in the Pattern Library matched this error class with "
            f"{conf}% similarity. The fix is derived from a previously validated resolution "
            f"that succeeded on structurally identical errors."
        ),
        "rollback_diff": (
            f"No confident LLM fix was generated. The system fell back to identifying the "
            f"last known-good commit and generating a targeted rollback diff. "
            f"Confidence {conf}% reflects rollback applicability."
        ),
        "human_escalation": "This error exceeded the confidence threshold for autonomous resolution and was escalated to a human reviewer.",
    }
    return rationales.get(strategy or "", f"Strategy '{strategy}' applied with {conf}% confidence.")



@router.get("/patterns", summary="List active patterns", dependencies=[Depends(_require_admin)])
async def admin_patterns() -> dict:
    """Lists all active pattern library entries sorted by success rate."""
    from sqlalchemy import select

    from storage.models import PatternLibrary

    postgres = get_postgres()
    async with postgres.session() as sess:
        rows = (
            await sess.execute(
                select(PatternLibrary)
                .where(PatternLibrary.is_active == True)
                .order_by(PatternLibrary.success_rate.desc())
                .limit(100)
            )
        ).scalars().all()

    return {
        "count": len(rows),
        "items": [
            {
                "id": str(r.id), "error_class": r.error_class,
                "language": r.language, "success_rate": r.success_rate,
                "times_used": r.times_used,
                "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            }
            for r in rows
        ],
    }


@router.post("/reindex", summary="Trigger Qdrant re-index", dependencies=[Depends(_require_admin)])
async def admin_reindex(
    service: str = Query("dara-self"),
    repo_path: str = Query("."),
) -> dict:
    """Enqueue a repo re-index task via Celery."""
    try:
        from workers.tasks import index_repository
        task = index_repository.delay(repo_path, service, [".py"])
        logger.info("admin_reindex: enqueued task_id=%s service=%s", task.id, service)
        return {"task_id": task.id, "status": "enqueued", "service": service}
    except Exception as e:
        logger.warning("admin_reindex: Celery unavailable: %s", e)
        raise HTTPException(status_code=503, detail=f"Celery not available: {e}") from e


@router.get("/pipeline/{error_id}", summary="Full pipeline state", dependencies=[Depends(_require_admin)])
async def admin_pipeline(error_id: str) -> dict:
    """Returns error + pipeline run + associated fix for a given error_id."""
    postgres = get_postgres()
    redis = get_redis()

    error = await postgres.get_error(error_id)
    if not error:
        raise HTTPException(status_code=404, detail="Error not found")

    # Get Redis pipeline state
    redis_state = {}
    try:
        redis_state = await redis.get_pipeline_state(error_id) or {}
    except Exception:
        pass

    return {
        "error": {
            "id": str(error.id), "error_class": error.error_class,
            "message": error.message, "service": error.service,
            "severity": error.severity, "status": error.status,
            "created_at": error.created_at.isoformat(),
            "signature": error.signature,
        },
        "redis_state": redis_state,
    }


@router.get("/audit", summary="Recent audit log events", dependencies=[Depends(_require_admin)])
async def admin_audit(
    limit: int = Query(50, ge=1, le=200),
    action: str | None = Query(None),
) -> dict:
    """Returns the most recent audit log entries."""
    from sqlalchemy import select

    from storage.models import AuditLog

    postgres = get_postgres()
    async with postgres.session() as sess:
        q = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        if action:
            q = q.where(AuditLog.action == action)
        rows = (await sess.execute(q)).scalars().all()

    return {
        "count": len(rows),
        "items": [
            {
                "id": str(r.id),
                "action": r.action,
                "fix_id": r.resource_id or "",
                "error_class": (r.extra_data or {}).get("error_class", ""),
                "reviewer": r.actor or "system",
                "comment": (r.extra_data or {}).get("comment") or (r.after_state or {}).get("reviewer_notes", ""),
                "resource_type": r.resource_type or "",
                "created_at": r.created_at.isoformat(),
                # XAI fields
                "confidence": (r.extra_data or {}).get("confidence") or (r.after_state or {}).get("confidence"),
                "strategy": (r.extra_data or {}).get("strategy") or (r.after_state or {}).get("strategy", ""),
                "pr_url": (r.after_state or {}).get("pr_url", ""),
                "before_outcome": (r.before_state or {}).get("outcome", ""),
                "after_outcome": (r.after_state or {}).get("outcome", ""),
                "decision_factors": _decision_factors(r.action, r.extra_data or {}, r.after_state or {}),
            }
            for r in rows
        ],
    }


def _decision_factors(action: str, extra: dict, after: dict) -> list[dict]:
    """Synthesise human-readable decision factors from stored state for XAI."""
    factors = []
    conf = extra.get("confidence") or after.get("confidence")
    strategy = extra.get("strategy") or after.get("strategy", "")
    if conf is not None:
        pct = int(float(conf) * 100) if float(conf) <= 1 else int(float(conf))
        threshold = "above" if pct >= 70 else "below"
        factors.append({"label": "AI Confidence", "value": f"{pct}%", "note": f"{threshold} 70% auto-merge threshold"})
    if strategy:
        factors.append({"label": "Strategy Used", "value": strategy, "note": "Model selected based on error class & pattern history"})
    if action == "fix_approved":
        factors.append({"label": "Human Decision", "value": "Approved", "note": "Reviewer accepted patch — triggers PR creation"})
    elif action == "fix_rejected":
        factors.append({"label": "Human Decision", "value": "Rejected", "note": "Reviewer rejected — fix returned to pending queue"})
    comment = extra.get("comment") or after.get("reviewer_notes", "")
    if comment:
        factors.append({"label": "Reviewer Note", "value": comment, "note": "Explicit rationale logged"})
    return factors



@router.get("/strategies", summary="Strategy variants (synthesized from fixes)", dependencies=[Depends(_require_admin)])
async def admin_strategies() -> dict:
    """
    Returns strategy variant performance synthesized from Fix records.
    Groups fixes by (error_class, strategy) and computes acceptance rate.
    """
    from sqlalchemy import case as sa_case, func, select

    from storage.models import Error, Fix

    postgres = get_postgres()
    async with postgres.session() as sess:
        # Join Fix → Error to get error_class per fix
        rows = (
            await sess.execute(
                select(
                    Fix.strategy,
                    Error.error_class,
                    func.count(Fix.id).label("total_uses"),
                    func.sum(
                        sa_case((Fix.outcome == "accepted", 1), else_=0)
                    ).label("accepted"),
                    func.avg(Fix.confidence).label("avg_confidence"),
                    func.max(Fix.created_at).label("last_used"),
                )
                .join(Error, Fix.error_id == Error.id, isouter=True)
                .group_by(Fix.strategy, Error.error_class)
                .order_by(func.count(Fix.id).desc())
                .limit(50)
            )
        ).all()

    items = []
    for i, r in enumerate(rows):
        total = r.total_uses or 0
        accepted = int(r.accepted or 0)
        rate = round((accepted / total * 100) if total > 0 else 0, 1)
        items.append({
            "id": f"strat-{i}",
            "error_class": r.error_class or "unknown",
            "variant_name": r.strategy or "unknown",
            "status": "active" if rate >= 50 else "testing",
            "acceptance_rate": rate,
            "total_uses": total,
            "success_rate": round(float(r.avg_confidence or 0) * 100, 1),
            "generated_by": "llm",
            "created_at": r.last_used.isoformat() if r.last_used else "",
        })

    return {"total": len(items), "items": items}


@router.get("/throughput", summary="Pipeline throughput (last 60min, 5min buckets)", dependencies=[Depends(_require_admin)])
async def admin_throughput() -> dict:
    """
    Returns 12 five-minute buckets covering the last 60 minutes.
    Each bucket has: label (HH:MM), ingested (error count), fixed (accepted fix count).
    Derived from real Error and Fix rows in Postgres.
    """
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func, select
    from storage.models import Error, Fix

    postgres = get_postgres()
    now = datetime.now(timezone.utc)
    buckets = []

    async with postgres.session() as sess:
        for i in range(11, -1, -1):
            bucket_start = now - timedelta(minutes=(i + 1) * 5)
            bucket_end   = now - timedelta(minutes=i * 5)
            label = bucket_start.strftime("%H:%M")

            err_count = await sess.scalar(
                select(func.count(Error.id)).where(
                    Error.created_at >= bucket_start,
                    Error.created_at <  bucket_end,
                )
            ) or 0

            fix_count = await sess.scalar(
                select(func.count(Fix.id)).where(
                    Fix.outcome == "accepted",
                    Fix.approved_at >= bucket_start,
                    Fix.approved_at <  bucket_end,
                )
            ) or 0

            buckets.append({"label": label, "ingested": err_count, "fixed": fix_count})

    return {"buckets": buckets}


@router.get("/errors-by-class", summary="Error count grouped by error_class", dependencies=[Depends(_require_admin)])
async def admin_errors_by_class() -> dict:
    """
    Returns error counts grouped by error_class, sorted descending.
    Used for the 'errors by class' bar chart on the dashboard.
    """
    from sqlalchemy import func, select
    from storage.models import Error

    postgres = get_postgres()
    async with postgres.session() as sess:
        rows = (await sess.execute(
            select(Error.error_class, func.count(Error.id).label("count"))
            .group_by(Error.error_class)
            .order_by(func.count(Error.id).desc())
        )).all()

    return {
        "items": [{"class": r.error_class or "unknown", "count": r.count} for r in rows]
    }


# ═════════════════════════════════════════════════════════════════════
# PHASE 2 ENDPOINTS
# ═════════════════════════════════════════════════════════════════════

@router.get("/anomalies", summary="Active anomaly alerts", dependencies=[Depends(_require_admin)])
async def admin_anomalies(hours: int = Query(4, ge=1, le=48)) -> dict:
    """
    Returns unresolved anomaly alerts from the last N hours.
    Fallback: if no alerts exist (AnomalyDetector hasn't run yet), computes
    a live rate-spike check by comparing the last-10-min error rate to
    the rolling 1-hour baseline — no TimescaleDB required.
    """
    from datetime import timedelta, timezone as tz, datetime as dt
    from sqlalchemy import func, select, text
    from storage.models import Error

    postgres = get_postgres()
    cutoff = dt.now(tz.utc) - timedelta(hours=hours)

    stored = []
    async with postgres.session() as sess:
        # Try to read from anomaly_alerts table (may not exist yet)
        try:
            rows = (await sess.execute(
                text("""
                    SELECT id, service_name, alert_type, metric_name,
                           current_value, baseline_value, z_score,
                           severity, triggered_at
                    FROM anomaly_alerts
                    WHERE triggered_at >= :cutoff
                      AND resolved_at IS NULL
                    ORDER BY triggered_at DESC
                    LIMIT 20
                """),
                {"cutoff": cutoff},
            )).mappings().all()
            stored = [
                {
                    "id": str(r["id"]),
                    "service_name": r["service_name"],
                    "alert_type": r["alert_type"],
                    "severity": r["severity"],
                    "current_value": float(r["current_value"]),
                    "baseline_value": float(r["baseline_value"]) if r["baseline_value"] is not None else None,
                    "z_score": float(r["z_score"]) if r["z_score"] is not None else None,
                    "triggered_at": r["triggered_at"].isoformat(),
                    "message": f"{r['alert_type']} on {r['service_name']}",
                }
                for r in rows
            ]
        except Exception as tbl_err:
            logger.debug("anomaly_alerts table not available: %s", tbl_err)

        # Live rate-spike fallback (always runs)
        live_anomalies = []
        now = dt.now(tz.utc)
        window_start = now - timedelta(minutes=10)
        baseline_start = now - timedelta(hours=1)
        baseline_end = now - timedelta(minutes=10)

        recent_count = await sess.scalar(
            select(func.count(Error.id)).where(Error.created_at >= window_start)
        ) or 0
        baseline_count = await sess.scalar(
            select(func.count(Error.id)).where(
                Error.created_at >= baseline_start,
                Error.created_at < baseline_end,
            )
        ) or 0

        recent_rate = recent_count / 10.0
        baseline_rate = baseline_count / 50.0

        if baseline_rate > 0 and recent_rate >= baseline_rate * 2.0:
            live_anomalies.append({
                "id": "live-rate-spike",
                "service_name": "all",
                "alert_type": "error_rate_surge",
                "severity": "critical" if recent_rate >= baseline_rate * 3 else "warning",
                "current_value": round(recent_rate, 2),
                "baseline_value": round(baseline_rate, 2),
                "z_score": round((recent_rate - baseline_rate) / max(baseline_rate, 0.01), 2),
                "triggered_at": now.isoformat(),
                "message": f"Error rate {recent_rate:.1f}/min is {recent_rate/max(baseline_rate,0.01):.1f}x the 1h baseline ({baseline_rate:.1f}/min)",
            })

    all_alerts = stored + live_anomalies
    return {
        "count": len(all_alerts),
        "has_anomalies": len(all_alerts) > 0,
        "items": all_alerts,
    }


@router.get("/confidence-trend", summary="Weekly acceptance rate trend", dependencies=[Depends(_require_admin)])
async def admin_confidence_trend(weeks: int = Query(8, ge=2, le=26)) -> dict:
    """
    Returns per-ISO-week acceptance rate and average confidence for the last N weeks.
    Used for the trend line chart on the Strategy page.
    """
    from datetime import timedelta, timezone as tz, datetime as dt
    from sqlalchemy import case as sa_case, func, select, text
    from storage.models import Fix

    postgres = get_postgres()
    since = dt.now(tz.utc) - timedelta(weeks=weeks)

    buckets = []
    async with postgres.session() as sess:
        try:
            rows = (await sess.execute(
                select(
                    func.date_trunc("week", Fix.created_at).label("week"),
                    func.count(Fix.id).label("total"),
                    func.sum(
                        sa_case((Fix.outcome == "accepted", 1), else_=0)
                    ).label("accepted"),
                    func.avg(Fix.confidence).label("avg_confidence"),
                )
                .where(Fix.created_at >= since)
                .group_by(func.date_trunc("week", Fix.created_at))
                .order_by(func.date_trunc("week", Fix.created_at))
            )).all()

            for r in rows:
                total = r.total or 1
                accepted = int(r.accepted or 0)
                buckets.append({
                    "week": r.week.strftime("%Y-W%V") if r.week else "",
                    "week_start": r.week.isoformat() if r.week else "",
                    "total": total,
                    "accepted": accepted,
                    "acceptance_rate": round(accepted / total * 100, 1),
                    "avg_confidence": round(float(r.avg_confidence or 0) * 100, 1),
                })
        except Exception as e:
            logger.warning("confidence-trend query failed: %s — returning empty", e)
            # Fallback: return a simple approved/total count without week grouping
            try:
                total = await sess.scalar(select(func.count(Fix.id)).where(Fix.created_at >= since)) or 0
                accepted = await sess.scalar(
                    select(func.count(Fix.id))
                    .where(Fix.created_at >= since, Fix.outcome == "accepted")
                ) or 0
                if total > 0:
                    from datetime import datetime as dt2
                    buckets.append({
                        "week": dt2.now(tz.utc).strftime("%Y-W%V"),
                        "week_start": since.isoformat(),
                        "total": total,
                        "accepted": accepted,
                        "acceptance_rate": round(accepted / total * 100, 1),
                        "avg_confidence": 0,
                    })
            except Exception:
                pass

    return {"weeks": weeks, "buckets": buckets}



@router.get("/export/fine-tuning", summary="Fine-tuning data export", dependencies=[Depends(_require_admin)])
async def admin_export_fine_tuning(
    dry_run: bool = Query(True),
    days: int = Query(30, ge=1, le=365),
) -> dict:
    """
    Triggers the FineTuningExporter to build an OpenAI-format JSONL
    dataset from approved, high-confidence fix pairs.
    dry_run=True previews the export without writing to MinIO.
    """
    from datetime import timedelta, timezone as tz, datetime as dt
    try:
        from export.fine_tuning_exporter import FineTuningExporter
        postgres = get_postgres()
        exporter = FineTuningExporter(postgres=postgres)
        since = dt.now(tz.utc) - timedelta(days=days)
        result = await exporter.export(since_timestamp=since, dry_run=dry_run)
        return {
            "status": result.status if result else "error",
            "run_id": result.run_id if result else None,
            "triple_count": result.triple_count if result else 0,
            "skipped_count": result.skipped_count if result else 0,
            "minio_path": result.minio_path if result else None,
            "dry_run": dry_run,
            "days": days,
            "error_message": result.error_message if result else "Export failed",
        }
    except Exception as e:
        logger.error("Fine-tuning export error: %s", e)
        return {
            "status": "error",
            "triple_count": 0,
            "skipped_count": 0,
            "dry_run": dry_run,
            "error_message": str(e),
        }


@router.post(
    "/errors/{error_id}/analyse",
    status_code=202,
    summary="Trigger on-demand pipeline analysis",
    dependencies=[Depends(_require_admin)],
)
async def admin_analyse_error(error_id: str) -> dict:
    """
    Enqueue an error through the full DARA pipeline immediately.
    Returns 202 with a task_id. Poll GET /admin/errors/{error_id}
    for status — 'analyzing' → 'fixed' | 'escalated'.
    """
    from fastapi import BackgroundTasks
    from fastapi.responses import JSONResponse

    postgres = get_postgres()
    error = await postgres.get_error(error_id)
    if not error:
        raise HTTPException(status_code=404, detail="Error not found")

    if error.status not in ("pending", "failed"):
        return {
            "status": "skipped",
            "reason": f"Error is already in state '{error.status}'",
            "error_id": error_id,
        }

    # Mark as analyzing immediately so the UI can reflect it
    try:
        from sqlalchemy import update
        from storage.models import Error as ErrorModel
        async with postgres.session() as sess:
            await sess.execute(
                update(ErrorModel)
                .where(ErrorModel.id == error_id)
                .values(status="analyzing")
            )
            await sess.commit()
    except Exception as e:
        logger.warning("Could not mark analyzing: %s", e)

    # Try Celery first (non-blocking), fall back to direct background run
    task_id = None
    try:
        from workers.tasks import analyze_error
        result = analyze_error.delay(error_id, priority="P1")
        task_id = result.id
        logger.info("Queued via Celery: task_id=%s error_id=%s", task_id, error_id)
    except Exception as celery_err:
        logger.warning("Celery unavailable (%s), running inline", celery_err)
        # Run inline as async background task
        import asyncio

        async def _run_inline():
            try:
                from config.llm_router import get_llm_router
                from agents.orchestrator import Orchestrator
                from storage.redis_client import get_redis
                redis = get_redis()
                llm = get_llm_router(redis_client=redis.client)
                orch = Orchestrator(postgres=postgres, redis=redis, llm_router=llm, repo_path=".")
                result = await orch.run(error_id)
                # Publish to SSE
                from api.events.broadcaster import get_broadcaster
                await get_broadcaster().publish({
                    "type": "fix.new",
                    "error_id": error_id,
                    "status": result.status,
                    "fix_id": result.fix_id,
                })
                logger.info("Inline pipeline done: %s → %s", error_id, result.status)
            except Exception as e:
                logger.error("Inline pipeline failed for %s: %s", error_id, e)

        asyncio.create_task(_run_inline())
        task_id = f"inline-{error_id[:8]}"

    # Publish SSE event — client knows analysis started
    try:
        from api.events.broadcaster import get_broadcaster
        await get_broadcaster().publish({
            "type": "error.analyzing",
            "error_id": error_id,
            "task_id": task_id,
        })
    except Exception:
        pass

    return {
        "status": "queued",
        "error_id": error_id,
        "task_id": task_id,
        "poll": f"/api/v1/admin/errors/{error_id}",
    }


# ═════════════════════════════════════════════════════════════════════
# PHASE 3 ENDPOINTS
# ═════════════════════════════════════════════════════════════════════

@router.get("/workers", summary="Celery worker status", dependencies=[Depends(_require_admin)])
async def admin_workers() -> dict:
    """
    Returns live Celery worker status, active tasks, and queue lengths.
    Uses celery inspect.
    """
    try:
        from workers.main import celery_app
        # Make the blocking Celery calls async-friendly
        import asyncio
        loop = asyncio.get_event_loop()
        
        # We use a timeout to prevent hanging if Celery is down
        def _get_stats():
            i = celery_app.control.inspect(timeout=1.0)
            return {
                "active": i.active() or {},
                "reserved": i.reserved() or {},
                "stats": i.stats() or {},
                "ping": i.ping() or {}
            }
            
        data = await loop.run_in_executor(None, _get_stats)
        
        workers = []
        for worker_name, stats in data["stats"].items():
            active_tasks = data["active"].get(worker_name, [])
            workers.append({
                "name": worker_name,
                "status": "online" if worker_name in data["ping"] else "offline",
                "concurrency": stats.get("pool", {}).get("max-concurrency", 0),
                "active_tasks": len(active_tasks),
                "processed": stats.get("total", {}).get("workers.tasks.analyze_error", 0),
                "tasks": [
                    {
                        "id": t.get("id"),
                        "name": t.get("name"),
                        "args": t.get("args"),
                        "time_start": t.get("time_start")
                    } for t in active_tasks
                ]
            })
            
        return {
            "status": "ok",
            "workers": workers,
            "total_active": sum(w["active_tasks"] for w in workers),
            "total_processed": sum(w["processed"] for w in workers)
        }
    except Exception as e:
        logger.error("Failed to fetch worker stats: %s", e)
        return {"status": "error", "error": str(e), "workers": []}


@router.get("/search", summary="Global search across errors/fixes/audit", dependencies=[Depends(_require_admin)])
async def admin_search(q: str = Query(..., min_length=2, max_length=200)) -> dict:
    """
    Searches errors (message, error_class, service), fixes (strategy, outcome),
    and audit logs (action, actor) for the given query string.
    Returns up to 25 results grouped by type.
    """
    from sqlalchemy import select, or_, func
    from storage.models import Error, Fix, AuditLog

    postgres = get_postgres()
    term = f"%{q.lower()}%"
    results = []

    async with postgres.session() as sess:
        # Errors
        err_rows = (await sess.execute(
            select(Error.id, Error.error_class, Error.message, Error.service, Error.status, Error.created_at)
            .where(or_(
                func.lower(Error.message).like(term),
                func.lower(Error.error_class).like(term),
                func.lower(Error.service).like(term),
            ))
            .order_by(Error.created_at.desc())
            .limit(10)
        )).all()

        for r in err_rows:
            results.append({
                "type": "error",
                "id": str(r.id),
                "title": r.error_class or "unknown",
                "subtitle": (r.message or "")[:120],
                "href": f"/errors?highlight={r.id}",
                "meta": r.status,
            })

        # Fixes
        from sqlalchemy import String as SAString
        fix_rows = (await sess.execute(
            select(Fix.id, Fix.error_id, Fix.strategy, Fix.outcome, Fix.confidence, Fix.created_at)
            .where(or_(
                func.lower(Fix.strategy).like(term),
                func.lower(Fix.outcome).like(term),
                func.lower(Fix.error_id.cast(SAString)).like(term),
            ))
            .order_by(Fix.created_at.desc())
            .limit(10)
        )).all()

        for r in fix_rows:
            results.append({
                "type": "fix",
                "id": str(r.id),
                "title": f"fix · {r.strategy}",
                "subtitle": f"error: {str(r.error_id)[:14]}… | outcome: {r.outcome or 'pending'}",
                "href": f"/fixes?highlight={r.id}",
                "meta": f"{int((r.confidence or 0)*100)}%" if r.confidence else None,
            })

        # Audit log
        try:
            audit_rows = (await sess.execute(
                select(AuditLog.id, AuditLog.action, AuditLog.actor, AuditLog.resource_id, AuditLog.created_at)
                .where(or_(
                    func.lower(AuditLog.action).like(term),
                    func.lower(AuditLog.actor).like(term),
                ))
                .order_by(AuditLog.created_at.desc())
                .limit(5)
            )).all()

            for r in audit_rows:
                results.append({
                    "type": "audit",
                    "id": str(r.id),
                    "title": r.action or "audit event",
                    "subtitle": f"by {r.actor or 'system'} · resource: {str(r.resource_id or '')[:14]}",
                    "href": f"/audit",
                    "meta": None,
                })
        except Exception:
            pass  # AuditLog may not exist yet

    return {"query": q, "total": len(results), "results": results}


@router.get("/rate-limits", summary="Redis rate-limit counters", dependencies=[Depends(_require_admin)])
async def admin_rate_limits() -> dict:
    """
    Reads all rate-limit keys from Redis (pattern: ratelimit:*).
    Returns hit counts, limits, TTL, and blocked status.
    Falls back gracefully if no keys exist.
    """
    import re

    redis = get_redis()
    entries = []

    try:
        client = redis.client
        keys = await client.keys("ratelimit:*")

        for key in keys[:100]:  # cap at 100
            key_str = key.decode() if isinstance(key, bytes) else key
            try:
                val = await client.get(key)
                ttl = await client.ttl(key)
                hits = int(val or 0)

                # Parse key: ratelimit:<endpoint>:<identifier>
                parts = key_str.split(":", 2)
                endpoint = parts[1] if len(parts) > 1 else ""
                identifier = parts[2] if len(parts) > 2 else key_str

                # Determine limit from settings (default 100/min)
                settings = get_settings()
                limit = getattr(settings, "rate_limit_per_minute", 100)
                window = 60

                entries.append({
                    "key": key_str,
                    "endpoint": endpoint,
                    "identifier": identifier,
                    "hits": hits,
                    "limit": limit,
                    "window_seconds": window,
                    "blocked": hits >= limit,
                    "ttl_seconds": max(ttl, 0),
                })
            except Exception:
                continue

        blocked = sum(1 for e in entries if e["blocked"])
        return {
            "status": "ok",
            "total_keys": len(entries),
            "blocked_count": blocked,
            "entries": sorted(entries, key=lambda e: e["hits"], reverse=True),
        }

    except Exception as exc:
        logger.error("rate-limits error: %s", exc)
        return {"status": "error", "error": str(exc), "total_keys": 0, "blocked_count": 0, "entries": []}


# ── Pattern Auto-Promotion (Feature 16) ───────────────────────────────────────

_PROMO_THRESHOLD_TIMES_USED = 3      # minimum times_used to be eligible
_PROMO_THRESHOLD_SUCCESS_RATE = 0.75  # minimum success_rate (0-1) to be eligible


@router.get(
    "/patterns/promotion-candidates",
    summary="Patterns eligible for auto-promotion",
    dependencies=[Depends(_require_admin)],
)
async def pattern_promotion_candidates() -> dict:
    """
    Returns inactive PatternLibrary entries that meet both promotion thresholds:
      • times_used >= 3
      • success_rate >= 0.75
    These are "pending" patterns that can be promoted to is_active=True.
    """
    from sqlalchemy import select
    from storage.models import PatternLibrary

    postgres = get_postgres()
    async with postgres.session() as sess:
        rows = (await sess.execute(
            select(PatternLibrary)
            .where(
                PatternLibrary.is_active == False,  # noqa: E712
                PatternLibrary.times_used >= _PROMO_THRESHOLD_TIMES_USED,
                PatternLibrary.success_rate >= _PROMO_THRESHOLD_SUCCESS_RATE,
            )
            .order_by(PatternLibrary.success_rate.desc())
        )).scalars().all()

    candidates = [
        {
            "id": str(r.id),
            "error_class": r.error_class,
            "language": r.language,
            "times_used": r.times_used,
            "success_rate": round(r.success_rate, 3),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
            "fix_template_preview": (r.fix_template or "")[:200],
        }
        for r in rows
    ]

    return {
        "thresholds": {
            "min_times_used": _PROMO_THRESHOLD_TIMES_USED,
            "min_success_rate": _PROMO_THRESHOLD_SUCCESS_RATE,
        },
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


@router.post(
    "/patterns/auto-promote",
    summary="Auto-promote eligible patterns to active",
    dependencies=[Depends(_require_admin)],
)
async def pattern_auto_promote(dry_run: bool = Query(default=False)) -> dict:
    """
    Promotes all eligible inactive patterns (times_used >= 3, success_rate >= 0.75)
    to is_active=True.  Writes one AuditLog entry per promoted pattern.
    Returns the list of promoted pattern IDs.

    Pass ?dry_run=true to preview without writing.
    """
    from datetime import datetime, timezone
    from sqlalchemy import select
    from storage.models import PatternLibrary, AuditLog

    postgres = get_postgres()
    promoted = []

    async with postgres.session() as sess:
        rows = (await sess.execute(
            select(PatternLibrary)
            .where(
                PatternLibrary.is_active == False,  # noqa: E712
                PatternLibrary.times_used >= _PROMO_THRESHOLD_TIMES_USED,
                PatternLibrary.success_rate >= _PROMO_THRESHOLD_SUCCESS_RATE,
            )
        )).scalars().all()

        if not dry_run:
            for pattern in rows:
                pattern.is_active = True
                sess.add(pattern)

                # Write audit entry
                try:
                    audit = AuditLog(
                        action="pattern_auto_promoted",
                        actor="dara-system",
                        resource_type="pattern_library",
                        resource_id=str(pattern.id),
                        before_state={"is_active": False},
                        after_state={
                            "is_active": True,
                            "times_used": pattern.times_used,
                            "success_rate": pattern.success_rate,
                        },
                        extra_data={
                            "error_class": pattern.error_class,
                            "threshold_times_used": _PROMO_THRESHOLD_TIMES_USED,
                            "threshold_success_rate": _PROMO_THRESHOLD_SUCCESS_RATE,
                        },
                    )
                    sess.add(audit)
                except Exception as audit_exc:
                    logger.warning("Could not write audit for pattern %s: %s", pattern.id, audit_exc)

            await sess.commit()

        promoted = [
            {
                "id": str(p.id),
                "error_class": p.error_class,
                "times_used": p.times_used,
                "success_rate": round(p.success_rate, 3),
            }
            for p in rows
        ]

    logger.info(
        "Pattern auto-promote: dry_run=%s promoted=%d patterns",
        dry_run, len(promoted)
    )

    # Broadcast SSE event so the live stream picks it up
    if not dry_run and promoted:
        try:
            from api.events.broadcaster import get_broadcaster
            await get_broadcaster().publish({
                "event": "patterns_promoted",
                "count": len(promoted),
                "pattern_ids": [p["id"] for p in promoted],
            })
        except Exception:
            pass

    return {
        "status": "dry_run" if dry_run else "promoted",
        "promoted_count": len(promoted),
        "promoted": promoted,
    }


@router.get(
    "/patterns/stats",
    summary="Pattern library aggregate stats",
    dependencies=[Depends(_require_admin)],
)
async def pattern_stats() -> dict:
    """
    Returns aggregate stats for the pattern library:
    active count, inactive count, avg success rate, top error classes.
    """
    from sqlalchemy import select, func
    from storage.models import PatternLibrary

    postgres = get_postgres()
    async with postgres.session() as sess:
        totals = (await sess.execute(
            select(
                func.count().label("total"),
                func.sum(
                    PatternLibrary.is_active.cast(type_=None)  # cast to int
                ).label("active_raw"),
                func.avg(PatternLibrary.success_rate).label("avg_success_rate"),
                func.avg(PatternLibrary.times_used).label("avg_times_used"),
            )
        )).one()

        # Top 5 error classes by pattern count
        top_rows = (await sess.execute(
            select(PatternLibrary.error_class, func.count().label("cnt"))
            .group_by(PatternLibrary.error_class)
            .order_by(func.count().desc())
            .limit(5)
        )).all()

    total = totals.total or 0
    try:
        active = int(totals.active_raw or 0)
    except Exception:
        active = 0

    return {
        "total_patterns": total,
        "active_patterns": active,
        "inactive_patterns": total - active,
        "promotion_candidates": 0,  # filled by frontend via /promotion-candidates
        "avg_success_rate": round(float(totals.avg_success_rate or 0), 3),
        "avg_times_used": round(float(totals.avg_times_used or 0), 1),
        "top_error_classes": [
            {"error_class": r.error_class, "pattern_count": r.cnt}
            for r in top_rows
        ],
    }
