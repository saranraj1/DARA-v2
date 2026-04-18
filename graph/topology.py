"""
DARA — Service Topology Builder (Week 7-8)
============================================
Builds and maintains a live Neo4j graph of how services call each other.
Updated on every OTel trace ingestion.

Graph schema:
  (:Service {name, language, repo_full_name, registered_at})
    -[:CALLS {call_count, error_count, avg_latency_ms, last_seen}]->
  (:Service)

  (:Service)-[:HOSTS]->(:Endpoint {path, method, service_name})

Usage:
    from graph.topology import ServiceTopologyBuilder
    builder = ServiceTopologyBuilder(neo4j=get_neo4j())
    await builder.update_from_trace(spans)
    deps = await builder.get_upstream_services("payment-service")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ServiceNode:
    name: str
    language: str | None = None
    repo_full_name: str | None = None


@dataclass
class TopologyEdge:
    source: str
    target: str
    call_count: int = 0
    error_count: int = 0
    avg_latency_ms: float | None = None
    last_seen: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class ServiceTopologyBuilder:
    """
    Builds and queries the service dependency graph in Neo4j.
    Called by SpanIngester after every trace batch is ingested.
    """

    def __init__(self, neo4j=None) -> None:
        self._neo4j = neo4j

    def _get_neo4j(self):
        if self._neo4j:
            return self._neo4j
        from storage.neo4j_client import get_neo4j
        return get_neo4j()

    # ── Public API ─────────────────────────────────────────────

    async def update_from_trace(self, spans: list) -> list[TopologyEdge]:
        """
        Extract cross-service call edges from a list of NormalisedSpan objects
        and upsert them into Neo4j. Returns the list of edges written.
        """
        edges = self._extract_edges(spans)
        if not edges:
            return []

        neo4j = self._get_neo4j()
        # Ensure all service nodes exist first
        services = {e.source for e in edges} | {e.target for e in edges}
        await self._upsert_services(neo4j, list(services))

        # Upsert each edge
        for edge in edges:
            await self._upsert_edge(neo4j, edge)

        logger.debug(
            "ServiceTopologyBuilder: updated %d edges from %d spans",
            len(edges), len(spans),
        )
        return edges

    async def get_upstream_services(self, service_name: str, max_hops: int = 3) -> list[dict]:
        """Return all services that transitively call this service (callers)."""
        neo4j = self._get_neo4j()
        return await neo4j.get_upstream_services(service_name, max_hops)

    async def get_downstream_services(self, service_name: str, max_hops: int = 3) -> list[dict]:
        """Return all services this service calls downstream."""
        neo4j = self._get_neo4j()
        return await neo4j.get_downstream_services(service_name, max_hops)

    async def get_full_topology(self) -> dict:
        """Return full service graph as nodes + edges for visualisation."""
        neo4j = self._get_neo4j()
        return await neo4j.get_full_topology()

    async def register_service(
        self, name: str, repo_full_name: str, language: str | None = None
    ) -> None:
        """Register a known service in Neo4j (upsert). Also saves to Postgres ServiceRegistry."""
        neo4j = self._get_neo4j()
        await neo4j.upsert_service_node(
            name=name, repo_full_name=repo_full_name, language=language
        )

    # ── Private helpers ────────────────────────────────────────

    def _extract_edges(self, spans: list) -> list[TopologyEdge]:
        """
        Build cross-service edges from parent→child span relationships.
        Only edges where parent.service != child.service are kept.
        """
        span_map: dict[str, Any] = {s.span_id: s for s in spans}
        edge_stats: dict[tuple[str, str], dict] = {}

        for span in spans:
            if not span.parent_span_id:
                continue
            parent = span_map.get(span.parent_span_id)
            if parent is None:
                continue
            if parent.service_name == span.service_name:
                continue  # same-service call — skip

            key = (parent.service_name, span.service_name)
            if key not in edge_stats:
                edge_stats[key] = {"call_count": 0, "error_count": 0, "latencies": []}

            edge_stats[key]["call_count"] += 1
            if span.status_code == "ERROR":
                edge_stats[key]["error_count"] += 1
            if span.duration_ms:
                edge_stats[key]["latencies"].append(span.duration_ms)

        edges = []
        for (src, tgt), stats in edge_stats.items():
            latencies = stats["latencies"]
            avg_lat = sum(latencies) / len(latencies) if latencies else None
            edges.append(TopologyEdge(
                source=src, target=tgt,
                call_count=stats["call_count"],
                error_count=stats["error_count"],
                avg_latency_ms=avg_lat,
            ))
        return edges

    async def _upsert_services(self, neo4j, service_names: list[str]) -> None:
        """Ensure all service nodes exist in Neo4j (MERGE)."""
        for name in service_names:
            await neo4j.upsert_service_node(name=name)

    async def _upsert_edge(self, neo4j, edge: TopologyEdge) -> None:
        """Upsert a CALLS relationship between two Service nodes."""
        await neo4j.upsert_service_call_edge(
            source=edge.source,
            target=edge.target,
            call_count=edge.call_count,
            error_count=edge.error_count,
            avg_latency_ms=edge.avg_latency_ms,
        )
