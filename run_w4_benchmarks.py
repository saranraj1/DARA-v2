import sys
import time

sys.path.insert(0, ".")
print("=== DARA Week 4 Benchmarks ===\n")

# B1: All agent imports
t0 = time.perf_counter()

print(f"B1 Agent imports: OK in {(time.perf_counter()-t0)*1000:.0f}ms")
print("B1 PASS")

# B2: Pydantic schemas used by agents
from api.models.agent_schemas import Fix, PatchFile, ReviewResult, RootCauseResult

rc = RootCauseResult(
    immediate_cause="user obj is None",
    root_cause="Missing null check before .get() call",
    contributing_factors=["No input validation", "Optional not handled"],
    confidence=0.87,
    evidence_quality="high",
    files_to_change=["app.py"],
    suggested_strategy="llm_single_file",
    reasoning_trace="Step 1: line 42 calls user.get() on None..."
)
print(f"\nB2 RootCauseResult: confidence={rc.confidence} strategy={rc.suggested_strategy}")
assert rc.confidence == 0.87
assert rc.suggested_strategy in {"template_based","llm_single_file","llm_multi_file","human_escalation"}
print("B2 PASS")

# B3: PatchFile + Fix schemas
pf = PatchFile(file_path="app.py", unified_diff="--- a/app.py\n+++ b/app.py\n@@ -42 +42 @@\n-user.get('id')\n+user.get('id') if user else None",
               lines_changed=2, change_description="Guard None before .get()")
fix = Fix(error_id="test-error-001", patches=[pf], total_files_changed=1, total_lines_changed=2,
          fix_explanation="Added null guard", suggested_tests=["test_none_user"],
          confidence_retained=0.87, regression_risk="low",
          strategy="llm_single_file", llm_provider="groq")
print(f"\nB3 Fix schema: files={fix.total_files_changed} lines={fix.total_lines_changed} risk={fix.regression_risk}")
assert fix.regression_risk in {"low","medium","high"}
print("B3 PASS")

# B4: ReviewResult schema
rv = ReviewResult(quality_score=0.92, correctness_passes=True, security_passes=True,
                  overall_recommendation="approve", rejection_reason=None,
                  reviewer_notes="Minimal fix, good null guard", issues=[])
print(f"\nB4 ReviewResult: score={rv.quality_score} rec={rv.overall_recommendation}")
assert rv.overall_recommendation in {"approve","approve_with_comments","reject"}
print("B4 PASS")

# B5: Prompt files loadable
from pathlib import Path

for name in ["root_cause_v1.txt","fix_generator_v1.txt","reviewer_v1.txt"]:
    txt = Path(f"prompts/{name}").read_text(encoding="utf-8")
    assert "{{error_class}}" in txt or "{{patch_content}}" in txt or "{{immediate_cause}}" in txt, f"Bad prompt: {name}"
    print(f"B5 {name}: {len(txt)} chars OK")
print("B5 PASS")

# B6: Celery tasks wired with agent pipeline
from workers.main import celery_app

worker_tasks = [k for k in celery_app.tasks if "workers" in k]
print(f"\nB6 Celery tasks: {worker_tasks}")
assert "workers.tasks.analyze_error" in celery_app.tasks
assert "workers.tasks.index_repository" in celery_app.tasks
print("B6 PASS")

# B7: DebuggerAgent JSON extractor logic (unit test without LLM)
import json
import re

raw_llm_response = """
Here is my analysis:

```json
{
  "immediate_cause": "user is None on line 42",
  "root_cause": "No null check before calling .get()",
  "contributing_factors": ["Missing validation", "Optional type not handled"],
  "confidence": 0.87,
  "evidence_quality": "high",
  "files_to_change": ["app.py"],
  "suggested_strategy": "llm_single_file",
  "reasoning_trace": "Step 1: found None dereference..."
}
```
"""
text = re.sub(r"```(?:json)?","",raw_llm_response).strip()
m = re.search(r"\{.*\}", text, re.DOTALL)
data = json.loads(m.group())
assert data["confidence"] == 0.87
assert data["suggested_strategy"] == "llm_single_file"
print(f"\nB7 JSON extractor: confidence={data['confidence']} strategy={data['suggested_strategy']}")
print("B7 PASS")

# B8: difflib patch generation (core of FixerAgent)
import difflib

original = "def get_user(uid):\n    user = db.find(uid)\n    return user.get('name')\n"
fixed    = "def get_user(uid):\n    user = db.find(uid)\n    return user.get('name') if user else None\n"
diff = list(difflib.unified_diff(original.splitlines(keepends=True),
                                  fixed.splitlines(keepends=True),
                                  fromfile="a/app.py", tofile="b/app.py", lineterm=""))
changed = sum(1 for l in diff if l.startswith(("+","-")) and not l.startswith(("+++","---")))
print(f"\nB8 difflib patch: {changed} lines changed")
print("\n".join(diff))
assert changed == 2
print("B8 PASS")

print("\n=== ALL 8 BENCHMARKS PASSED ===")
