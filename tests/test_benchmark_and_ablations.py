"""
Tests for Benchmark Harness, TestRunner Patch Application, and Ablation Toggles
"""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import get_settings
from evaluation.benchmark_harness import (
    BENCHMARK_CASES,
    BenchmarkCase,
    BenchmarkHarness,
    BenchmarkResult,
)
from validation.test_runner import TestRunner


class TestBenchmarkHarness:
    @pytest.mark.asyncio
    async def test_run_suite_simulation(self):
        harness = BenchmarkHarness()
        results = await harness.run_suite()
        assert len(results) == len(BENCHMARK_CASES)
        
        metrics = harness.compute_summary_metrics(results)
        assert metrics["total_cases"] == len(BENCHMARK_CASES)
        assert "resolved_rate" in metrics
        assert "failure_categories" in metrics
        assert metrics["ground_truth_match_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_run_single_case_live_orchestrator(self):
        harness = BenchmarkHarness()
        mock_orch = MagicMock()
        mock_orch._pg = MagicMock()
        mock_orch.run = AsyncMock()

        from api.models.agent_schemas import Fix, ReviewResult, RootCauseResult
        from validation.engine import ValidationReport

        pipeline_res = MagicMock()
        pipeline_res.status = "fixed"
        pipeline_res.stage_reached = "fixed"
        pipeline_res.fix = Fix(
            error_id="err-1",
            patches=[],
            total_files_changed=0,
            total_lines_changed=0,
            fix_explanation="Handled None",
            confidence_retained=0.92,
            regression_risk="low",
            strategy="llm_single_file",
            llm_provider="groq",
        )
        pipeline_res.root_cause = RootCauseResult(
            immediate_cause="NoneType dereference",
            root_cause="NoneType error",
            confidence=0.92,
            evidence_quality="high",
            files_to_change=["storage/postgres.py"],
            suggested_strategy="llm_single_file",
            reasoning_trace="trace",
        )
        pipeline_res.escalation_trigger = None
        pipeline_res.sandbox_iterations = 1
        pipeline_res.security_retries = 0
        pipeline_res.validation = ValidationReport(
            fix_id="f1",
            passed=True,
            sandbox_passed=True,
            sandbox_iterations=1,
        )
        pipeline_res.review = ReviewResult(
            quality_score=0.95,
            correctness_passes=True,
            security_passes=True,
            overall_recommendation="approve",
            rejection_reason=None,
            reviewer_notes="",
            issues=[],
        )
        mock_orch.run.return_value = pipeline_res

        case = BENCHMARK_CASES[0]
        res = await harness.run_single_case(case, orchestrator=mock_orch)
        assert res.case_id == case.case_id
        assert res.pipeline_status == "fixed"
        assert res.auto_approved is True
        assert res.failure_category == "success"

    def test_export_json_and_csv(self, tmp_path):
        harness = BenchmarkHarness()
        sample_results = [
            BenchmarkResult(
                case_id="BM-TEST",
                error_class="KeyError",
                pipeline_status="fixed",
                stage_reached="fixed",
                confidence=0.9,
                strategy="llm_single_file",
                escalation_trigger=None,
                sandbox_passed=True,
                sandbox_iterations=1,
                security_passed=True,
                security_retries=0,
                auto_approved=True,
                ground_truth_match=True,
                failure_category="success",
                execution_duration_ms=50,
            )
        ]
        json_file = tmp_path / "results.json"
        csv_file = tmp_path / "results.csv"

        harness.export_json(sample_results, json_file)
        harness.export_csv(sample_results, csv_file)

        assert json_file.exists()
        assert csv_file.exists()
        assert "BM-TEST" in json_file.read_text(encoding="utf-8")
        assert "BM-TEST" in csv_file.read_text(encoding="utf-8")


class TestTestRunnerPatchApplication:
    def test_apply_patch_fixed_content(self, tmp_path):
        target = tmp_path / "foo.py"
        target.write_text("def hello(): return 'world'\n", encoding="utf-8")

        patch = {"file_path": "foo.py", "fixed_content": "def hello(): return 'fixed'\n"}
        applied = TestRunner._apply_patch(tmp_path, patch)
        assert applied is True
        assert target.read_text(encoding="utf-8") == "def hello(): return 'fixed'\n"

    def test_apply_patch_unified_diff(self, tmp_path):
        target = tmp_path / "bar.py"
        target.write_text("x = 1\ny = 2\n", encoding="utf-8")

        diff = """--- a/bar.py
+++ b/bar.py
@@ -1,2 +1,2 @@
-x = 1
+x = 10
 y = 2
"""
        patch = {"file_path": "bar.py", "unified_diff": diff}
        applied = TestRunner._apply_patch(tmp_path, patch)
        assert applied is True
        content = target.read_text(encoding="utf-8")
        assert "x = 10" in content


class TestAblationSettings:
    def test_ablation_defaults_are_true(self):
        settings = get_settings()
        assert settings.enable_pattern_memory is True
        assert settings.enable_semantic_retrieval is True
        assert settings.enable_self_healing is True
        assert settings.enable_security_validation is True
        assert settings.enable_reviewer_gate is True
