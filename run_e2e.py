"""
DARA Phase 1 End-to-End Test with Real Docker Stack
Requires: docker-compose.dev.yml services running (postgres, redis, qdrant)
Run: python run_e2e.py
"""
import asyncio
import sys
import time
import uuid

sys.path.insert(0, ".")

async def main():
    print("=" * 65)
    print("DARA Phase 1 - Full E2E Test (Real DB + Redis + Qdrant)")
    print("=" * 65)
    results = {}

    # -- E1: Infrastructure health checks ----------------------
    print("\nE1: Infrastructure health checks...")
    try:
        from config.llm_router import get_llm_router
        from storage.postgres import get_postgres
        from storage.qdrant_client import get_qdrant
        from storage.redis_client import get_redis

        postgres = get_postgres()
        redis = get_redis()
        qdrant = get_qdrant()

        # Postgres
        await postgres.session().__aenter__()
        print("   Postgres: connected")

        # Redis
        pong = await redis.ping()
        print(f"   Redis: {pong}")

        # Qdrant
        await qdrant.initialize()
        print("   Qdrant: collections initialized")

        results["E1"] = "PASS"
        print("E1: PASS")
    except Exception as e:
        results["E1"] = f"FAIL: {e}"
        print(f"E1: FAIL - {e}")
        print("   Ensure Docker stack is running: docker compose -f docker-compose.dev.yml up -d")
        return

    # -- E2: Ingest a real error via direct DB (no API server needed) ---
    print("\nE2: Error ingestion to Postgres...")
    error_id = None
    try:
        from ingestion.classifier import ErrorClassifier
        from ingestion.deduplicator import ErrorDeduplicator
        from ingestion.normalizer import ErrorNormalizer
        normalizer = ErrorNormalizer()
        classifier = ErrorClassifier()
        deduper = ErrorDeduplicator()

        raw = {
            "error_class": "AttributeError",
            "message": "NoneType object has no attribute get",
            "stack_trace": "File app.py line 42\n  user.get('id')\nAttributeError: NoneType",
            "service": "auth-service",
            "severity": "high",
            "file_path": "storage/postgres.py",
            "line_number": 42,
            "source": "direct",
        }
        normalized = normalizer.normalize(raw, source="direct")
        classified = await classifier.classify(normalized)
        sig = deduper.compute_signature(classified)
        classified["signature"] = sig

        error_id = await postgres.save_error(classified)
        await postgres.create_pipeline_run(error_id)
        print(f"   Ingested error_id: {error_id}")
        print(f"   Signature: {sig[:16]}...")
        results["E2"] = "PASS"
        print("E2: PASS")

        # E2b: Deduplication — same error should return existing_error_id
        print("\nE2b: Deduplication check...")
        dedup = await deduper.check(classified, postgres)
        assert dedup.is_duplicate, "Same error should be detected as duplicate"
        assert dedup.existing_error_id == error_id
        results["E2b"] = "PASS"
        print(f"   Dedup hit: existing_id={dedup.existing_error_id[:12]}... status={dedup.existing_status}")
        print("E2b: PASS")

    except Exception as e:
        results["E2"] = f"FAIL: {e}"
        results["E2b"] = "SKIP"
        print(f"E2: FAIL - {e}")


    # -- E3: Context builder with real services -----------------
    print("\nE3: Context builder (AST + git + Qdrant)...")
    try:
        from context.builder import ContextBuilder
        from context.retriever import ContextRetriever
        llm = get_llm_router(redis_client=redis.client)
        retriever = ContextRetriever(qdrant=qdrant, llm_router=llm)
        builder = ContextBuilder(retriever=retriever, repo_path=".")
        mock_error = {
            "id": error_id or str(uuid.uuid4()),
            "error_class": "AttributeError",
            "message": "NoneType object has no attribute get",
            "stack_trace": "File app.py line 42\nAttributeError",
            "file_path": "storage/postgres.py",
            "line_number": 42,
            "service": "auth-service",
            "severity": "high",
            "commit_sha": "abc1234",
            "branch": "main",
            "trace_id": None,
        }
        t0 = time.perf_counter()
        bundle = await builder.build(mock_error)
        ms = (time.perf_counter()-t0)*1000
        print(f"   {bundle.summary()}")
        print(f"   Time: {ms:.0f}ms")
        assert bundle.erroring_code is not None, "Should find erroring function in postgres.py"
        assert bundle.total_tokens > 0
        results["E3"] = "PASS"
        print("E3: PASS")
    except Exception as e:
        results["E3"] = f"FAIL: {e}"
        print(f"E3: FAIL - {e}")
        bundle = None

    # -- E4: Index repo into Qdrant -----------------------------
    print("\nE4: Repository indexing into Qdrant...")
    try:
        from context.ast_chunker import ASTChunker
        from context.retriever import ContextRetriever
        chunker = ASTChunker()
        chunks = chunker.chunk_file("storage/postgres.py", service="auth-service")
        llm = get_llm_router(redis_client=redis.client)
        retriever = ContextRetriever(qdrant=qdrant, llm_router=llm)
        t0 = time.perf_counter()
        indexed = await retriever.index_code_chunks(chunks, service="auth-service")
        ms = (time.perf_counter()-t0)*1000
        print(f"   Indexed {indexed}/{len(chunks)} chunks in {ms:.0f}ms")
        assert indexed >= 0
        results["E4"] = "PASS"
        print("E4: PASS")
    except Exception as e:
        results["E4"] = f"FAIL: {e}"
        print(f"E4: FAIL - {e}")

    # -- E5: Orchestrator full pipeline (if error ingested) -----
    print("\nE5: Full orchestrator pipeline (real LLM calls)...")
    if not error_id:
        print("   SKIP: no error_id (E2 failed)")
        results["E5"] = "SKIP"
    else:
        try:
            from agents.orchestrator import Orchestrator
            llm = get_llm_router(redis_client=redis.client)
            orch = Orchestrator(postgres=postgres, redis=redis, llm_router=llm, repo_path=".")
            t0 = time.perf_counter()
            result = await orch.run(error_id)
            ms = (time.perf_counter()-t0)*1000
            print(f"   Status:        {result.status}")
            print(f"   Stage reached: {result.stage_reached}")
            print(f"   Fix ID:        {result.fix_id}")
            print(f"   Slack sent:    {result.slack_sent}")
            print(f"   Time:          {ms:.0f}ms")
            if result.root_cause:
                print(f"   Confidence:    {result.root_cause.confidence:.2f}")
                print(f"   Strategy:      {result.root_cause.suggested_strategy}")
            if result.review:
                print(f"   Review:        {result.review.overall_recommendation} (score={result.review.quality_score:.2f})")
            if result.validation:
                print(f"   Validation:    passed={result.validation.passed}")
            assert result.status not in ("failed",), f"Pipeline failed: {result.failure_reason}"
            assert result.stage_reached not in ("start",)
            results["E5"] = "PASS"
            print("E5: PASS")
        except Exception as e:
            results["E5"] = f"FAIL: {e}"
            print(f"E5: FAIL - {e}")

    # -- E6: Redis pipeline state tracking ---------------------
    print("\nE6: Redis state tracking...")
    try:
        await redis.set_pipeline_state("e2e-test", "status", "testing")
        state = await redis.get_pipeline_state('e2e-test'); val = state.get('status') if isinstance(state,dict) else state
        assert val == "testing", f"Expected 'testing', got {val}"
        results["E6"] = "PASS"
        print(f"   Redis round-trip: set='testing' get='{val}'")
        print("E6: PASS")
    except Exception as e:
        results["E6"] = f"FAIL: {e}"
        print(f"E6: FAIL - {e}")

    # -- Summary ------------------------------------------------
    print("\n" + "=" * 65)
    print("E2E TEST SUMMARY")
    print("=" * 65)
    passed = sum(1 for v in results.values() if v == "PASS")
    skipped = sum(1 for v in results.values() if v == "SKIP")
    failed = sum(1 for v in results.values() if v.startswith("FAIL"))
    total = len(results)
    for k, v in results.items():
        icon = "[PASS]" if v == "PASS" else "[SKIP]" if v == "SKIP" else "[FAIL]"
        print(f"  {icon} {k}: {v}")
    print(f"\nResult: {passed}/{total} passed | {skipped} skipped | {failed} failed")
    if failed == 0:
        print("\nPHASE 1 E2E: ALL TESTS PASSED")
    else:
        print(f"\nFAILED: {failed} tests - check Docker stack and API server")
        sys.exit(1)

asyncio.run(main())
