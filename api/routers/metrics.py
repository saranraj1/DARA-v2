"""DARA — Metrics router for Grafana dashboard."""
from fastapi import APIRouter
from storage.postgres import get_postgres

router = APIRouter()


@router.get("/metrics/summary", summary="Pipeline performance summary")
async def metrics_summary() -> dict:
    stats = await get_postgres().get_pipeline_stats()
    acceptance_rate = (
        stats["accepted_fixes"] / stats["total_fixes"]
        if stats["total_fixes"] > 0
        else 0.0
    )
    return {**stats, "acceptance_rate": round(acceptance_rate, 3)}
