"""
DARA — Neo4j Cypher Query Templates (Week 7-8)
===============================================
All Cypher queries in one place for easy versioning and testing.
Import these constants into neo4j_client.py or execute directly.
"""

# ── Service Node Queries ──────────────────────────────────────

UPSERT_SERVICE = """
MERGE (s:Service {name: $name})
ON CREATE SET
    s.language        = $language,
    s.repo_full_name  = $repo_full_name,
    s.registered_at   = datetime()
ON MATCH SET
    s.language        = COALESCE($language, s.language),
    s.repo_full_name  = COALESCE($repo_full_name, s.repo_full_name),
    s.last_seen       = datetime()
RETURN s.name AS name
"""

UPSERT_SERVICE_CALL_EDGE = """
MERGE (src:Service {name: $source})
MERGE (tgt:Service {name: $target})
MERGE (src)-[r:CALLS]->(tgt)
ON CREATE SET
    r.call_count     = $call_count,
    r.error_count    = $error_count,
    r.avg_latency_ms = $avg_latency_ms,
    r.first_seen     = datetime(),
    r.last_seen      = datetime()
ON MATCH SET
    r.call_count     = r.call_count + $call_count,
    r.error_count    = r.error_count + $error_count,
    r.avg_latency_ms = $avg_latency_ms,
    r.last_seen      = datetime()
"""

GET_UPSTREAM_SERVICES = """
MATCH path = (upstream:Service)-[:CALLS*1..$hops]->(target:Service {name: $name})
RETURN DISTINCT
    upstream.name           AS service,
    length(path)            AS hop_count,
    last(relationships(path)).call_count  AS call_count,
    last(relationships(path)).error_count AS error_count,
    last(relationships(path)).avg_latency_ms AS avg_latency_ms
ORDER BY hop_count ASC
LIMIT 20
"""

GET_DOWNSTREAM_SERVICES = """
MATCH path = (source:Service {name: $name})-[:CALLS*1..$hops]->(downstream:Service)
RETURN DISTINCT
    downstream.name         AS service,
    length(path)            AS hop_count,
    last(relationships(path)).call_count  AS call_count,
    last(relationships(path)).error_count AS error_count,
    last(relationships(path)).avg_latency_ms AS avg_latency_ms
ORDER BY hop_count ASC
LIMIT 20
"""

GET_FULL_TOPOLOGY = """
MATCH (src:Service)-[r:CALLS]->(tgt:Service)
RETURN
    src.name         AS source,
    tgt.name         AS target,
    r.call_count     AS call_count,
    r.error_count    AS error_count,
    r.avg_latency_ms AS avg_latency_ms,
    r.last_seen      AS last_seen
ORDER BY r.call_count DESC
LIMIT 500
"""

GET_ALL_SERVICE_NODES = """
MATCH (s:Service)
RETURN
    s.name           AS name,
    s.language       AS language,
    s.repo_full_name AS repo_full_name,
    s.registered_at  AS registered_at
ORDER BY s.name
"""

# ── Failure Cascade Queries ───────────────────────────────────

STORE_FAILURE_CASCADE = """
MERGE (c:FailureCascade {trace_id: $trace_id})
SET
    c.root_service       = $root_service,
    c.affected_services  = $affected_services,
    c.propagation_path   = $propagation_path,
    c.blame_confidence   = $blame_confidence,
    c.error_message      = $error_message,
    c.recorded_at        = datetime()
"""

LINK_CASCADE_TO_SERVICE = """
MATCH (c:FailureCascade {trace_id: $trace_id})
MATCH (s:Service {name: $service_name})
MERGE (c)-[:ORIGINATED_IN]->(s)
"""

FIND_SIMILAR_CASCADES = """
MATCH (c:FailureCascade {root_service: $root_service})
WHERE c.trace_id <> $exclude_trace_id
OPTIONAL MATCH (c)-[:RESOLVED_BY]->(t:FixTemplate)
RETURN
    c.trace_id           AS trace_id,
    c.affected_services  AS affected_services,
    c.propagation_path   AS propagation_path,
    c.blame_confidence   AS blame_confidence,
    t.template_id        AS fix_template_id,
    t.success_rate       AS template_success_rate,
    c.recorded_at        AS recorded_at
ORDER BY c.recorded_at DESC
LIMIT $limit
"""

# ── Index Definitions ─────────────────────────────────────────

INDEXES = [
    "CREATE INDEX func_name IF NOT EXISTS FOR (n:Function) ON (n.name)",
    "CREATE INDEX func_id IF NOT EXISTS FOR (n:Function) ON (n.id)",
    "CREATE INDEX file_path IF NOT EXISTS FOR (n:File) ON (n.path)",
    "CREATE INDEX service_name IF NOT EXISTS FOR (n:Service) ON (n.name)",
    "CREATE INDEX error_pattern IF NOT EXISTS FOR (n:ErrorPattern) ON (n.error_class)",
    "CREATE INDEX fix_template IF NOT EXISTS FOR (n:FixTemplate) ON (n.template_id)",
    "CREATE INDEX cascade_trace IF NOT EXISTS FOR (n:FailureCascade) ON (n.trace_id)",
    "CREATE INDEX cascade_root_service IF NOT EXISTS FOR (n:FailureCascade) ON (n.root_service)",
]
