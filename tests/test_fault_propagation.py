"""
Tests: Week 7-8 — Neo4j Service Topology + Fault Propagation Mapper
Covers:
  - ServiceTopologyBuilder edge extraction (cross-service, same-service, multi-hop)
  - FaultPropagationMapper: root error detection, propagation path ordering
  - FaultPropagationMapper: confidence scoring rules
  - FaultCascade properties (is_distributed_bug, summary)
  - Neo4j queries module imports and Cypher template validation
  - Healthy trace (no error spans) → empty cascade
  - Single-service error → confidence scoring
  - Multi-service chain: A→B(ERROR)→C(ERROR) — root=B, propagation=[B,C]
"""
import sys
sys.path.insert(0, ".")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest


# ── Helpers ────────────────────────────────────────────────────

def make_span(
    trace_id="t1",
    span_id="s1",
    parent_span_id=None,
    service="svc-a",
    operation="op",
    status="OK",
    message=None,
    duration_ms=100,
    attrs=None,
    started_offset_sec=0,
):
    from ingestion.otel_receiver import NormalisedSpan
    return NormalisedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        service_name=service,
        operation_name=operation,
        status_code=status,
        status_message=message,
        duration_ms=duration_ms,
        started_at=datetime(2026, 4, 18, 10, 0, started_offset_sec, tzinfo=timezone.utc),
        attributes=attrs or {},
    )


# ── ServiceTopologyBuilder Tests ──────────────────────────────

class TestServiceTopologyBuilder:

    def _builder(self):
        from graph.topology import ServiceTopologyBuilder
        return ServiceTopologyBuilder(neo4j=None)

    def test_single_cross_service_edge_extracted(self):
        builder = self._builder()
        spans = [
            make_span(span_id="s1", parent_span_id=None, service="api", operation="GET /"),
            make_span(span_id="s2", parent_span_id="s1", service="payment", operation="charge"),
        ]
        edges = builder._extract_edges(spans)
        assert len(edges) == 1
        assert edges[0].source == "api"
        assert edges[0].target == "payment"
        assert edges[0].call_count == 1

    def test_error_span_increments_error_count(self):
        builder = self._builder()
        spans = [
            make_span(span_id="s1", service="api"),
            make_span(span_id="s2", parent_span_id="s1", service="db", status="ERROR"),
        ]
        edges = builder._extract_edges(spans)
        assert edges[0].error_count == 1

    def test_same_service_spans_produce_no_edges(self):
        builder = self._builder()
        spans = [
            make_span(span_id="s1", service="api"),
            make_span(span_id="s2", parent_span_id="s1", service="api"),
            make_span(span_id="s3", parent_span_id="s2", service="api"),
        ]
        edges = builder._extract_edges(spans)
        assert len(edges) == 0

    def test_multi_hop_chain_creates_multiple_edges(self):
        builder = self._builder()
        spans = [
            make_span(span_id="s1", service="frontend"),
            make_span(span_id="s2", parent_span_id="s1", service="api-gateway"),
            make_span(span_id="s3", parent_span_id="s2", service="payment-svc"),
            make_span(span_id="s4", parent_span_id="s3", service="db-svc"),
        ]
        edges = builder._extract_edges(spans)
        # 3 cross-service edges: frontend→api-gateway, api-gateway→payment, payment→db
        assert len(edges) == 3
        sources = {e.source for e in edges}
        assert "frontend" in sources
        assert "api-gateway" in sources

    def test_latency_averaged_over_multiple_child_spans(self):
        builder = self._builder()
        spans = [
            make_span(span_id="s1", service="api"),
            make_span(span_id="s2", parent_span_id="s1", service="cache", duration_ms=10),
            make_span(span_id="s3", parent_span_id="s1", service="cache", duration_ms=30),
        ]
        edges = builder._extract_edges(spans)
        assert len(edges) == 1
        assert edges[0].avg_latency_ms == 20.0


# ── FaultPropagationMapper Tests ──────────────────────────────

class TestFaultPropagationMapper:

    @pytest.mark.asyncio
    async def test_healthy_trace_returns_empty_cascade(self):
        from graph.fault_propagation import FaultPropagationMapper
        mapper = FaultPropagationMapper()
        spans = [
            make_span(span_id="s1", service="api", status="OK"),
            make_span(span_id="s2", parent_span_id="s1", service="db", status="OK"),
        ]
        cascade = await mapper.analyze("trace-ok", spans)
        assert cascade.error_span_count == 0
        assert cascade.affected_services == []
        assert not cascade.is_distributed_bug
        assert cascade.blame_confidence == 0.0

    @pytest.mark.asyncio
    async def test_single_error_span_identified_as_root(self):
        from graph.fault_propagation import FaultPropagationMapper
        mapper = FaultPropagationMapper()
        spans = [
            make_span(span_id="s1", service="api", status="OK"),
            make_span(span_id="s2", parent_span_id="s1", service="payment",
                      status="ERROR", message="timeout"),
        ]
        cascade = await mapper.analyze("trace-single-err", spans)
        assert cascade.root_service == "payment"
        assert cascade.error_span_count == 1
        assert cascade.root_error_message == "timeout"

    @pytest.mark.asyncio
    async def test_multi_service_cascade_correct_root(self):
        """
        api(OK) → payment(ERROR) → notification(ERROR)
        Root must be payment (payment's parent = api = OK).
        notification's parent = payment = ERROR → propagated.
        """
        from graph.fault_propagation import FaultPropagationMapper
        mapper = FaultPropagationMapper()
        spans = [
            make_span(span_id="s1", service="api", status="OK", started_offset_sec=0),
            make_span(span_id="s2", parent_span_id="s1", service="payment",
                      status="ERROR", started_offset_sec=1),
            make_span(span_id="s3", parent_span_id="s2", service="notification",
                      status="ERROR", started_offset_sec=2),
        ]
        cascade = await mapper.analyze("trace-cascade", spans)
        assert cascade.root_service == "payment"
        assert cascade.is_distributed_bug
        assert "notification" in cascade.propagation_path
        assert cascade.propagation_path[0] == "payment"

    @pytest.mark.asyncio
    async def test_blame_confidence_increases_with_code_filepath_attr(self):
        from graph.fault_propagation import FaultPropagationMapper
        mapper = FaultPropagationMapper()
        spans_no_attr = [
            make_span(span_id="s1", service="api", status="OK"),
            make_span(span_id="s2", parent_span_id="s1", service="db", status="ERROR"),
        ]
        spans_with_attr = [
            make_span(span_id="s1", service="api", status="OK"),
            make_span(span_id="s2", parent_span_id="s1", service="db",
                      status="ERROR", attrs={"code.filepath": "db/queries.py"}),
        ]
        cascade_no_attr = await mapper.analyze("t1", spans_no_attr)
        cascade_with_attr = await mapper.analyze("t2", spans_with_attr)
        assert cascade_with_attr.blame_confidence > cascade_no_attr.blame_confidence

    @pytest.mark.asyncio
    async def test_empty_spans_returns_empty_cascade(self):
        from graph.fault_propagation import FaultPropagationMapper
        mapper = FaultPropagationMapper()
        cascade = await mapper.analyze("trace-empty", [])
        assert cascade.root_service == "unknown"
        assert cascade.blame_confidence == 0.0

    @pytest.mark.asyncio
    async def test_fault_cascade_summary_string(self):
        from graph.fault_propagation import FaultCascade
        cascade = FaultCascade(
            trace_id="t1",
            root_service="payment-svc",
            root_span_id="s2",
            root_operation="charge",
            propagation_path=["payment-svc", "notify-svc"],
            affected_services=["payment-svc", "notify-svc"],
            non_error_services=["api"],
            total_span_count=3,
            error_span_count=2,
            root_error_message="DB timeout",
            root_file_path="payment.py",
            root_function="charge_card",
            blame_confidence=0.85,
        )
        summary = cascade.summary
        assert "payment-svc" in summary
        assert "85%" in summary


# ── Cypher Queries Module Tests ───────────────────────────────

class TestCypherQueries:

    def test_all_cypher_templates_importable(self):
        from graph.queries import (
            UPSERT_SERVICE, UPSERT_SERVICE_CALL_EDGE,
            GET_UPSTREAM_SERVICES, GET_DOWNSTREAM_SERVICES,
            GET_FULL_TOPOLOGY, STORE_FAILURE_CASCADE, INDEXES,
        )
        assert "MERGE" in UPSERT_SERVICE
        assert "CALLS" in UPSERT_SERVICE_CALL_EDGE
        assert len(INDEXES) >= 7

    def test_indexes_list_contains_service_index(self):
        from graph.queries import INDEXES
        service_indexes = [i for i in INDEXES if "Service" in i]
        assert len(service_indexes) >= 1
