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
    "CREATE INDEX error_pattern_hash IF NOT EXISTS FOR (n:ErrorPattern) ON (n.signature_hash)",
    "CREATE INDEX fix_template IF NOT EXISTS FOR (n:FixTemplate) ON (n.template_id)",
    "CREATE INDEX fix_template_class IF NOT EXISTS FOR (n:FixTemplate) ON (n.error_class)",
    "CREATE INDEX code_pattern_hash IF NOT EXISTS FOR (n:CodePattern) ON (n.ast_hash)",
    "CREATE INDEX cascade_trace IF NOT EXISTS FOR (n:FailureCascade) ON (n.trace_id)",
    "CREATE INDEX cascade_root_service IF NOT EXISTS FOR (n:FailureCascade) ON (n.root_service)",
]


# ── Phase 3: Institutional Memory Graph Queries ───────────────

UPSERT_ERROR_PATTERN = """
MERGE (p:ErrorPattern {signature_hash: $signature_hash})
ON CREATE SET
    p.error_class      = $error_class,
    p.signature_hash   = $signature_hash,
    p.frequency        = 1,
    p.rejection_count  = 0,
    p.acceptance_count = 0,
    p.avg_confidence   = $confidence,
    p.dominant_strategy = $strategy,
    p.needs_refresh    = false,
    p.first_seen       = datetime(),
    p.last_seen        = datetime()
ON MATCH SET
    p.frequency        = p.frequency + 1,
    p.avg_confidence   = (p.avg_confidence * (p.frequency - 1) + $confidence) / p.frequency,
    p.dominant_strategy = $strategy,
    p.last_seen        = datetime()
RETURN p.signature_hash AS hash
"""

UPSERT_FIX_TEMPLATE = """
MERGE (t:FixTemplate {template_id: $template_id})
ON CREATE SET
    t.error_class       = $error_class,
    t.strategy          = $strategy,
    t.fix_body          = $fix_body,
    t.acceptance_count  = 0,
    t.rejection_count   = 0,
    t.success_rate      = 0.0,
    t.created_at        = datetime(),
    t.last_used         = datetime()
ON MATCH SET
    t.last_used         = datetime()
RETURN t.template_id AS tid
"""

UPDATE_FIX_TEMPLATE_OUTCOME = """
MATCH (t:FixTemplate {template_id: $template_id})
SET
    t.acceptance_count  = t.acceptance_count + $accepted,
    t.rejection_count   = t.rejection_count  + $rejected,
    t.success_rate      = toFloat(t.acceptance_count) /
                          CASE WHEN (t.acceptance_count + t.rejection_count) = 0
                               THEN 1
                               ELSE (t.acceptance_count + t.rejection_count) END,
    t.last_used         = datetime()
"""

LINK_FIX_TO_PATTERN = """
MATCH (p:ErrorPattern {signature_hash: $signature_hash})
MATCH (t:FixTemplate   {template_id: $template_id})
MERGE (p)-[r:RESOLVED_BY]->(t)
ON CREATE SET
    r.confidence     = $confidence,
    r.outcome        = $outcome,
    r.strategy_used  = $strategy_used,
    r.recorded_at    = datetime()
ON MATCH SET
    r.confidence     = $confidence,
    r.outcome        = $outcome,
    r.strategy_used  = $strategy_used,
    r.recorded_at    = datetime()
"""

INCREMENT_PATTERN_REJECTION = """
MATCH (p:ErrorPattern {signature_hash: $signature_hash})
SET
    p.rejection_count = p.rejection_count + 1,
    p.needs_refresh   = CASE
        WHEN toFloat(p.rejection_count + 1) /
             CASE WHEN (p.acceptance_count + p.rejection_count + 1) = 0
                  THEN 1
                  ELSE (p.acceptance_count + p.rejection_count + 1) END > $threshold
        THEN true
        ELSE p.needs_refresh
    END,
    p.last_seen = datetime()
"""

INCREMENT_PATTERN_ACCEPTANCE = """
MATCH (p:ErrorPattern {signature_hash: $signature_hash})
SET
    p.acceptance_count = p.acceptance_count + 1,
    p.last_seen        = datetime()
"""

SUPERSEDE_TEMPLATE = """
MATCH (old:FixTemplate {template_id: $old_template_id})
MATCH (new:FixTemplate {template_id: $new_template_id})
MERGE (old)-[r:SUPERSEDED_BY]->(new)
SET r.reason     = $reason,
    r.promoted_at = datetime()
SET old.status = 'retired'
SET new.status = 'active'
"""

UPSERT_CODE_PATTERN = """
MERGE (c:CodePattern {ast_hash: $ast_hash})
ON CREATE SET
    c.language     = $language,
    c.pattern_type = $pattern_type,
    c.description  = $description,
    c.first_seen   = datetime()
ON MATCH SET
    c.last_seen = datetime()
RETURN c.ast_hash AS hash
"""

LINK_CODE_TO_ERROR = """
MATCH (c:CodePattern   {ast_hash: $ast_hash})
MATCH (p:ErrorPattern  {signature_hash: $signature_hash})
MERGE (c)-[r:ASSOCIATED_WITH]->(p)
SET r.recorded_at = datetime()
"""

GET_BEST_TEMPLATE = """
MATCH (p:ErrorPattern {error_class: $error_class})-[:RESOLVED_BY]->(t:FixTemplate)
WHERE t.acceptance_count + t.rejection_count >= $min_uses
  AND t.success_rate >= $min_success_rate
  AND NOT EXISTS {
      MATCH (t)-[:SUPERSEDED_BY]->(:FixTemplate)
  }
RETURN
    t.template_id     AS template_id,
    t.strategy        AS strategy,
    t.fix_body        AS fix_body,
    t.success_rate    AS success_rate,
    t.acceptance_count AS acceptance_count,
    t.rejection_count AS rejection_count,
    t.last_used       AS last_used
ORDER BY t.success_rate DESC, t.acceptance_count DESC
LIMIT 1
"""

GET_FAILING_CLASSES = """
MATCH (p:ErrorPattern)
WHERE p.acceptance_count + p.rejection_count >= $min_samples
  AND toFloat(p.rejection_count) /
      (p.acceptance_count + p.rejection_count) >= $threshold
OPTIONAL MATCH (p)-[r:RESOLVED_BY]->(t:FixTemplate)
RETURN
    p.error_class       AS error_class,
    p.signature_hash    AS signature_hash,
    p.rejection_count   AS rejection_count,
    p.acceptance_count  AS acceptance_count,
    p.dominant_strategy AS dominant_strategy,
    p.needs_refresh     AS needs_refresh,
    p.last_seen         AS last_seen,
    t.template_id       AS template_id,
    t.success_rate      AS template_success_rate
ORDER BY toFloat(p.rejection_count) / (p.acceptance_count + p.rejection_count) DESC
LIMIT 20
"""

GET_GRAPH_STATS = """
CALL apoc.meta.stats()
YIELD labels
RETURN labels
UNION ALL
MATCH (p:ErrorPattern) RETURN count(p) AS error_patterns,
     0 AS fix_templates, 0 AS code_patterns, 0 AS services, 0 AS cascades
UNION ALL
MATCH (t:FixTemplate) RETURN 0, count(t), 0, 0, 0
UNION ALL
MATCH (c:CodePattern) RETURN 0, 0, count(c), 0, 0
UNION ALL
MATCH (s:Service)     RETURN 0, 0, 0, count(s), 0
UNION ALL
MATCH (f:FailureCascade) RETURN 0, 0, 0, 0, count(f)
"""

# Simplified stats query (no APOC dependency)
GET_GRAPH_STATS_SIMPLE = """
MATCH (p:ErrorPattern)  WITH count(p)  AS error_patterns
MATCH (t:FixTemplate)   WITH error_patterns, count(t) AS fix_templates
MATCH (c:CodePattern)   WITH error_patterns, fix_templates, count(c) AS code_patterns
MATCH (s:Service)       WITH error_patterns, fix_templates, code_patterns, count(s) AS services
OPTIONAL MATCH (f:FailureCascade)
RETURN error_patterns, fix_templates, code_patterns, services, count(f) AS cascades
"""

MEMORY_GRAPH_INDEXES = [
    "CREATE INDEX error_pattern_sig IF NOT EXISTS FOR (n:ErrorPattern) ON (n.signature_hash)",
    "CREATE INDEX error_pattern_class IF NOT EXISTS FOR (n:ErrorPattern) ON (n.error_class)",
    "CREATE INDEX fix_template_rate IF NOT EXISTS FOR (n:FixTemplate) ON (n.success_rate)",
    "CREATE INDEX fix_template_status IF NOT EXISTS FOR (n:FixTemplate) ON (n.status)",
    "CREATE INDEX code_pattern IF NOT EXISTS FOR (n:CodePattern) ON (n.ast_hash)",
]

