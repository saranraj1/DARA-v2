"""
Live end-to-end pipeline test for Week 4.
Uses real Groq LLM. Tests: DebuggerAgent -> FixerAgent -> ReviewerAgent
No database writes - pure in-memory test.
"""
import asyncio
import sys
import time

sys.path.insert(0, ".")

async def main():
    print("=== DARA Live Pipeline Test (Week 4) ===\n")

    from agents.debugger import DebuggerAgent
    from agents.fixer import FixerAgent
    from agents.reviewer import ReviewerAgent
    from config.llm_router import get_llm_router
    from context.builder import ContextBundle
    from storage.redis_client import get_redis

    # Use real LLM router
    redis = get_redis()
    llm = get_llm_router(redis_client=redis.client)

    # Synthetic error event (AttributeError NoneType)
    error = {
        "id": "test-live-001",
        "error_class": "AttributeError",
        "message": "NoneType object has no attribute get",
        "stack_trace": (
            "Traceback (most recent call last):\n"
            "  File app.py, line 42, in get_user_profile\n"
            "    name = user.get('name')\n"
            "AttributeError: NoneType object has no attribute get"
        ),
        "file_path": "storage/postgres.py",
        "line_number": 42,
        "service": "auth-service",
        "severity": "high",
        "commit_sha": "abc1234",
        "branch": "main",
        "trace_id": None,
    }

    bundle = ContextBundle(
        error_id="test-live-001",
        erroring_file="storage/postgres.py",
        erroring_function="get_error",
        erroring_code=(
            "async def get_error(self, error_id: str):\n"
            "    async with self.session() as sess:\n"
            "        result = await sess.execute(select(Error).where(Error.id == error_id))\n"
            "        return result.scalar_one_or_none()\n"
        ),
        related_functions=[],
        recent_commits=[{"sha": "abc1234", "date": "2026-04-17", "message": "fix: update error retrieval"}],
        similar_past_bugs=[],
        total_tokens=120,
    )

    # ── Step 1: DebuggerAgent ───────────────────────────────────
    print("Step 1: DebuggerAgent analyzing...")
    t0 = time.perf_counter()
    debugger = DebuggerAgent(llm_router=llm)
    root_cause = await debugger.analyze(bundle, error)
    d1 = (time.perf_counter()-t0)*1000
    print(f"  Confidence:   {root_cause.confidence:.2f}")
    print(f"  Strategy:     {root_cause.suggested_strategy}")
    print(f"  Root cause:   {root_cause.root_cause[:80]}...")
    print(f"  Files:        {root_cause.files_to_change}")
    print(f"  Time:         {d1:.0f}ms")
    assert root_cause.confidence > 0, "Confidence must be > 0"
    assert root_cause.suggested_strategy in {"template_based","llm_single_file","llm_multi_file","human_escalation"}
    print("  PASS\n")

    # ── Step 2: FixerAgent ──────────────────────────────────────
    print("Step 2: FixerAgent generating patch...")
    t0 = time.perf_counter()
    fixer = FixerAgent(llm_router=llm)
    fix = await fixer.generate(root_cause, bundle, error)
    d2 = (time.perf_counter()-t0)*1000
    print(f"  Files changed:  {fix.total_files_changed}")
    print(f"  Lines changed:  {fix.total_lines_changed}")
    print(f"  Risk:           {fix.regression_risk}")
    print(f"  Confidence:     {fix.confidence_retained:.2f}")
    print(f"  Time:           {d2:.0f}ms")
    if fix.patches:
        print(f"  Patch preview:  {fix.patches[0].unified_diff[:120]}...")
        assert fix.patches[0].lines_changed >= 0
    print("  PASS\n")

    # ── Step 3: ReviewerAgent ───────────────────────────────────
    print("Step 3: ReviewerAgent reviewing...")
    t0 = time.perf_counter()
    reviewer = ReviewerAgent(llm_router=llm)
    review = await reviewer.review(fix, root_cause, error)
    d3 = (time.perf_counter()-t0)*1000
    print(f"  Quality score:    {review.quality_score:.2f}")
    print(f"  Recommendation:   {review.overall_recommendation}")
    print(f"  Correctness:      {review.correctness_passes}")
    print(f"  Security:         {review.security_passes}")
    print(f"  Notes:            {review.reviewer_notes[:80]}...")
    print(f"  Time:             {d3:.0f}ms")
    assert review.overall_recommendation in {"approve","approve_with_comments","reject"}
    print("  PASS\n")

    total = d1 + d2 + d3
    print(f"=== PIPELINE COMPLETE ===")
    print(f"Total time: {total:.0f}ms (target: <30000ms)")
    print(f"Debugger: {d1:.0f}ms | Fixer: {d2:.0f}ms | Reviewer: {d3:.0f}ms")
    assert total < 30000, f"Pipeline too slow: {total:.0f}ms"
    print("\n=== ALL LIVE BENCHMARKS PASSED ===")

asyncio.run(main())
