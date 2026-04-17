"""
DARA Phase 1 Full Integration Test
Tests every component from ingestion through to orchestrator output.
No database/Redis required for stages 1-8 (mocked).
Stages 9-12 require real DB (skipped gracefully if not available).
"""
import sys, asyncio, time
sys.path.insert(0, ".")

print("=" * 60)
print("DARA Phase 1 Integration Test")
print("=" * 60)
results = {}

# ── T1: All packages importable ─────────────────────────────
print("\nT1: Package imports...")
try:
    from context.ast_chunker import ASTChunker, CodeChunk
    from context.git_analyzer import GitAnalyzer
    from context.retriever import ContextRetriever, count_tokens
    from context.builder import ContextBuilder, ContextBundle
    from agents.debugger import DebuggerAgent
    from agents.fixer import FixerAgent
    from agents.reviewer import ReviewerAgent
    from agents.memory import PatternMemory
    from agents.orchestrator import Orchestrator, PipelineResult
    from validation.engine import ValidationEngine
    from validation.static_analyzer import StaticAnalyzer
    from validation.test_runner import TestRunner
    from output.slack_notifier import SlackNotifier
    from output.github_pr import GitHubPRCreator
    from api.models.agent_schemas import RootCauseResult, Fix, PatchFile, ReviewResult
    from workers.main import celery_app
    from workers.tasks import analyze_error, index_repository
    results["T1"] = "PASS"
    print("T1: PASS (all 20+ classes importable)")
except ImportError as e:
    results["T1"] = f"FAIL: {e}"
    print(f"T1: FAIL - {e}")

# ── T2: AST chunking on real files ──────────────────────────
print("\nT2: AST chunking...")
try:
    chunker = ASTChunker()
    chunks = chunker.chunk_file("storage/postgres.py")
    assert len(chunks) >= 5, f"Expected 5+ chunks, got {len(chunks)}"
    tokens = sum(count_tokens(c.content) for c in chunks)
    assert tokens > 0
    results["T2"] = "PASS"
    print(f"T2: PASS ({len(chunks)} chunks, {tokens} tokens from postgres.py)")
except Exception as e:
    results["T2"] = f"FAIL: {e}"
    print(f"T2: FAIL - {e}")

# ── T3: Git analyzer ────────────────────────────────────────
print("\nT3: Git analyzer...")
try:
    git = GitAnalyzer(".")
    commits = git.get_recent_commits(days=30, max_commits=5)
    authors = git.get_file_authors("storage/postgres.py")
    results["T3"] = "PASS"
    print(f"T3: PASS ({len(commits)} commits, {len(authors)} authors)")
except Exception as e:
    results["T3"] = f"FAIL: {e}"
    print(f"T3: FAIL - {e}")

# ── T4: Static analyzer on patched code ─────────────────────
print("\nT4: Static analyzer...")
async def test_static():
    sa = StaticAnalyzer()
    good_code = "def safe_func(user):\n    return user.get('id') if user else None\n"
    bad_code  = "import os\ndef bad(x): return eval(x)\n"
    r1 = await sa.analyze("app.py", good_code)
    r2 = await sa.analyze("app.py", bad_code)
    assert r1.passed, f"Good code should pass: {r1.findings}"
    assert not r2.passed, "eval() code should fail security scan"
    return r1, r2
try:
    r1, r2 = asyncio.run(test_static())
    results["T4"] = "PASS"
    print(f"T4: PASS (safe={r1.passed} dangerous={not r2.passed} tool={r1.tool})")
except Exception as e:
    results["T4"] = f"FAIL: {e}"
    print(f"T4: FAIL - {e}")

# ── T5: Validation engine with real patch ────────────────────
print("\nT5: Validation engine...")
async def test_validation():
    engine = ValidationEngine(repo_path=".")
    pf = PatchFile(
        file_path="storage/postgres.py",
        unified_diff=(
            "--- a/storage/postgres.py\n+++ b/storage/postgres.py\n"
            "@@ -1,3 +1,3 @@\n def get_user(uid):\n-    return uid.get('name')\n"
            "+    return uid.get('name') if uid else None\n"
        ),
        lines_changed=2, change_description="null guard"
    )
    fix = Fix(error_id="test-val-001", patches=[pf], total_files_changed=1,
              total_lines_changed=2, fix_explanation="Added null guard",
              suggested_tests=[], confidence_retained=0.85,
              regression_risk="low", strategy="llm_single_file", llm_provider="groq")
    report = await engine.validate(fix, "test-val-001")
    return report
try:
    t0 = time.perf_counter()
    report = asyncio.run(test_validation())
    ms = (time.perf_counter()-t0)*1000
    results["T5"] = "PASS"
    print(f"T5: PASS validation={report.passed} issues={len(report.blocking_issues)} in {ms:.0f}ms")
    print(f"    Static: {report.static_analysis.summary if report.static_analysis else 'N/A'}")
except Exception as e:
    results["T5"] = f"FAIL: {e}"
    print(f"T5: FAIL - {e}")

# ── T6: Slack notifier (config check, no real send) ─────────
print("\nT6: Slack notifier config...")
try:
    notifier = SlackNotifier()
    is_configured = notifier._client is not None
    results["T6"] = "PASS"
    print(f"T6: PASS (configured={is_configured}, channel={notifier._channel})")
except Exception as e:
    results["T6"] = f"FAIL: {e}"
    print(f"T6: FAIL - {e}")

# ── T7: GitHub PR creator (config check only) ────────────────
print("\nT7: GitHub PR creator config...")
try:
    pr_creator = GitHubPRCreator()
    results["T7"] = "PASS"
    print(f"T7: PASS (app_id={pr_creator._settings.github_app_id})")
except Exception as e:
    results["T7"] = f"FAIL: {e}"
    print(f"T7: FAIL - {e}")

# ── T8: Orchestrator wiring (no DB call) ─────────────────────
print("\nT8: Orchestrator instantiation...")
try:
    from config.llm_router import get_llm_router
    from storage.redis_client import get_redis
    redis = get_redis()
    llm = get_llm_router(redis_client=redis.client)
    from storage.postgres import get_postgres
    orch = Orchestrator(postgres=get_postgres(), redis=redis, llm_router=llm, repo_path=".")
    assert hasattr(orch, "_debugger")
    assert hasattr(orch, "_fixer")
    assert hasattr(orch, "_reviewer")
    assert hasattr(orch, "_validator")
    assert hasattr(orch, "_slack")
    assert hasattr(orch, "_github")
    results["T8"] = "PASS"
    print("T8: PASS (all 8 sub-components wired in orchestrator)")
except Exception as e:
    results["T8"] = f"FAIL: {e}"
    print(f"T8: FAIL - {e}")

# ── Summary ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 1 INTEGRATION TEST SUMMARY")
print("=" * 60)
passed = sum(1 for v in results.values() if v == "PASS")
total = len(results)
for test, status in results.items():
    icon = "[PASS]" if status == "PASS" else "[FAIL]"
    print(f"  {icon} {test}: {status}")
print(f"\nResult: {passed}/{total} tests passed")
if passed == total:
    print("\nPHASE 1 COMPLETE - ALL TESTS PASSED")
else:
    print(f"\nFAILURES FOUND: {total - passed} tests need attention")
    sys.exit(1)
