"""
Unit tests: ingestion/deduplicator.py + workers maintenance tasks (Week 7)
"""
import sys
sys.path.insert(0, ".")

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


class TestErrorDeduplicator:

    def test_signature_is_deterministic(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        error = {"error_class": "AttributeError", "message": "NoneType",
                 "service": "auth", "file_path": "app.py"}
        s1 = d.compute_signature(error)
        s2 = d.compute_signature(error)
        assert s1 == s2
        assert len(s1) == 64  # sha256 hex

    def test_signature_changes_with_different_error(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        e1 = {"error_class": "AttributeError", "message": "a", "service": "s1", "file_path": "f1"}
        e2 = {"error_class": "ValueError", "message": "b", "service": "s2", "file_path": "f2"}
        assert d.compute_signature(e1) != d.compute_signature(e2)

    def test_signature_uses_message_truncation(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        # Two errors with same first 200 chars of message should have same signature
        long_msg = "x" * 300
        e1 = {"error_class": "E", "message": long_msg, "service": "s", "file_path": "f"}
        e2 = {"error_class": "E", "message": long_msg + "EXTRA", "service": "s", "file_path": "f"}
        assert d.compute_signature(e1) == d.compute_signature(e2)

    @pytest.mark.asyncio
    async def test_check_returns_not_duplicate_when_no_db_match(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        mock_pg = MagicMock()
        mock_pg.find_error_by_signature = AsyncMock(return_value=None)
        error = {"error_class": "X", "message": "y", "service": "z", "file_path": "a.py"}
        result = await d.check(error, mock_pg)
        assert not result.is_duplicate
        assert result.existing_error_id is None

    @pytest.mark.asyncio
    async def test_check_returns_duplicate_when_db_match(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        mock_pg = MagicMock()
        mock_pg.find_error_by_signature = AsyncMock(return_value={
            "id": "abc-123", "status": "analyzing", "created_at": "2024-01-01"
        })
        error = {"error_class": "X", "message": "y", "service": "z", "file_path": "a.py"}
        result = await d.check(error, mock_pg)
        assert result.is_duplicate
        assert result.existing_error_id == "abc-123"
        assert result.existing_status == "analyzing"

    @pytest.mark.asyncio
    async def test_check_does_not_raise_on_db_error(self):
        """Deduplication must never block ingestion on DB failures."""
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        mock_pg = MagicMock()
        mock_pg.find_error_by_signature = AsyncMock(side_effect=Exception("DB DOWN"))
        error = {"error_class": "X", "message": "y", "service": "z", "file_path": "a.py"}
        result = await d.check(error, mock_pg)
        # Should NOT raise, should return not-duplicate (safe-fail)
        assert not result.is_duplicate

    def test_signature_handles_none_fields(self):
        from ingestion.deduplicator import ErrorDeduplicator
        d = ErrorDeduplicator()
        e = {"error_class": "E", "message": None, "service": None, "file_path": None}
        sig = d.compute_signature(e)
        assert len(sig) == 64

    def test_ingest_response_schema_dedup_fields(self):
        from api.models.error_schemas import ErrorIngestResponse
        r = ErrorIngestResponse(id="abc", deduplicated=True, existing_status="analyzing")
        assert r.deduplicated is True
        assert r.existing_status == "analyzing"
        assert "Duplicate" in r.message


class TestMaintenanceTasks:

    def test_cleanup_stale_task_registered(self):
        import workers.tasks
        assert hasattr(workers.tasks, "cleanup_stale_pipelines")

    def test_warm_stats_cache_task_registered(self):
        import workers.tasks
        assert hasattr(workers.tasks, "warm_stats_cache")

    def test_optimize_pattern_library_task_registered(self):
        import workers.tasks
        assert hasattr(workers.tasks, "optimize_pattern_library")

    @pytest.mark.asyncio
    async def test_cleanup_stale_calls_postgres(self):
        """_cleanup_stale async helper should call cleanup_stale_errors."""
        with patch("storage.postgres.get_postgres") as mock_get:
            mock_pg = MagicMock()
            mock_pg.cleanup_stale_errors = AsyncMock(return_value=3)
            mock_get.return_value = mock_pg
            from workers.tasks import _cleanup_stale
            result = await _cleanup_stale(30)
            assert result["cleaned"] == 3
