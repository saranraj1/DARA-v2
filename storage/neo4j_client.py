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

    # ─── Schema / Indexes ────────────────────────────────────

    async def create_indexes(self) -> None:
        """Create all required Neo4j indexes on first startup."""
        indexes = [
            "CREATE INDEX func_name IF NOT EXISTS FOR (n:Function) ON (n.name)",
            "CREATE INDEX func_id IF NOT EXISTS FOR (n:Function) ON (n.id)",
            "CREATE INDEX file_path IF NOT EXISTS FOR (n:File) ON (n.path)",
            "CREATE INDEX service_name IF NOT EXISTS FOR (n:Service) ON (n.name)",
            "CREATE INDEX error_pattern IF NOT EXISTS FOR (n:ErrorPattern) ON (n.error_class)",
            "CREATE INDEX fix_template IF NOT EXISTS FOR (n:FixTemplate) ON (n.template_id)",
            "CREATE INDEX cascade_trace IF NOT EXISTS FOR (n:FailureCascade) ON (n.trace_id)",
        ]
        for cypher in indexes:
            try:
                await self.execute_write(cypher)
            except Exception as e:
                # Indexes may already exist on subsequent startups
                logger.debug("Index creation skipped", extra={"error": str(e)})

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

    # ─── Failure Cascade Operations ──────────────────────────

    async def store_failure_cascade(
        self,
        trace_id: str,
        root_service: str,
        affected_services: list[str],
    ) -> None:
        cypher = """
        MERGE (c:FailureCascade {trace_id: $trace_id})
        SET c.root_service = $root_service,
            c.affected_services = $affected_services,
            c.recorded_at = datetime()
        """
        await self.execute_write(
            cypher,
            {
                "trace_id": trace_id,
                "root_service": root_service,
                "affected_services": affected_services,
            },
        )

    async def find_similar_cascades(
        self, root_service: str, limit: int = 5
    ) -> list[dict]:
        cypher = """
        MATCH (c:FailureCascade {root_service: $service})
        OPTIONAL MATCH (c)-[:RESOLVED_BY]->(t:FixTemplate)
        RETURN c.trace_id AS trace_id,
               c.affected_services AS affected_services,
               t.template_id AS fix_template_id,
               t.success_rate AS template_success_rate
        ORDER BY c.recorded_at DESC
        LIMIT $limit
        """
        return await self.execute_query(
            cypher, {"service": root_service, "limit": limit}
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
