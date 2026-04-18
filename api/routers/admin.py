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
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import APIKeyHeader

from config.settings import get_settings
from storage.postgres import get_postgres
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

_admin_key_header = APIKeyHeader(name="X-Admin-Token", auto_error=False)


async def _require_admin(token: str | None = Depends(_admin_key_header)) -> None:
    """Validates the X-Admin-Token against settings.admin_api_key."""
    settings = get_settings()
    expected = getattr(settings, "admin_api_key", None) or "dara-admin-secret"
    if not token or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Admin-Token",
        )


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
            }
            for r in rows
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
            }
            for r in rows
        ],
    }


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
        raise HTTPException(status_code=503, detail=f"Celery not available: {e}")


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
                "id": str(r.id), "action": r.action,
                "actor": r.actor, "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
    }
