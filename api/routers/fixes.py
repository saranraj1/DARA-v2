"""DARA — Fixes router: approve, reject, view validation report."""
from __future__ import annotations
import logging
from fastapi import APIRouter, HTTPException, status
from api.models.fix_schemas import FixApprovalRequest, FixRejectionRequest, FixResponse
from storage.postgres import get_postgres
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/fixes/{fix_id}", response_model=FixResponse, summary="Get fix by ID")
async def get_fix(fix_id: str) -> FixResponse:
    fix = await get_postgres().get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    return FixResponse(
        id=fix.id, error_id=fix.error_id, confidence=fix.confidence,
        strategy=fix.strategy, validation_pass=fix.validation_pass,
        outcome=fix.outcome, pr_url=fix.pr_url, lines_changed=fix.lines_changed,
        files_changed=fix.files_changed, created_at=fix.created_at,
    )


@router.post("/fixes/{fix_id}/approve", status_code=202, summary="Approve a fix")
async def approve_fix(fix_id: str, body: FixApprovalRequest) -> dict:
    fix = await get_postgres().get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    await get_postgres().update_fix_outcome(fix_id, "accepted", body.reviewer_notes)
    if body.create_pr:
        await get_redis().push_task("dara:output", {"action": "create_pr", "fix_id": fix_id})
    logger.info("Fix approved", extra={"fix_id": fix_id})
    return {"status": "accepted", "fix_id": fix_id}


@router.post("/fixes/{fix_id}/reject", status_code=200, summary="Reject a fix")
async def reject_fix(fix_id: str, body: FixRejectionRequest) -> dict:
    fix = await get_postgres().get_fix(fix_id)
    if not fix:
        raise HTTPException(status_code=404, detail="Fix not found")
    await get_postgres().update_fix_outcome(fix_id, "rejected", body.reason)
    logger.info("Fix rejected", extra={"fix_id": fix_id})
    return {"status": "rejected", "fix_id": fix_id}


@router.get("/fixes/{fix_id}/validation", summary="Get validation report for a fix")
async def get_validation(fix_id: str) -> dict:
    redis = get_redis()
    cached = await redis.get_json(f"validation:{fix_id}")
    if cached:
        return cached
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
