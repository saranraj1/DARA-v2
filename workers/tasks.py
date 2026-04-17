from __future__ import annotations
import asyncio, logging
from workers.main import celery_app

logger = logging.getLogger(__name__)

def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed(): raise RuntimeError
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)

@celery_app.task(name="workers.tasks.analyze_error", bind=True, max_retries=3, default_retry_delay=30, acks_late=True)
def analyze_error(self, error_id: str, priority: str = "P2") -> dict:
    logger.info("analyze_error: error_id=%s priority=%s", error_id, priority)
    try:
        return _run(_analyze(error_id, priority))
    except Exception as exc:
        logger.error("analyze_error failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)

async def _analyze(error_id: str, priority: str) -> dict:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
    from storage.postgres import get_postgres
    from storage.redis_client import get_redis
    from storage.qdrant_client import get_qdrant
    from config.llm_router import get_llm_router
    from config.settings import get_settings
    from context.builder import ContextBuilder
    from context.retriever import ContextRetriever
    from agents.debugger import DebuggerAgent
    from agents.fixer import FixerAgent
    from agents.reviewer import ReviewerAgent
    from agents.memory import PatternMemory

    settings = get_settings()
    postgres = get_postgres()
    redis = get_redis()

    # 1. Load error
    error = await postgres.get_error(error_id)
    if not error:
        return {"status": "error_not_found"}

    error_dict = {
        "id": str(error.id), "error_class": error.error_class,
        "message": error.message, "stack_trace": error.stack_trace,
        "file_path": error.file_path, "line_number": error.line_number,
        "service": error.service, "severity": error.severity,
        "commit_sha": error.commit_sha, "branch": error.branch, "trace_id": error.trace_id,
    }

    # 2. Check pattern library first (fast path)
    memory = PatternMemory()
    template = await memory.find_template(error_dict)
    if template and template.get("success_rate", 0) >= 0.8:
        logger.info("analyze_error: template hit for %s", error_id)
        await postgres.update_error_status(error_id, "fix_ready")
        return {"status": "template_hit", "error_id": error_id, "template_id": template["template_id"]}

    await postgres.update_error_status(error_id, "analyzing")

    # 3. Build context bundle
    llm = get_llm_router(redis_client=redis.client)
    retriever = ContextRetriever(qdrant=get_qdrant(), llm_router=llm)
    builder = ContextBuilder(retriever=retriever, repo_path=".")
    bundle = await builder.build(error_dict)

    # 4. DebuggerAgent -> root cause
    debugger = DebuggerAgent(llm_router=llm)
    root_cause = await debugger.analyze(bundle, error_dict)
    await redis.set_pipeline_state(error_id, "root_cause_confidence", root_cause.confidence)
    await redis.set_pipeline_state(error_id, "status", "root_cause_found")

    # 5. Gate: escalate if confidence too low or complex
    if root_cause.suggested_strategy == "human_escalation" or root_cause.confidence < 0.4:
        logger.warning("analyze_error: escalating to human review for %s", error_id)
        await postgres.update_error_status(error_id, "escalated")
        return {"status": "escalated", "error_id": error_id, "confidence": root_cause.confidence,
                "reason": root_cause.root_cause[:200]}

    # 6. FixerAgent -> generate patch
    fixer = FixerAgent(llm_router=llm)
    fix = await fixer.generate(root_cause, bundle, error_dict)
    await redis.set_pipeline_state(error_id, "status", "fix_generated")

    # 7. ReviewerAgent -> review patch
    reviewer = ReviewerAgent(llm_router=llm)
    review = await reviewer.review(fix, root_cause, error_dict)
    await redis.set_pipeline_state(error_id, "status", "reviewed")

    # 8. Persist fix to DB
    fix_id = await postgres.save_fix({
        "error_id": error_id,
        "patch_content": "\n\n".join(p.unified_diff for p in fix.patches),
        "files_changed": [p.file_path for p in fix.patches],
        "lines_changed": fix.total_lines_changed,
        "confidence": fix.confidence_retained,
        "strategy": fix.strategy,
        "fix_explanation": fix.fix_explanation,
        "quality_score": review.quality_score,
        "validation_pass": review.correctness_passes and review.security_passes,
        "outcome": "pending_review",
        "regression_risk": fix.regression_risk,
    })

    # 9. Auto-approve if high confidence + low risk
    if (review.overall_recommendation == "approve"
            and fix.confidence_retained >= settings.auto_merge_confidence_threshold
            and fix.regression_risk == "low"):
        await postgres.update_fix_outcome(fix_id, "auto_accepted", "Auto-approved by DARA")
        await memory.record_success(error_dict, fix, root_cause, "accepted")
        await postgres.update_error_status(error_id, "resolved")
        logger.info("analyze_error: auto-approved fix=%s for error=%s", fix_id, error_id)

    # 10. Notify Slack (Week 5)
    await redis.push_task("dara:output", {
        "action": "notify_fix_ready", "error_id": error_id, "fix_id": fix_id,
        "recommendation": review.overall_recommendation, "quality_score": review.quality_score,
    })

    return {
        "status": "fix_ready", "error_id": error_id, "fix_id": fix_id,
        "confidence": root_cause.confidence, "strategy": root_cause.suggested_strategy,
        "recommendation": review.overall_recommendation, "quality_score": review.quality_score,
        "context_tokens": bundle.total_tokens,
    }


@celery_app.task(name="workers.tasks.index_repository", bind=True, max_retries=2, default_retry_delay=60)
def index_repository(self, repo_path: str, service_name: str, file_extensions: list | None = None) -> dict:
    logger.info("index_repository: repo=%s service=%s", repo_path, service_name)
    try:
        return _run(_index(repo_path, service_name, file_extensions or [".py"]))
    except Exception as exc:
        logger.error("index_repository failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)

async def _index(repo_path: str, service_name: str, extensions: list) -> dict:
    from pathlib import Path
    from context.ast_chunker import ASTChunker
    from context.retriever import ContextRetriever
    from config.llm_router import get_llm_router
    from storage.qdrant_client import get_qdrant
    from storage.redis_client import get_redis

    redis = get_redis()
    llm = get_llm_router(redis_client=redis.client)
    retriever = ContextRetriever(qdrant=get_qdrant(), llm_router=llm)
    chunker = ASTChunker()
    SKIP = {"__pycache__","node_modules",".venv",".git","migrations"}
    all_chunks, count = [], 0
    for ext in extensions:
        for fp in Path(repo_path).rglob(f"*{ext}"):
            if any(s in fp.parts for s in SKIP): continue
            all_chunks.extend(chunker.chunk_file(str(fp), service=service_name))
            count += 1
    if not all_chunks:
        return {"indexed": 0, "files": count, "service": service_name}
    indexed = await retriever.index_code_chunks(all_chunks, service=service_name)
    logger.info("index_repository: indexed=%d chunks from %d files", indexed, count)
    return {"indexed": indexed, "total_chunks": len(all_chunks), "files": count, "service": service_name}
