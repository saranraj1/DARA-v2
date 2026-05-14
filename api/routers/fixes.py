"""DARA — Fixes router: approve, reject, view validation report."""
from __future__ import annotations

import logging
import uuid as _uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from api.models.fix_schemas import FixApprovalRequest, FixRejectionRequest, FixResponse
from storage.models import AuditLog
from storage.postgres import get_postgres

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────

async def _write_audit(
    action: str,
    fix_id: str,
    actor: str,
    before_outcome: str,
    after_outcome: str,
    extra: dict | None = None,
) -> None:
    """Write a row to audit_log after every HITL decision."""
    pg = get_postgres()
    async with pg.session() as sess:
        entry = AuditLog(
            id=_uuid.uuid4(),
            action=action,
            actor=actor,
            resource_type="fix",
            resource_id=fix_id,
            before_state={"outcome": before_outcome},
            after_state={"outcome": after_outcome},
            extra_data=extra or {},
            created_at=datetime.now(timezone.utc),
        )
        sess.add(entry)
        await sess.commit()
    logger.info("Audit written: action=%s fix=%s actor=%s", action, fix_id, actor)


async def _resolve_repo(fix, error) -> str | None:
    """
    Resolve the GitHub repo full name for a fix.
    Priority: known_github_repos[service] → known_github_repos['default'] → None
    """
    try:
        from config.settings import get_settings
        settings = get_settings()
        known = settings.known_github_repos or {}
        service = (error.service if error else None) or "default"
        repo = known.get(service) or known.get("default")
        if repo:
            return repo
        # Fallback: build from github_org + service name
        org = getattr(settings, "github_org", None)
        if org and service and service != "default":
            return f"{org}/{service}"
    except Exception as e:
        logger.warning("_resolve_repo failed: %s", e)
    return None


async def _create_pr(fix_id: str, fix, error, notes: str | None) -> str | None:
    """
    Create a GitHub PR using the existing GitHubPRCreator (GitHub App auth).
    Returns PR URL or None.
    """
    try:
        from output.github_pr import GitHubPRCreator
        repo = await _resolve_repo(fix, error)
        if not repo:
            logger.warning("No GitHub repo resolved for fix %s — skipping PR", fix_id)
            return None

        patch = getattr(fix, "patch_content", "") or ""
        files = getattr(fix, "files_changed", []) or []
        error_class = getattr(error, "error_class", "unknown") if error else "unknown"

        # Build file-level patches list expected by GitHubPRCreator
        patches = [{"file_path": fp, "fixed_content": patch} for fp in files[:3]] if files else []
        if not patches and patch:
            patches = [{"file_path": "fix.patch", "fixed_content": patch}]

        creator = GitHubPRCreator()
        result = await creator.create_pr(
            repo_full_name=repo,
            fix_id=fix_id,
            error_class=error_class,
            patches=patches,
            fix_explanation=notes or f"Auto-fix for {error_class} (confidence {int((fix.confidence or 0) * 100)}%)",
        )

        if result:
            pr_url = result.get("pr_url")
            pr_number = result.get("pr_number")
            await get_postgres().update_fix_outcome(
                fix_id, "accepted", notes, pr_url=pr_url, pr_number=pr_number
            )
            return pr_url

    except Exception as e:
        logger.warning("PR creation failed for fix %s: %s", fix_id, e)

    return None


# ── Endpoints ─────────────────────────────────────────────────

@router.get("/fixes/{fix_id}", response_model=FixResponse, summary="Get fix by ID")
async def get_fix(fix_id: str) -> FixResponse:
    try:
        fix = await get_postgres().get_fix(fix_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid fix_id format (must be a UUID)")
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    return FixResponse(
        id=fix.id, error_id=fix.error_id, confidence=fix.confidence,
        strategy=fix.strategy, validation_pass=fix.validation_pass,
        outcome=fix.outcome, pr_url=fix.pr_url, lines_changed=fix.lines_changed,
        files_changed=fix.files_changed, created_at=fix.created_at,
    )


@router.post("/fixes/{fix_id}/approve", status_code=202, summary="Approve a fix")
async def approve_fix(fix_id: str, body: FixApprovalRequest, request: Request) -> dict:
    pg = get_postgres()
    try:
        fix = await pg.get_fix(fix_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid fix_id format (must be a UUID)")
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")

    prev_outcome = fix.outcome
    await pg.update_fix_outcome(fix_id, "accepted", body.reviewer_notes)

    # Fetch linked error for repo resolution & audit context
    error = None
    try:
        error = await pg.get_error(str(fix.error_id))
    except Exception:
        pass

    actor = request.headers.get("X-Reviewer", "hitl-reviewer")

    # ── 1. Write audit log ──────────────────────────────────────
    await _write_audit(
        action="fix_approved",
        fix_id=fix_id,
        actor=actor,
        before_outcome=prev_outcome,
        after_outcome="accepted",
        extra={
            "comment": body.reviewer_notes or "",
            "error_class": getattr(error, "error_class", ""),
            "confidence": fix.confidence,
            "strategy": fix.strategy,
        },
    )

    # ── 2. Create PR directly ────────────────────────────────────
    pr_url = None
    if body.create_pr:
        # Also push to queue in case a Celery worker picks it up later (idempotent)
        try:
            from storage.redis_client import get_redis
            await get_redis().push_task("dara:output", {"action": "create_pr", "fix_id": fix_id})
        except Exception:
            pass

        # Always attempt direct PR creation (works without Celery)
        pr_url = await _create_pr(fix_id, fix, error, body.reviewer_notes)

    logger.info("Fix approved fix_id=%s pr_url=%s", fix_id, pr_url)

    # ── 3. Slack notification ───────────────────────────────────
    try:
        from output.slack_notifier import SlackNotifier
        notifier = SlackNotifier()
        conf = float(fix.confidence or 0)
        await notifier.send_simple(
            f":white_check_mark: *Fix Approved* by `{actor}`\n"
            f"• Fix: `{fix_id[:12]}` | Error: `{getattr(error, 'error_class', 'unknown')}`\n"
            f"• Confidence: {conf:.0%} | Strategy: `{fix.strategy}`\n"
            f"• PR: {pr_url or '_pending_'}"
        )
    except Exception as slack_err:
        logger.debug("Slack notification skipped: %s", slack_err)

    # ── 4. Publish SSE event ────────────────────────────────────
    try:
        from api.events.broadcaster import get_broadcaster
        await get_broadcaster().publish({
            "type": "fix.approved",
            "fix_id": fix_id,
            "actor": actor,
            "pr_url": pr_url,
            "error_class": getattr(error, "error_class", ""),
        })
    except Exception:
        pass

    return {"status": "accepted", "fix_id": fix_id, "pr_url": pr_url}


@router.post("/fixes/{fix_id}/reject", status_code=200, summary="Reject a fix")
async def reject_fix(fix_id: str, body: FixRejectionRequest, request: Request) -> dict:
    pg = get_postgres()
    try:
        fix = await pg.get_fix(fix_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid fix_id format (must be a UUID)")
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")

    error = None
    try:
        error = await pg.get_error(str(fix.error_id))
    except Exception:
        pass

    prev_outcome = fix.outcome
    await pg.update_fix_outcome(fix_id, "rejected", body.reason)

    actor = request.headers.get("X-Reviewer", "hitl-reviewer")

    # Write audit log
    await _write_audit(
        action="fix_rejected",
        fix_id=fix_id,
        actor=actor,
        before_outcome=prev_outcome,
        after_outcome="rejected",
        extra={
            "comment": body.reason,
            "error_class": getattr(error, "error_class", ""),
            "strategy": fix.strategy,
        },
    )

    logger.info("Fix rejected fix_id=%s", fix_id)

    # ── Slack notification ──────────────────────────────────────
    try:
        from output.slack_notifier import SlackNotifier
        notifier = SlackNotifier()
        await notifier.send_simple(
            f":x: *Fix Rejected* by `{actor}`\n"
            f"• Fix: `{fix_id[:12]}` | Error: `{getattr(error, 'error_class', 'unknown')}`\n"
            f"• Reason: {body.reason or '_no reason given_'}"
        )
    except Exception as slack_err:
        logger.debug("Slack notification skipped: %s", slack_err)

    # ── Publish SSE event ────────────────────────────────────────
    try:
        from api.events.broadcaster import get_broadcaster
        await get_broadcaster().publish({
            "type": "fix.rejected",
            "fix_id": fix_id,
            "actor": actor,
            "error_class": getattr(error, "error_class", ""),
        })
    except Exception:
        pass

    return {"status": "rejected", "fix_id": fix_id}


@router.get("/fixes/{fix_id}/validation", summary="Get validation report for a fix")
async def get_validation(fix_id: str) -> dict:
    try:
        from storage.redis_client import get_redis
        cached = await get_redis().get_json(f"validation:{fix_id}")
        if cached:
            return cached
    except Exception:
        pass

    fix = await get_postgres().get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    return {
        "fix_id": fix_id,
        "validation_pass": fix.validation_pass,
        "test_results": fix.test_results or {},
        "semgrep_results": fix.semgrep_results or {},
        "coverage_delta": fix.coverage_delta,
    }

