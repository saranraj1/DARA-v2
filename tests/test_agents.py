"""Unit tests: agent logic (no LLM calls - pure unit tests)"""
import json, re, sys, difflib
import pytest
sys.path.insert(0,".")

from api.models.agent_schemas import RootCauseResult, Fix, PatchFile, ReviewResult
from context.builder import ContextBundle

# ── JSON extractor (DebuggerAgent logic) ─────────────────────
class TestJsonExtractor:
    def _extract(self, text):
        text = re.sub(r"```(?:json)?","",text).strip()
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try: return json.loads(m.group())
            except json.JSONDecodeError: pass
        return {}

    def test_clean_json(self):
        raw = '{"confidence": 0.9, "suggested_strategy": "llm_single_file"}'
        d = self._extract(raw)
        assert d["confidence"] == 0.9

    def test_json_in_markdown_fence(self):
        raw = 'Some text\n```json\n{"confidence": 0.8, "suggested_strategy": "template_based"}\n```\n'
        d = self._extract(raw)
        assert d["confidence"] == 0.8

    def test_json_with_surrounding_text(self):
        raw = "Analysis done.\n{\"confidence\": 0.7, \"suggested_strategy\": \"human_escalation\"}\nEnd."
        d = self._extract(raw)
        assert d["suggested_strategy"] == "human_escalation"

    def test_invalid_json_returns_empty(self):
        d = self._extract("not json at all {{broken}")
        assert d == {}


# ── RootCauseResult schema ────────────────────────────────────
class TestRootCauseResult:
    def test_valid(self):
        rc = RootCauseResult(immediate_cause="x is None", root_cause="Missing null check",
                             contributing_factors=["no validation"],
                             confidence=0.85, evidence_quality="high",
                             files_to_change=["app.py"],
                             suggested_strategy="llm_single_file",
                             reasoning_trace="step 1...")
        assert rc.confidence == 0.85

    def test_confidence_bounds(self):
        # Both extremes should be valid per-schema
        rc_low = RootCauseResult(immediate_cause="x", root_cause="y",
                                  contributing_factors=[], confidence=0.0,
                                  evidence_quality="low", files_to_change=[],
                                  suggested_strategy="human_escalation", reasoning_trace="")
        rc_high = RootCauseResult(immediate_cause="x", root_cause="y",
                                   contributing_factors=[], confidence=1.0,
                                   evidence_quality="high", files_to_change=["f.py"],
                                   suggested_strategy="template_based", reasoning_trace="")
        assert rc_low.confidence == 0.0
        assert rc_high.confidence == 1.0


# ── Fix + PatchFile schema ────────────────────────────────────
class TestFixSchema:
    def test_patch_file(self):
        pf = PatchFile(file_path="app.py", unified_diff="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
                       lines_changed=2, change_description="fixed null")
        assert pf.lines_changed == 2

    def test_fix_aggregation(self):
        pf1 = PatchFile(file_path="a.py", unified_diff="d1", lines_changed=3, change_description="c1")
        pf2 = PatchFile(file_path="b.py", unified_diff="d2", lines_changed=5, change_description="c2")
        fix = Fix(error_id="e1", patches=[pf1,pf2], total_files_changed=2,
                  total_lines_changed=8, fix_explanation="fixed 2 files",
                  suggested_tests=[], confidence_retained=0.8,
                  regression_risk="medium", strategy="llm_multi_file", llm_provider="groq")
        assert fix.total_files_changed == 2
        assert fix.total_lines_changed == 8
        assert fix.regression_risk == "medium"


# ── ReviewResult schema ───────────────────────────────────────
class TestReviewerSchema:
    def test_approve(self):
        rv = ReviewResult(quality_score=0.92, correctness_passes=True, security_passes=True,
                          overall_recommendation="approve", rejection_reason=None,
                          reviewer_notes="LGTM", issues=[])
        assert rv.overall_recommendation == "approve"

    def test_reject(self):
        rv = ReviewResult(quality_score=0.3, correctness_passes=False, security_passes=True,
                          overall_recommendation="reject",
                          rejection_reason="Does not fix root cause", reviewer_notes="", issues=["wrong fix"])
        assert rv.overall_recommendation == "reject"
        assert rv.rejection_reason is not None


# ── difflib patch generation (FixerAgent core) ────────────────
class TestDifflibPatch:
    def test_generates_valid_diff(self):
        orig = "def f(x):\n    return x.get('k')\n"
        fixed = "def f(x):\n    return x.get('k') if x else None\n"
        diff = list(difflib.unified_diff(orig.splitlines(keepends=True),
                                          fixed.splitlines(keepends=True),
                                          fromfile="a/app.py", tofile="b/app.py", lineterm=""))
        assert any(l.startswith("+") and "if x else None" in l for l in diff)
        changed = sum(1 for l in diff if l.startswith(("+","-")) and not l.startswith(("+++","---")))
        assert changed == 2

    def test_no_diff_when_identical(self):
        code = "def f(x):\n    return x\n"
        diff = list(difflib.unified_diff(code.splitlines(keepends=True),
                                          code.splitlines(keepends=True),
                                          fromfile="a/app.py", tofile="b/app.py", lineterm=""))
        assert diff == []


# ── Validation static analysis ───────────────────────────────
class TestStaticAnalyzer:
    @pytest.fixture
    def analyzer(self):
        from validation.static_analyzer import StaticAnalyzer
        return StaticAnalyzer()

    @pytest.mark.asyncio
    async def test_safe_code_passes(self, analyzer):
        code = "def safe(x):\n    return x + 1 if x else 0\n"
        result = await analyzer.analyze("test.py", code)
        assert result.passed

    @pytest.mark.asyncio
    async def test_eval_blocked(self, analyzer):
        code = "import os\ndef dangerous(x): return eval(x)\n"
        result = await analyzer.analyze("test.py", code)
        assert not result.passed
        assert any("eval" in f.get("message","").lower() or
                   f.get("code","").startswith("S")
                   for f in result.findings)
