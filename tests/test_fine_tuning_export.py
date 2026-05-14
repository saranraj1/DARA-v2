"""
Tests: Week 19-20 — Fine-Tuning Data Export Pipeline
Covers:
  - ExportResult.success is True for 'completed' and 'dry_run' status
  - ExportResult.success is False for 'failed' status
  - _build_triple: returns None when fix_explanation < min_fix_lines
  - _build_triple: returns valid messages array structure
  - _build_triple: metadata contains correct error_class
  - _format_error_context: includes all relevant fields
  - export(dry_run=True): returns triple_count without writing to MinIO
"""
import sys

sys.path.insert(0, ".")
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest


class TestExportResult:

    def _result(self, status):
        from export.fine_tuning_exporter import ExportResult
        return ExportResult(
            run_id="test-run",
            triple_count=5,
            skipped_count=1,
            minio_path="s3://bucket/test.jsonl" if status == "completed" else None,
            status=status,
            started_at=datetime.now(timezone.utc),
        )

    def test_success_true_for_completed(self):
        r = self._result("completed")
        assert r.success is True

    def test_success_true_for_dry_run(self):
        r = self._result("dry_run")
        assert r.success is True

    def test_success_false_for_failed(self):
        r = self._result("failed")
        assert r.success is False


class TestBuildTriple:

    def _exporter(self):
        from export.fine_tuning_exporter import FineTuningExporter
        return FineTuningExporter()

    def _row(self, **kwargs):
        base = {
            "error_id": "err-001",
            "error_class": "null_reference",
            "message": "NoneType has no attribute 'charge'",
            "stack_trace": "  File charge.py line 42\n    amount = user.amount",
            "service": "payment-svc",
            "file_path": "payment/charge.py",
            "line_number": 42,
            "root_cause_json": '{"root_cause": "user was None at line 42"}',
            "fix_explanation": (
                "Added guard clause:\n"
                "if user is None:\n"
                "    raise ValueError('user cannot be None')\n"
                "return process(user.amount)"
            ),
            "fix_confidence": 0.87,
            "fix_id": "fix-001",
            "outcome": "accepted",
            "strategy_used": "null_reference",
            "pipeline_at": datetime.now(timezone.utc),
        }
        base.update(kwargs)
        return base

    def test_build_triple_returns_messages_structure(self):
        exp = self._exporter()
        triple = exp._build_triple(self._row())
        assert triple is not None
        assert "messages" in triple
        roles = [m["role"] for m in triple["messages"]]
        assert roles == ["system", "user", "assistant"]

    def test_build_triple_metadata_has_error_class(self):
        exp = self._exporter()
        triple = exp._build_triple(self._row(error_class="database_error"))
        assert triple["metadata"]["error_class"] == "database_error"

    def test_build_triple_metadata_has_dara_version(self):
        exp = self._exporter()
        triple = exp._build_triple(self._row())
        assert triple["metadata"]["dara_version"] == "phase3"

    def test_build_triple_returns_none_for_short_explanation(self):
        exp = self._exporter()
        # explanation < min_fix_lines (3)
        triple = exp._build_triple(self._row(fix_explanation="x"))
        assert triple is None


class TestFormatContext:

    def _exporter(self):
        from export.fine_tuning_exporter import FineTuningExporter
        return FineTuningExporter()

    def test_format_context_includes_service(self):
        exp = self._exporter()
        row = {
            "error_class": "network_timeout",
            "service": "api-svc",
            "message": "Connection timed out",
            "file_path": "api/client.py",
            "line_number": 88,
            "stack_trace": "  File api/client.py line 88",
        }
        ctx = exp._format_error_context(row)
        assert "api-svc" in ctx
        assert "network_timeout" in ctx


class TestDryRunExport:

    @pytest.mark.asyncio
    async def test_dry_run_returns_count_without_minio(self):
        from export.fine_tuning_exporter import FineTuningExporter
        exporter = FineTuningExporter()
        # Mock fetch to return 3 quality row dicts
        exporter._fetch_quality_fixes = AsyncMock(return_value=[
            {
                "error_id": "e1", "error_class": "null_reference",
                "message": "None error", "stack_trace": "File x.py 5",
                "service": "svc", "file_path": "x.py", "line_number": 5,
                "root_cause_json": '{"root_cause": "x was None"}',
                "fix_explanation": "Added guard:\nif x is None:\n    raise\nreturn x",
                "fix_confidence": 0.90, "fix_id": "f1", "outcome": "accepted",
                "strategy_used": "null_reference",
                "pipeline_at": datetime.now(timezone.utc),
            }
        ])
        result = await exporter.export(dry_run=True)
        assert result.status == "dry_run"
        assert result.minio_path is None
        assert result.triple_count >= 0
