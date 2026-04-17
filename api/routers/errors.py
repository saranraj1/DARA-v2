"""
DARA — Errors Router
Handles direct error ingestion and error record retrieval.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from api.models.error_schemas import (
    ErrorIngestRequest,
    ErrorIngestResponse,
    ErrorResponse,
)
from ingestion.classifier import ErrorClassifier
from ingestion.normalizer import ErrorNormalizer
from storage.postgres import get_postgres
from storage.redis_client import get_redis

logger = logging.getLogger(__name__)
router = APIRouter()

_normalizer = ErrorNormalizer()
_classifier = ErrorClassifier()  # LLM router injected later


@router.post(
    "/errors/ingest",
    response_model=ErrorIngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest an error for analysis",
)
async def ingest_error(
    payload: ErrorIngestRequest,
    background_tasks: BackgroundTasks,
) -> ErrorIngestResponse:
    """
    Accept an error payload and queue it for the debug pipeline.
    Returns immediately with 202 — processing happens asynchronously.
    """
    raw = payload.model_dump()
    raw["source"] = "direct"

    # Normalize
    normalized = _normalizer.normalize(raw, source="direct")

    # Classify synchronously (rule-based is instant)
    classified = await _classifier.classify(normalized)

    # Persist to PostgreSQL
    postgres = get_postgres()
    error_id = await postgres.save_error(classified)

    # Push to Redis queue for Celery worker processing (graceful degradation)
    redis = get_redis()
    try:
        await redis.push_task("dara:ingestion", {"error_id": error_id, "priority": classified.get("priority", "P2")})
    except Exception as e:
        logger.warning("Redis push failed — error saved to DB, will retry via polling", extra={"error": str(e)})

    # Create initial pipeline run record
    await postgres.create_pipeline_run(error_id)

    logger.info("Error ingested", extra={"error_id": error_id, "error_class": classified["error_class"]})
    return ErrorIngestResponse(id=error_id)


@router.get(
    "/errors/{error_id}",
    response_model=ErrorResponse,
    summary="Get error by ID",
)
async def get_error(error_id: str) -> ErrorResponse:
    postgres = get_postgres()
    error = await postgres.get_error(error_id)
    if not error:
        raise HTTPException(status_code=404, detail="Error not found")
    return ErrorResponse(
        id=error.id,
        error_class=error.error_class,
        message=error.message,
        severity=error.severity,
        status=error.status,
        service=error.service,
        auto_fix_eligible=error.auto_fix_eligible,
        created_at=error.created_at,
        resolved_at=error.resolved_at,
    )


@router.get("/errors/{error_id}/analysis", summary="Get root cause analysis for an error")
async def get_analysis(error_id: str) -> JSONResponse:
    redis = get_redis()
    cached = await redis.get_json(f"analysis:{error_id}")
    if cached:
        return JSONResponse(cached)

    postgres = get_postgres()
    error = await postgres.get_error(error_id)
    if not error:
        raise HTTPException(status_code=404, detail="Error not found")

    if error.status == "pending":
        return JSONResponse({"status": "pending", "message": "Analysis in progress"}, status_code=202)

    return JSONResponse({"error_id": error_id, "status": error.status})
