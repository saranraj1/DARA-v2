"""
DARA — Neo4j Async Graph Client
Handles connections, Cypher query execution, and batch operations
for the call graph and institutional memory graph.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, Record

from config.settings import get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """
    Async Neo4j driver wrapper with typed query helpers.
    Uses connection pooling with max_connection_lifetime and retry logic.
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=50,
            connection_acquisition_timeout=30,
        )

    async def verify_connectivity(self) -> bool:
        try:
            await self._driver.verify_connectivity()
            return True
        except Exception as e:
            logger.error("Neo4j connectivity failed", extra={"error": str(e)})
            return False

    async def close(self) -> None:
        await self._driver.close()

    # ─── Core Query Execution ────────────────────────────────

    async def execute_query(
        self, cypher: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        """
        Execute a Cypher query and return list of record dicts.
        Automatically uses a new session per call.
        """
        async with self._driver.session() as session:
            result = await session.run(cypher, params or {})
            records: list[Record] = await result.fetch(-1)
            return [dict(record) for record in records]

    async def execute_write(
        self, cypher: str, params: dict[str, Any] | None = None
    ) -> list[dict]:
        """Execute a write transaction with automatic retry on transient errors."""
        async with self._driver.session() as session:
            result = await session.execute_write(
                lambda tx: tx.run(cypher, params or {})
            )
            return [dict(r) for r in await result.fetch(-1)]  # type: ignore

    async def batch_create_nodes(self, nodes: list[dict], label: str) -> None:
        """
        Efficient batch node creation using UNWIND.
        Each dict in nodes must have an 'id' field.
        """
        cypher = f"""
        UNWIND $nodes AS node
        MERGE (n:{label} {{id: node.id}})
        SET n += node
        """
        await self.execute_write(cypher, {"nodes": nodes})
        logger.debug(
            "Batch created nodes",
            extra={"label": label, "count": len(nodes)},
        )

    async def create_indexes(self) -> None:
        """Create all required Neo4j indexes on first startup."""
        from graph.queries import INDEXES
        for cypher in INDEXES:
            try:
                await self.execute_write(cypher)
            except Exception as e:
                logger.debug("Index creation skipped", extra={"error": str(e)})

    # ─── Service Topology (Week 7-8) ─────────────────────────

    async def upsert_service_node(
        self,
        name: str,
        repo_full_name: str | None = None,
        language: str | None = None,
    ) -> None:
        """Create or update a Service node in Neo4j."""
        from graph.queries import UPSERT_SERVICE
        await self.execute_write(
            UPSERT_SERVICE,
            {"name": name, "repo_full_name": repo_full_name, "language": language},
        )

    async def upsert_service_call_edge(
        self,
        source: str,
        target: str,
        call_count: int = 1,
        error_count: int = 0,
        avg_latency_ms: float | None = None,
    ) -> None:
        """Upsert a CALLS relationship between two Service nodes."""
        from graph.queries import UPSERT_SERVICE_CALL_EDGE
        await self.execute_write(
            UPSERT_SERVICE_CALL_EDGE,
            {
                "source": source,
                "target": target,
                "call_count": call_count,
                "error_count": error_count,
                "avg_latency_ms": avg_latency_ms,
            },
        )

    async def get_upstream_services(
        self, service_name: str, max_hops: int = 3
    ) -> list[dict]:
        """Return all services that call this service (transitively)."""
        from graph.queries import GET_UPSTREAM_SERVICES
        return await self.execute_query(
            GET_UPSTREAM_SERVICES,
            {"name": service_name, "hops": max_hops},
        )

    async def get_downstream_services(
        self, service_name: str, max_hops: int = 3
    ) -> list[dict]:
        """Return all services this service calls (transitively)."""
        from graph.queries import GET_DOWNSTREAM_SERVICES
        return await self.execute_query(
            GET_DOWNSTREAM_SERVICES,
            {"name": service_name, "hops": max_hops},
        )

    async def get_full_topology(self) -> dict:
        """Return full service graph as {nodes, edges}."""
        from graph.queries import GET_FULL_TOPOLOGY, GET_ALL_SERVICE_NODES
        edges = await self.execute_query(GET_FULL_TOPOLOGY)
        nodes = await self.execute_query(GET_ALL_SERVICE_NODES)
        return {"nodes": nodes, "edges": edges}

    # ─── Call Graph Operations ───────────────────────────────

    async def get_callers(
        self, function_name: str, service: str, max_hops: int = 3
    ) -> list[dict]:
        """Return all functions that transitively call the given function."""
        cypher = """
        MATCH path = (caller:Function)-[:CALLS*1..$hops]->(target:Function {name: $name})
        WHERE caller.service = $service OR target.service = $service
        RETURN DISTINCT
            caller.name AS caller_name,
            caller.file_path AS file_path,
            caller.service AS service,
            length(path) AS hop_count
        ORDER BY hop_count ASC
        LIMIT 20
        """
        return await self.execute_query(
            cypher,
            {"name": function_name, "service": service, "hops": max_hops},
        )

    async def store_call_edge(
        self, caller_id: str, callee_id: str
    ) -> None:
        cypher = """
        MERGE (a:Function {id: $caller_id})
        MERGE (b:Function {id: $callee_id})
        MERGE (a)-[:CALLS]->(b)
        """
        await self.execute_write(cypher, {"caller_id": caller_id, "callee_id": callee_id})

    # ─── Failure Cascade (enhanced Week 7-8) ─────────────────

    async def store_failure_cascade(
        self,
        trace_id: str,
        root_service: str,
        affected_services: list[str],
        propagation_path: list[str] | None = None,
        blame_confidence: float = 0.0,
        error_message: str | None = None,
    ) -> None:
        """Persist or update a FailureCascade node. Also links to root Service."""
        from graph.queries import STORE_FAILURE_CASCADE, LINK_CASCADE_TO_SERVICE
        await self.execute_write(
            STORE_FAILURE_CASCADE,
            {
                "trace_id": trace_id,
                "root_service": root_service,
                "affected_services": affected_services,
                "propagation_path": propagation_path or [root_service],
                "blame_confidence": blame_confidence,
                "error_message": error_message,
            },
        )
        try:
            await self.execute_write(
                LINK_CASCADE_TO_SERVICE,
                {"trace_id": trace_id, "service_name": root_service},
            )
        except Exception:
            pass  # Service node may not exist yet — non-fatal

    async def find_similar_cascades(
        self,
        root_service: str,
        limit: int = 5,
        exclude_trace_id: str = "",
    ) -> list[dict]:
        """Find past cascades with the same root service."""
        from graph.queries import FIND_SIMILAR_CASCADES
        return await self.execute_query(
            FIND_SIMILAR_CASCADES,
            {
                "root_service": root_service,
                "limit": limit,
                "exclude_trace_id": exclude_trace_id,
            },
        )

    # ─── Institutional Memory Graph ──────────────────────────

    async def record_error_pattern(
        self, error_class: str, signature: str, frequency: int = 1
    ) -> None:
        cypher = """
        MERGE (p:ErrorPattern {error_class: $error_class, signature: $signature})
        ON CREATE SET p.frequency = $frequency, p.first_seen = datetime()
        ON MATCH SET p.frequency = p.frequency + $frequency, p.last_seen = datetime()
        """
        await self.execute_write(
            cypher,
            {"error_class": error_class, "signature": signature, "frequency": frequency},
        )

    async def link_fix_to_pattern(
        self, error_class: str, signature: str, fix_template_id: str, outcome: str
    ) -> None:
        cypher = """
        MERGE (p:ErrorPattern {error_class: $error_class, signature: $signature})
        MERGE (t:FixTemplate {template_id: $template_id})
        MERGE (p)-[r:RESOLVED_BY]->(t)
        SET r.outcome = $outcome, r.recorded_at = datetime()
        """
        await self.execute_write(
            cypher,
            {
                "error_class": error_class,
                "signature": signature,
                "template_id": fix_template_id,
                "outcome": outcome,
            },
        )


_neo4j_instance: Neo4jClient | None = None


def get_neo4j() -> Neo4jClient:
    global _neo4j_instance
    if _neo4j_instance is None:
        s = get_settings()
        _neo4j_instance = Neo4jClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    return _neo4j_instance

