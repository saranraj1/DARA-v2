"""
DARA — OpenTelemetry Span Ingester
=====================================
Accepts OTel spans in JSON format (OTLP/HTTP JSON format or simplified DARA format),
normalises them into DistributedTrace rows, persists to Postgres, and
updates the service_topology table with call-graph edges.

Supports two ingestion formats:
  1. OTLP JSON (as sent by OTel SDK with otlp/http exporter)
  2. DARA Simplified JSON (easier for testing / manual submission)

OTLP JSON structure:
  {
    "resourceSpans": [{
      "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "api-service"}}]},
      "scopeSpans": [{
        "spans": [{
          "traceId": "...", "spanId": "...", "parentSpanId": "...",
          "name": "GET /users",
          "status": {"code": "STATUS_CODE_ERROR", "message": "..."},
          "startTimeUnixNano": "1234567890000000000",
          "endTimeUnixNano":   "1234567890500000000",
          "attributes": [{"key": "http.status_code", "value": {"intValue": 500}}]
        }]
      }]
    }]
  }

DARA Simplified JSON (convenience, for manual submission):
  {
    "spans": [
      {
        "trace_id": "abc123",
        "span_id": "def456",
        "parent_span_id": "ghi789",   # optional
        "service_name": "payment-service",
        "operation": "process_payment",
        "status": "ERROR",
        "error_message": "Connection refused",
        "duration_ms": 532,
        "started_at": "2026-04-18T10:00:00Z",
        "attributes": {"code.filepath": "payment.py", "code.function": "charge"}
      }
    ]
  }
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Normalised span dataclass ─────────────────────────────────

@dataclass
class NormalisedSpan:
    trace_id: str
    span_id: str
    service_name: str
    operation_name: str
    started_at: datetime
    parent_span_id: str | None = None
    status_code: str | None = None
    status_message: str | None = None
    duration_ms: int | None = None
    attributes: dict = field(default_factory=dict)
    events: dict = field(default_factory=dict)


# ── OTel receiver / normaliser ────────────────────────────────

class SpanIngester:
    """
    Receives raw OTel payload (OTLP JSON or DARA simplified JSON),
    normalises spans, persists to Postgres, and updates topology.
    """

    def __init__(self, postgres=None) -> None:
        self._pg = postgres

    def _get_pg(self):
        if self._pg:
            return self._pg
        from storage.postgres import get_postgres
        return get_postgres()

    # ── Public entry point ─────────────────────────────────────

    async def ingest(self, payload: dict) -> dict:
        """
        Main entry point. Detects format, normalises, persists.
        Returns: {"ingested": N, "trace_ids": [...], "errors": [...]}
        """
        spans: list[NormalisedSpan] = []
        errors: list[str] = []

        if "resourceSpans" in payload:
            spans, errors = self._parse_otlp(payload)
        elif "spans" in payload:
            spans, errors = self._parse_simplified(payload)
        else:
            return {"ingested": 0, "trace_ids": [], "errors": ["Unknown payload format"]}

        if not spans:
            return {"ingested": 0, "trace_ids": [], "errors": errors}

        pg = self._get_pg()
        saved = 0
        trace_ids: set[str] = set()

        for span in spans:
            try:
                await pg.save_span(span)
                trace_ids.add(span.trace_id)
                saved += 1
            except Exception as e:
                logger.warning("SpanIngester: failed to save span %s: %s", span.span_id, e)
                errors.append(str(e))

        # Update service topology from this batch of spans
        await self._update_topology(spans, pg)

        logger.info(
            "SpanIngester: ingested %d/%d spans, %d traces",
            saved, len(spans), len(trace_ids),
        )
        return {
            "ingested": saved,
            "trace_ids": sorted(trace_ids),
            "errors": errors,
        }

    # ── OTLP JSON parser ──────────────────────────────────────

    def _parse_otlp(self, payload: dict) -> tuple[list[NormalisedSpan], list[str]]:
        spans: list[NormalisedSpan] = []
        errors: list[str] = []

        for resource_span in payload.get("resourceSpans", []):
            # Extract service name from resource attributes
            service_name = "unknown"
            for attr in resource_span.get("resource", {}).get("attributes", []):
                if attr.get("key") == "service.name":
                    service_name = (
                        attr.get("value", {}).get("stringValue")
                        or attr.get("value", {}).get("string_value")
                        or "unknown"
                    )
                    break

            for scope_span in resource_span.get("scopeSpans", []):
                for raw in scope_span.get("spans", []):
                    try:
                        span = self._parse_otlp_span(raw, service_name)
                        spans.append(span)
                    except Exception as e:
                        errors.append(f"OTLP span parse error: {e}")

        return spans, errors

    def _parse_otlp_span(self, raw: dict, service_name: str) -> NormalisedSpan:
        """Convert a single OTLP span dict to NormalisedSpan."""
        # OTel time is nanoseconds since epoch
        start_ns = int(raw.get("startTimeUnixNano", 0))
        end_ns = int(raw.get("endTimeUnixNano", 0))
        started_at = datetime.fromtimestamp(start_ns / 1e9, tz=timezone.utc) if start_ns else datetime.now(timezone.utc)
        duration_ms = int((end_ns - start_ns) / 1e6) if end_ns and start_ns else None

        # Status
        status_obj = raw.get("status", {})
        status_code_raw = status_obj.get("code", "STATUS_CODE_UNSET")
        status_code = "ERROR" if "ERROR" in str(status_code_raw).upper() else "OK"

        # Attributes: flatten OTel attribute list → dict
        attributes: dict = {}
        for attr in raw.get("attributes", []):
            key = attr.get("key", "")
            val_obj = attr.get("value", {})
            for val_type in ("stringValue", "intValue", "boolValue", "doubleValue",
                             "string_value", "int_value", "bool_value", "double_value"):
                if val_type in val_obj:
                    attributes[key] = val_obj[val_type]
                    break

        return NormalisedSpan(
            trace_id=raw.get("traceId", raw.get("trace_id", "")),
            span_id=raw.get("spanId", raw.get("span_id", "")),
            parent_span_id=raw.get("parentSpanId") or raw.get("parent_span_id") or None,
            service_name=service_name,
            operation_name=raw.get("name", "unknown"),
            status_code=status_code,
            status_message=status_obj.get("message"),
            duration_ms=duration_ms,
            started_at=started_at,
            attributes=attributes,
        )

    # ── DARA simplified parser ────────────────────────────────

    def _parse_simplified(self, payload: dict) -> tuple[list[NormalisedSpan], list[str]]:
        spans: list[NormalisedSpan] = []
        errors: list[str] = []

        for raw in payload.get("spans", []):
            try:
                started_at_raw = raw.get("started_at", "")
                if isinstance(started_at_raw, str):
                    started_at = datetime.fromisoformat(started_at_raw.replace("Z", "+00:00"))
                else:
                    started_at = datetime.now(timezone.utc)

                spans.append(NormalisedSpan(
                    trace_id=str(raw.get("trace_id", "")),
                    span_id=str(raw.get("span_id", "")),
                    parent_span_id=raw.get("parent_span_id") or None,
                    service_name=str(raw.get("service_name", "unknown")),
                    operation_name=str(raw.get("operation", "unknown")),
                    status_code=str(raw.get("status", "OK")).upper(),
                    status_message=raw.get("error_message"),
                    duration_ms=raw.get("duration_ms"),
                    started_at=started_at,
                    attributes=raw.get("attributes") or {},
                ))
            except Exception as e:
                errors.append(f"Simplified span parse error: {e}")

        return spans, errors

    # ── Topology update ───────────────────────────────────────

    async def _update_topology(self, spans: list[NormalisedSpan], pg) -> None:
        """
        Build call edges from parent→child span relationships.
        Edge = (parent.service_name → child.service_name).
        Only cross-service edges are stored (same-service calls skipped).
        """
        # Build span_id → service map
        span_service: dict[str, str] = {s.span_id: s.service_name for s in spans}

        edges: dict[tuple[str, str], dict] = {}

        for span in spans:
            if span.parent_span_id and span.parent_span_id in span_service:
                parent_service = span_service[span.parent_span_id]
                child_service = span.service_name

                if parent_service != child_service:  # only cross-service edges
                    key = (parent_service, child_service)
                    if key not in edges:
                        edges[key] = {"call_count": 0, "error_count": 0, "latencies": []}
                    edges[key]["call_count"] += 1
                    if span.status_code == "ERROR":
                        edges[key]["error_count"] += 1
                    if span.duration_ms:
                        edges[key]["latencies"].append(span.duration_ms)

        for (src, tgt), stats in edges.items():
            avg_lat = (sum(stats["latencies"]) / len(stats["latencies"])
                       if stats["latencies"] else None)
            try:
                await pg.upsert_topology_edge(
                    source=src,
                    target=tgt,
                    call_count=stats["call_count"],
                    error_count=stats["error_count"],
                    avg_latency_ms=avg_lat,
                )
            except Exception as e:
                logger.warning("SpanIngester: topology upsert failed %s→%s: %s", src, tgt, e)
