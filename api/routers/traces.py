"""
DARA — Distributed Traces REST API
=====================================
Endpoints for OTel span ingestion and trace querying.

POST /api/v1/traces            — ingest OTLP or simplified spans
GET  /api/v1/traces/{trace_id} — full trace tree (all spans for a trace)
GET  /api/v1/traces            — list traces, filterable by service/error/time
GET  /api/v1/topology          — current service call graph
POST /api/v1/deploy            — ingest a deploy event (CI/CD notification)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ingestion.otel_receiver import SpanIngester
from storage.postgres import get_postgres

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request / Response Models ─────────────────────────────────

class TraceIngestResponse(BaseModel):
    ingested: int
    trace_ids: list[str]
    errors: list[str] = []


class DeployEventRequest(BaseModel):
    service_name: str
    commit_sha: str
    branch: str | None = None
    environment: str = "production"
    deployed_by: str | None = None
    version_tag: str | None = None
    deployed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Endpoints ─────────────────────────────────────────────────

@router.post(
    "/traces",
    response_model=TraceIngestResponse,
    summary="Ingest OTel spans (OTLP JSON or DARA simplified format)",
    status_code=202,
)
async def ingest_traces(payload: dict) -> TraceIngestResponse:
    """
    Accepts spans in two formats:
    - OTLP JSON (standard OTel SDK output with `resourceSpans` top-level key)
    - DARA Simplified JSON (convenience format with `spans` top-level key)

    Spans are persisted to distributed_traces table and service topology is updated.
    """
    ingester = SpanIngester(postgres=get_postgres())
    result = await ingester.ingest(payload)
    return TraceIngestResponse(**result)


@router.get(
    "/traces/{trace_id}",
    summary="Get full trace tree for a given trace_id",
)
async def get_trace(trace_id: str) -> dict:
    """Returns all spans for a trace, organised as a tree (root → children)."""
    pg = get_postgres()
    spans = await pg.get_trace(trace_id)
    if not spans:
        raise HTTPException(status_code=404, detail=f"No spans found for trace_id={trace_id}")

    # Build tree structure
    span_map = {s["span_id"]: s for s in spans}
    roots = []
    for span in spans:
        parent = span.get("parent_span_id")
        if parent and parent in span_map:
            span_map[parent].setdefault("children", []).append(span)
        else:
            roots.append(span)

    services = list({s["service_name"] for s in spans})
    error_spans = [s for s in spans if s.get("status_code") == "ERROR"]

    return {
        "trace_id": trace_id,
        "span_count": len(spans),
        "services": services,
        "has_errors": len(error_spans) > 0,
        "error_count": len(error_spans),
        "root_spans": roots,
    }


@router.get(
    "/traces",
    summary="List traces (filterable by service, errors only, time range)",
)
async def list_traces(
    service: str | None = Query(None),
    error_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
) -> dict:
    """
    Returns unique trace_ids with summary info.
    Useful for finding distributed bugs across services.
    """
    pg = get_postgres()
    traces = await pg.list_traces(service=service, error_only=error_only, limit=limit)
    return {"count": len(traces), "traces": traces}


@router.get(
    "/topology",
    summary="Service call topology graph",
)
async def get_topology() -> dict:
    """
    Returns the current service→service call graph built from ingested traces.
    Shows call counts, error counts, and average latency per edge.
    """
    pg = get_postgres()
    edges = await pg.get_topology()
    # Build adjacency list for easier frontend consumption
    services: set[str] = set()
    for edge in edges:
        services.add(edge["source_service"])
        services.add(edge["target_service"])

    return {
        "service_count": len(services),
        "edge_count": len(edges),
        "services": sorted(services),
        "edges": edges,
    }


@router.post(
    "/deploy",
    summary="Ingest a deploy event from CI/CD",
    status_code=201,
)
async def ingest_deploy_event(event: DeployEventRequest) -> dict:
    """
    Record a deploy event. Used by BlameAttributionEngine to correlate
    which commit was deployed before an error occurred.

    Call from CI/CD pipeline after successful deploy:
    curl -X POST http://dara:8000/api/v1/deploy \\
      -H 'Content-Type: application/json' \\
      -d '{"service_name": "api", "commit_sha": "abc123", "deployed_by": "github-actions"}'
    """
    pg = get_postgres()
    deploy_id = await pg.save_deploy_event(event.model_dump())
    logger.info(
        "Deploy event ingested: service=%s sha=%s env=%s",
        event.service_name, event.commit_sha[:8], event.environment,
    )
    return {"id": deploy_id, "status": "recorded"}
