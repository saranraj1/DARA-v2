"""DARA - Celery tasks: thin wrappers that delegate to Orchestrator"""
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


@celery_app.task(
    name="workers.tasks.analyze_error",
    bind=True, max_retries=3, default_retry_delay=30, acks_late=True,
)
def analyze_error(self, error_id: str, priority: str = "P2") -> dict:
    """Full debug pipeline: load -> context -> debug -> fix -> validate -> review -> output."""
    logger.info("analyze_error: id=%s priority=%s", error_id, priority)
    try:
        return _run(_analyze(error_id))
    except Exception as exc:
        logger.error("analyze_error FAILED: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


async def _analyze(error_id: str) -> dict:
    from config.llm_router import get_llm_router
    from storage.postgres import get_postgres
    from storage.redis_client import get_redis
    from agents.orchestrator import Orchestrator

    postgres = get_postgres()
    redis = get_redis()
    llm = get_llm_router(redis_client=redis.client)

    orch = Orchestrator(postgres=postgres, redis=redis, llm_router=llm, repo_path=".")
    result = await orch.run(error_id)

    logger.info(
        "analyze_error done: id=%s status=%s stage=%s fix_id=%s",
        error_id, result.status, result.stage_reached, result.fix_id,
    )
    return {
        "status": result.status,
        "error_id": result.error_id,
        "fix_id": result.fix_id,
        "stage_reached": result.stage_reached,
        "pr_url": result.pr_url,
        "slack_sent": result.slack_sent,
        "failure_reason": result.failure_reason,
        "confidence": result.root_cause.confidence if result.root_cause else None,
        "strategy": result.root_cause.suggested_strategy if result.root_cause else None,
        "recommendation": result.review.overall_recommendation if result.review else None,
        "quality_score": result.review.quality_score if result.review else None,
        "validation_passed": result.validation.passed if result.validation else None,
    }


@celery_app.task(
    name="workers.tasks.index_repository",
    bind=True, max_retries=2, default_retry_delay=60,
)
def index_repository(self, repo_path: str, service_name: str,
                     file_extensions: list | None = None) -> dict:
    """Walk a repo, chunk all files, embed, store in Qdrant."""
    logger.info("index_repository: repo=%s service=%s", repo_path, service_name)
    try:
        return _run(_index(repo_path, service_name, file_extensions or [".py"]))
    except Exception as exc:
        logger.error("index_repository FAILED: %s", exc, exc_info=True)
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
    SKIP = {"__pycache__", "node_modules", ".venv", ".git", "migrations"}

    all_chunks, files = [], 0
    for ext in extensions:
        for fp in Path(repo_path).rglob(f"*{ext}"):
            if any(s in fp.parts for s in SKIP):
                continue
            chunks = chunker.chunk_file(str(fp), service=service_name)
            all_chunks.extend(chunks)
            files += 1

    if not all_chunks:
        return {"indexed": 0, "files": files, "service": service_name}

    indexed = await retriever.index_code_chunks(all_chunks, service=service_name)
    logger.info("index_repository: indexed=%d from %d files", indexed, files)
    return {"indexed": indexed, "total_chunks": len(all_chunks),
            "files": files, "service": service_name}
