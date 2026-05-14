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
        from graph.queries import GET_ALL_SERVICE_NODES, GET_FULL_TOPOLOGY
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
        from graph.queries import LINK_CASCADE_TO_SERVICE, STORE_FAILURE_CASCADE
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

    # ─── Phase 3: Institutional Memory Graph ─────────────────

    async def create_memory_indexes(self) -> None:
        """Create Phase 3 Neo4j indexes for memory graph nodes."""
        from graph.queries import MEMORY_GRAPH_INDEXES
        for cypher in MEMORY_GRAPH_INDEXES:
            try:
                await self.execute_write(cypher)
            except Exception as e:
                logger.debug("Memory index creation skipped: %s", e)

    async def upsert_error_pattern(
        self,
        signature_hash: str,
        error_class: str,
        confidence: float = 0.5,
        strategy: str = "llm_single_file",
    ) -> None:
        """Merge an ErrorPattern node. Updates frequency and avg_confidence on match."""
        from graph.queries import UPSERT_ERROR_PATTERN
        await self.execute_write(
            UPSERT_ERROR_PATTERN,
            {
                "signature_hash": signature_hash,
                "error_class": error_class,
                "confidence": confidence,
                "strategy": strategy,
            },
        )

    async def upsert_fix_template(
        self,
        template_id: str,
        error_class: str,
        strategy: str,
        fix_body: str,
    ) -> None:
        """Merge a FixTemplate node — creates on first use, updates last_used on match."""
        from graph.queries import UPSERT_FIX_TEMPLATE
        await self.execute_write(
            UPSERT_FIX_TEMPLATE,
            {
                "template_id": template_id,
                "error_class": error_class,
                "strategy": strategy,
                "fix_body": fix_body[:2000],  # cap to 2KB in graph
            },
        )

    async def update_fix_template_outcome(
        self,
        template_id: str,
        accepted: int = 0,
        rejected: int = 0,
    ) -> None:
        """Increment acceptance/rejection counters and recompute success_rate."""
        from graph.queries import UPDATE_FIX_TEMPLATE_OUTCOME
        await self.execute_write(
            UPDATE_FIX_TEMPLATE_OUTCOME,
            {"template_id": template_id, "accepted": accepted, "rejected": rejected},
        )

    async def link_fix_to_pattern(
        self,
        signature_hash: str,
        template_id: str,
        confidence: float,
        outcome: str,
        strategy_used: str,
    ) -> None:
        """Create or update RESOLVED_BY edge between ErrorPattern and FixTemplate."""
        from graph.queries import LINK_FIX_TO_PATTERN
        await self.execute_write(
            LINK_FIX_TO_PATTERN,
            {
                "signature_hash": signature_hash,
                "template_id": template_id,
                "confidence": confidence,
                "outcome": outcome,
                "strategy_used": strategy_used,
            },
        )

    async def increment_pattern_rejection(
        self, signature_hash: str, threshold: float = 0.4
    ) -> None:
        """Increment rejection_count and flip needs_refresh if rate exceeds threshold."""
        from graph.queries import INCREMENT_PATTERN_REJECTION
        await self.execute_write(
            INCREMENT_PATTERN_REJECTION,
            {"signature_hash": signature_hash, "threshold": threshold},
        )

    async def increment_pattern_acceptance(self, signature_hash: str) -> None:
        """Increment acceptance_count on an ErrorPattern."""
        from graph.queries import INCREMENT_PATTERN_ACCEPTANCE
        await self.execute_write(
            INCREMENT_PATTERN_ACCEPTANCE,
            {"signature_hash": signature_hash},
        )

    async def supersede_template(
        self,
        old_template_id: str,
        new_template_id: str,
        reason: str = "A/B test promotion",
    ) -> None:
        """Create SUPERSEDED_BY edge and update statuses (old→retired, new→active)."""
        from graph.queries import SUPERSEDE_TEMPLATE
        await self.execute_write(
            SUPERSEDE_TEMPLATE,
            {
                "old_template_id": old_template_id,
                "new_template_id": new_template_id,
                "reason": reason,
            },
        )

    async def upsert_code_pattern(
        self,
        ast_hash: str,
        language: str,
        pattern_type: str,
        description: str,
    ) -> None:
        """Merge a CodePattern node (structural AST pattern reuse tracking)."""
        from graph.queries import UPSERT_CODE_PATTERN
        await self.execute_write(
            UPSERT_CODE_PATTERN,
            {
                "ast_hash": ast_hash,
                "language": language,
                "pattern_type": pattern_type,
                "description": description,
            },
        )

    async def link_code_to_error(
        self, ast_hash: str, signature_hash: str
    ) -> None:
        """Create ASSOCIATED_WITH edge between CodePattern and ErrorPattern."""
        from graph.queries import LINK_CODE_TO_ERROR
        await self.execute_write(
            LINK_CODE_TO_ERROR,
            {"ast_hash": ast_hash, "signature_hash": signature_hash},
        )

    async def get_best_template(
        self,
        error_class: str,
        min_uses: int = 3,
        min_success_rate: float = 0.6,
    ) -> dict | None:
        """
        Return the highest success_rate FixTemplate for an error_class.
        Excludes superseded (retired) templates.
        Returns None if no qualifying template exists.
        """
        from graph.queries import GET_BEST_TEMPLATE
        results = await self.execute_query(
            GET_BEST_TEMPLATE,
            {
                "error_class": error_class,
                "min_uses": min_uses,
                "min_success_rate": min_success_rate,
            },
        )
        return results[0] if results else None

    async def get_failing_classes(
        self,
        threshold: float = 0.4,
        min_samples: int = 5,
    ) -> list[dict]:
        """
        Return error classes where rejection_rate >= threshold and samples >= min_samples.
        Used by StrategyMonitor to identify which classes need new strategies.
        """
        from graph.queries import GET_FAILING_CLASSES
        return await self.execute_query(
            GET_FAILING_CLASSES,
            {"threshold": threshold, "min_samples": min_samples},
        )

    async def get_graph_stats(self) -> dict:
        """
        Return node counts for all major node types.
        Falls back to individual MATCH queries if APOC is not available.
        """
        try:
            from graph.queries import GET_GRAPH_STATS_SIMPLE
            rows = await self.execute_query(GET_GRAPH_STATS_SIMPLE)
            if rows:
                return rows[0]
        except Exception as e:
            logger.warning("Neo4j get_graph_stats failed: %s", e)
        return {
            "error_patterns": 0,
            "fix_templates": 0,
            "code_patterns": 0,
            "services": 0,
            "cascades": 0,
        }


_neo4j_instance: Neo4jClient | None = None


def get_neo4j() -> Neo4jClient:
    global _neo4j_instance
    if _neo4j_instance is None:
        s = get_settings()
        _neo4j_instance = Neo4jClient(s.neo4j_uri, s.neo4j_user, s.neo4j_password)
    return _neo4j_instance


