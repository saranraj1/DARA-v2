"""
Tests: Week 13-14 — Memory Graph (Neo4j Cypher Templates + Client Methods)
Covers:
  - UPSERT_ERROR_PATTERN contains correct ON CREATE / ON MATCH
  - LINK_FIX_TO_PATTERN contains RESOLVED_BY
  - SUPERSEDE_TEMPLATE contains SUPERSEDED_BY + status updates
  - GET_BEST_TEMPLATE filters by min_uses and min_success_rate
  - GET_FAILING_CLASSES filters by threshold and min_samples
  - Neo4jClient.upsert_error_pattern calls execute_write with correct keys
  - Neo4jClient.get_graph_stats returns dict with expected keys
  - Neo4jClient.increment_pattern_rejection calls with threshold param
"""
import sys

sys.path.insert(0, ".")
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestCypherTemplates:

    def test_upsert_error_pattern_has_merge_and_frequency(self):
        from graph.queries import UPSERT_ERROR_PATTERN
        assert "MERGE" in UPSERT_ERROR_PATTERN
        assert "signature_hash" in UPSERT_ERROR_PATTERN
        assert "frequency" in UPSERT_ERROR_PATTERN
        assert "avg_confidence" in UPSERT_ERROR_PATTERN

    def test_link_fix_to_pattern_has_resolved_by(self):
        from graph.queries import LINK_FIX_TO_PATTERN
        assert "RESOLVED_BY" in LINK_FIX_TO_PATTERN
        assert "template_id" in LINK_FIX_TO_PATTERN
        assert "confidence" in LINK_FIX_TO_PATTERN

    def test_supersede_template_has_superseded_by(self):
        from graph.queries import SUPERSEDE_TEMPLATE
        assert "SUPERSEDED_BY" in SUPERSEDE_TEMPLATE
        assert "old_template_id" in SUPERSEDE_TEMPLATE
        assert "new_template_id" in SUPERSEDE_TEMPLATE
        assert "retired" in SUPERSEDE_TEMPLATE.lower()
        assert "active" in SUPERSEDE_TEMPLATE.lower()

    def test_get_best_template_filters_superseded(self):
        from graph.queries import GET_BEST_TEMPLATE
        assert "SUPERSEDED_BY" in GET_BEST_TEMPLATE
        assert "min_uses" in GET_BEST_TEMPLATE
        assert "success_rate" in GET_BEST_TEMPLATE
        assert "ORDER BY" in GET_BEST_TEMPLATE

    def test_get_failing_classes_uses_threshold(self):
        from graph.queries import GET_FAILING_CLASSES
        assert "threshold" in GET_FAILING_CLASSES
        assert "min_samples" in GET_FAILING_CLASSES
        assert "rejection_count" in GET_FAILING_CLASSES

    def test_increment_pattern_rejection_has_needs_refresh(self):
        from graph.queries import INCREMENT_PATTERN_REJECTION
        assert "needs_refresh" in INCREMENT_PATTERN_REJECTION
        assert "threshold" in INCREMENT_PATTERN_REJECTION
        assert "rejection_count" in INCREMENT_PATTERN_REJECTION

    def test_memory_graph_indexes_not_empty(self):
        from graph.queries import MEMORY_GRAPH_INDEXES
        assert len(MEMORY_GRAPH_INDEXES) >= 3
        for idx in MEMORY_GRAPH_INDEXES:
            assert "CREATE INDEX" in idx


class TestNeo4jMemoryMethods:

    def _client(self):
        from storage.neo4j_client import Neo4jClient
        with patch.object(Neo4jClient, "__init__", lambda self, *a, **kw: None):
            client = Neo4jClient.__new__(Neo4jClient)
            client.execute_write = AsyncMock()
            client.execute_query = AsyncMock(return_value=[])
            client._driver = MagicMock()
            return client

    @pytest.mark.asyncio
    async def test_upsert_error_pattern_calls_execute_write(self):
        client = self._client()
        await client.upsert_error_pattern("hash123", "null_reference", 0.8, "null_reference")
        client.execute_write.assert_called_once()
        args = client.execute_write.call_args
        params = args[0][1]
        assert params["signature_hash"] == "hash123"
        assert params["error_class"] == "null_reference"
        assert params["confidence"] == 0.8

    @pytest.mark.asyncio
    async def test_get_best_template_returns_none_when_empty(self):
        client = self._client()
        client.execute_query = AsyncMock(return_value=[])
        result = await client.get_best_template("null_reference")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_graph_stats_returns_dict(self):
        client = self._client()
        client.execute_query = AsyncMock(return_value=[{
            "error_patterns": 5, "fix_templates": 3,
            "code_patterns": 2, "services": 4, "cascades": 1,
        }])
        stats = await client.get_graph_stats()
        assert "error_patterns" in stats
        assert stats["error_patterns"] == 5

    @pytest.mark.asyncio
    async def test_increment_rejection_calls_with_threshold(self):
        client = self._client()
        await client.increment_pattern_rejection("hash123", threshold=0.4)
        client.execute_write.assert_called_once()
        params = client.execute_write.call_args[0][1]
        assert params["threshold"] == 0.4
