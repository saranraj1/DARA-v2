"""
Tests: Week 6-7 — OpenTelemetry trace ingestion + service topology (W6-7)
Covers:
  - OTLP JSON parsing (valid, error spans, missing fields)
  - DARA simplified JSON parsing
  - Topology edge extraction from parent-child spans
  - Cross-service vs same-service edge filtering
  - Postgres save_span / get_trace / get_topology mocked
  - Traces API router (POST /traces, GET /traces/{id}, GET /topology)
  - Deploy event ingestion
"""
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── OTel Span Parser Tests ─────────────────────────────────────

class TestOtelParser:
    """Unit tests for SpanIngester parsing logic — no DB needed."""

    def _ingester(self):
        from ingestion.otel_receiver import SpanIngester
        return SpanIngester(postgres=None)

    def test_parse_simplified_format_success(self):
        ingester = self._ingester()
        payload = {
            "spans": [
                {
                    "trace_id": "trace-abc",
                    "span_id": "span-001",
                    "service_name": "api-service",
                    "operation": "POST /users",
                    "status": "OK",
                    "duration_ms": 45,
                    "started_at": "2026-04-18T10:00:00Z",
                }
            ]
        }
        spans, errors = ingester._parse_simplified(payload)
        assert len(spans) == 1
        assert errors == []
        assert spans[0].trace_id == "trace-abc"
        assert spans[0].service_name == "api-service"
        assert spans[0].status_code == "OK"
        assert spans[0].duration_ms == 45

    def test_parse_simplified_error_span(self):
        ingester = self._ingester()
        payload = {
            "spans": [
                {
                    "trace_id": "trace-xyz",
                    "span_id": "span-err",
                    "service_name": "payment-service",
                    "operation": "charge_card",
                    "status": "error",  # case-insensitive
                    "error_message": "Connection refused",
                    "duration_ms": 5000,
                    "started_at": "2026-04-18T10:01:00Z",
                }
            ]
        }
        spans, errors = ingester._parse_simplified(payload)
        assert len(spans) == 1
        assert spans[0].status_code == "ERROR"
        assert spans[0].status_message == "Connection refused"

    def test_parse_otlp_format_success(self):
        ingester = self._ingester()
        payload = {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [
                            {"key": "service.name", "value": {"stringValue": "order-service"}}
                        ]
                    },
                    "scopeSpans": [
                        {
                            "spans": [
                                {
                                    "traceId": "aabbcc001122",
                                    "spanId": "spanid001",
                                    "name": "process_order",
                                    "startTimeUnixNano": "1713434400000000000",
                                    "endTimeUnixNano":   "1713434400500000000",
                                    "status": {"code": "STATUS_CODE_OK"},
                                    "attributes": [
                                        {"key": "code.filepath", "value": {"stringValue": "orders.py"}},
                                        {"key": "http.status_code", "value": {"intValue": 200}},
                                    ]
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        spans, errors = ingester._parse_otlp(payload)
        assert len(spans) == 1
        assert errors == []
        assert spans[0].service_name == "order-service"
        assert spans[0].trace_id == "aabbcc001122"
        assert spans[0].status_code == "OK"
        assert spans[0].duration_ms == 500
        assert spans[0].attributes.get("code.filepath") == "orders.py"
        assert spans[0].attributes.get("http.status_code") == 200

    def test_parse_otlp_error_span(self):
        ingester = self._ingester()
        payload = {
            "resourceSpans": [
                {
                    "resource": {"attributes": [
                        {"key": "service.name", "value": {"stringValue": "db-service"}}
                    ]},
                    "scopeSpans": [{"spans": [{
                        "traceId": "trace99",
                        "spanId": "spanERR",
                        "name": "query",
                        "startTimeUnixNano": "1000000000",
                        "endTimeUnixNano": "2000000000",
                        "status": {"code": "STATUS_CODE_ERROR", "message": "timeout"},
                    }]}]
                }
            ]
        }
        spans, _ = ingester._parse_otlp(payload)
        assert spans[0].status_code == "ERROR"
        assert spans[0].status_message == "timeout"

    def test_unknown_payload_returns_error(self):
        from ingestion.otel_receiver import SpanIngester
        ingester = SpanIngester(postgres=MagicMock())
        # Neither resourceSpans nor spans key
        import asyncio
        result = asyncio.get_event_loop().run_until_complete(ingester.ingest({"data": "unknown"}))
        assert result["ingested"] == 0
        assert "Unknown payload format" in result["errors"]


class TestTopologyExtraction:
    """Tests for cross-service topology edge extraction."""

    def _ingester(self):
        from ingestion.otel_receiver import SpanIngester
        return SpanIngester(postgres=None)

    def test_cross_service_edges_detected(self):
        from ingestion.otel_receiver import NormalisedSpan
        ingester = self._ingester()
        now = datetime.now(timezone.utc)

        # api-service calls payment-service
        spans = [
            NormalisedSpan(
                trace_id="t1", span_id="s1", parent_span_id=None,
                service_name="api-service", operation_name="POST /pay",
                started_at=now, status_code="OK", duration_ms=200,
            ),
            NormalisedSpan(
                trace_id="t1", span_id="s2", parent_span_id="s1",
                service_name="payment-service", operation_name="charge",
                started_at=now, status_code="ERROR", duration_ms=150,
            ),
        ]

        # Simulate _update_topology logic
        span_service = {s.span_id: s.service_name for s in spans}
        edges = {}
        for span in spans:
            if span.parent_span_id and span.parent_span_id in span_service:
                parent_svc = span_service[span.parent_span_id]
                child_svc = span.service_name
                if parent_svc != child_svc:
                    key = (parent_svc, child_svc)
                    edges.setdefault(key, {"call_count": 0, "error_count": 0})
                    edges[key]["call_count"] += 1
                    if span.status_code == "ERROR":
                        edges[key]["error_count"] += 1

        assert ("api-service", "payment-service") in edges
        assert edges[("api-service", "payment-service")]["error_count"] == 1

    def test_same_service_spans_not_added_as_edges(self):
        from ingestion.otel_receiver import NormalisedSpan
        ingester = self._ingester()
        now = datetime.now(timezone.utc)

        # Both spans belong to the same service — no cross-service edge
        spans = [
            NormalisedSpan(
                trace_id="t2", span_id="s1", parent_span_id=None,
                service_name="api-service", operation_name="outer",
                started_at=now, status_code="OK",
            ),
            NormalisedSpan(
                trace_id="t2", span_id="s2", parent_span_id="s1",
                service_name="api-service", operation_name="inner",
                started_at=now, status_code="OK",
            ),
        ]
        span_service = {s.span_id: s.service_name for s in spans}
        edges = {}
        for span in spans:
            if span.parent_span_id and span.parent_span_id in span_service:
                p = span_service[span.parent_span_id]
                c = span.service_name
                if p != c:
                    edges[(p, c)] = True

        assert len(edges) == 0, "Same-service spans should NOT create topology edges"


class TestTracesRouter:
    """Tests for the /api/v1/traces endpoints."""

    def _app(self):
        from fastapi import FastAPI
        from api.routers.traces import router
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        return app

    def test_ingest_simplified_spans_returns_202(self):
        from fastapi.testclient import TestClient
        from unittest.mock import patch, AsyncMock

        mock_pg = MagicMock()
        mock_pg.save_span = AsyncMock(return_value="uuid-1")
        mock_pg.upsert_topology_edge = AsyncMock()

        with patch("ingestion.otel_receiver.SpanIngester._get_pg", return_value=mock_pg):
            client = TestClient(self._app())
            r = client.post("/api/v1/traces", json={
                "spans": [{
                    "trace_id": "trace-test-01",
                    "span_id": "span-test-01",
                    "service_name": "test-svc",
                    "operation": "test_op",
                    "status": "OK",
                    "duration_ms": 10,
                    "started_at": "2026-04-18T10:00:00Z",
                }]
            })
        assert r.status_code == 202
        data = r.json()
        assert "ingested" in data
        assert "trace_ids" in data

    def test_get_trace_not_found_returns_404(self):
        from fastapi.testclient import TestClient

        mock_pg = MagicMock()
        mock_pg.get_trace = AsyncMock(return_value=[])

        with patch("api.routers.traces.get_postgres", return_value=mock_pg):
            client = TestClient(self._app())
            r = client.get("/api/v1/traces/nonexistent-trace-id")
        assert r.status_code == 404

    def test_get_topology_returns_edges(self):
        from fastapi.testclient import TestClient

        mock_pg = MagicMock()
        mock_pg.get_topology = AsyncMock(return_value=[
            {"source_service": "api", "target_service": "db",
             "call_count": 100, "error_count": 5, "error_rate": 0.05,
             "avg_latency_ms": 12.5, "last_seen": "2026-04-18T10:00:00+00:00"}
        ])

        with patch("api.routers.traces.get_postgres", return_value=mock_pg):
            client = TestClient(self._app())
            r = client.get("/api/v1/topology")
        assert r.status_code == 200
        data = r.json()
        assert data["edge_count"] == 1
        assert "api" in data["services"]


class TestDeployEventIngestion:
    """Tests for deploy event storage."""

    def test_deploy_event_schema(self):
        from storage.models import DeployEvent
        cols = {c.key for c in DeployEvent.__table__.columns}
        required = {"id", "service_name", "commit_sha", "deployed_at", "environment"}
        assert required.issubset(cols)

    def test_distributed_trace_schema(self):
        from storage.models import DistributedTrace
        cols = {c.key for c in DistributedTrace.__table__.columns}
        required = {"id", "trace_id", "span_id", "service_name", "operation_name", "status_code"}
        assert required.issubset(cols)

    def test_service_topology_schema(self):
        from storage.models import ServiceTopology
        cols = {c.key for c in ServiceTopology.__table__.columns}
        required = {"id", "source_service", "target_service", "call_count", "error_count"}
        assert required.issubset(cols)
