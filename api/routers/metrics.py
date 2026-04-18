"""DARA — Metrics router: Prometheus scrape endpoint + JSON summary for Grafana."""
from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from storage.postgres import get_postgres

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics() -> Response:
    """
    Expose all Prometheus metrics in text format.
    Scraped by Prometheus every 15 seconds (see prometheus.yml).
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/metrics/summary", summary="Pipeline performance summary (JSON)")
async def metrics_summary() -> dict:
    """JSON stats for Grafana dashboard or quick health check."""
    stats = await get_postgres().get_pipeline_stats()
    acceptance_rate = (
        stats["accepted_fixes"] / stats["total_fixes"]
        if stats["total_fixes"] > 0
        else 0.0
    )
    return {**stats, "acceptance_rate": round(acceptance_rate, 3)}
