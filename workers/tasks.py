"""DARA - Celery tasks: thin wrappers that delegate to Orchestrator"""
from __future__ import annotations

import asyncio
import logging

from workers.main import celery_app

logger = logging.getLogger(__name__)


def _run(coro):
    """
    Run a coroutine from a synchronous Celery task.
    Reuses an existing event loop if one is already set, otherwise creates a
    fresh one. This avoids the 'cannot re-enter event loop' error.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ── Core pipeline task ─────────────────────────────────────────


@celery_app.task(
    name="workers.tasks.analyze_error",
    bind=True, max_retries=3, default_retry_delay=30, acks_late=True,
)
def analyze_error(self, error_id: str, priority: str = "P2") -> dict:
    """Full debug pipeline: load → context → debug → fix → validate → review → output."""
    logger.info("analyze_error: id=%s priority=%s", error_id, priority)
    try:
        return _run(_analyze(error_id))
    except Exception as exc:
        logger.error("analyze_error FAILED: %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc  # SEV-4: chain exception for traceback


async def _analyze(error_id: str) -> dict:
    from agents.orchestrator import Orchestrator
    from config.llm_router import get_llm_router
    from storage.postgres import get_postgres
    from storage.redis_client import get_redis

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


# ── Repository indexing task ───────────────────────────────────


@celery_app.task(
    name="workers.tasks.index_repository",
    bind=True, max_retries=2, default_retry_delay=60,
)
def index_repository(self, repo_path: str, service_name: str,
                     file_extensions: list | None = None) -> dict:
    """Walk a repo, chunk all Python files, embed, store in Qdrant."""
    logger.info("index_repository: repo=%s service=%s", repo_path, service_name)
    try:
        return _run(_index(repo_path, service_name, file_extensions or [".py"]))
    except Exception as exc:
        logger.error("index_repository FAILED: %s", exc, exc_info=True)
        raise self.retry(exc=exc) from exc  # SEV-4: chain exception


async def _index(repo_path: str, service_name: str, extensions: list) -> dict:
    from pathlib import Path

    from config.llm_router import get_llm_router
    from context.ast_chunker import ASTChunker
    from context.retriever import ContextRetriever
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


# ── Maintenance tasks ──────────────────────────────────────────


@celery_app.task(name="workers.tasks.cleanup_stale_pipelines", bind=True)
def cleanup_stale_pipelines(self, stale_after_minutes: int = 30) -> dict:
    """Mark errors stuck in analyzing/fixing as failed after a timeout."""
    logger.info("cleanup_stale_pipelines: running (stale_after=%dm)", stale_after_minutes)
    try:
        return _run(_cleanup_stale(stale_after_minutes))
    except Exception as exc:
        logger.error("cleanup_stale_pipelines FAILED: %s", exc, exc_info=True)
        return {"cleaned": 0, "error": str(exc)}


async def _cleanup_stale(minutes: int) -> dict:
    from storage.postgres import get_postgres
    pg = get_postgres()
    count = await pg.cleanup_stale_errors(stale_after_minutes=minutes)
    return {"cleaned": count, "stale_after_minutes": minutes}


@celery_app.task(name="workers.tasks.warm_stats_cache", bind=True)
def warm_stats_cache(self) -> dict:
    """Pre-compute and cache pipeline stats for fast admin API response."""
    logger.info("warm_stats_cache: running")
    try:
        return _run(_warm_stats())
    except Exception as exc:
        logger.error("warm_stats_cache FAILED: %s", exc, exc_info=True)
        return {"cached": False, "error": str(exc)}


async def _warm_stats() -> dict:
    import json

    from storage.postgres import get_postgres
    from storage.redis_client import get_redis

    pg = get_postgres()
    redis = get_redis()
    stats = await pg.get_pipeline_stats()
    # Cache for 70 minutes (longer than the 60-minute schedule to avoid gaps)
    await redis.client.setex("dara:stats:cache", 4200, json.dumps(stats))
    logger.info("warm_stats_cache: cached stats=%s", stats)
    return {"cached": True, "stats": stats}


@celery_app.task(name="workers.tasks.optimize_pattern_library", bind=True)
def optimize_pattern_library(self) -> dict:
    """Deactivate patterns with very low success rates (<0.2)."""
    logger.info("optimize_pattern_library: running")
    try:
        return _run(_optimize_patterns())
    except Exception as exc:
        logger.error("optimize_pattern_library FAILED: %s", exc, exc_info=True)
        return {"deactivated": 0, "error": str(exc)}


async def _optimize_patterns() -> dict:
    from sqlalchemy import update

    from storage.models import PatternLibrary
    from storage.postgres import get_postgres

    pg = get_postgres()
    async with pg.session() as sess:
        result = await sess.execute(
            update(PatternLibrary)
            .where(PatternLibrary.success_rate < 0.2)
            .where(PatternLibrary.is_active == True)  # noqa: E712
            .values(is_active=False)
            .returning(PatternLibrary.id)
        )
        count = len(result.fetchall())
    logger.info("optimize_pattern_library: deactivated %d low-rate patterns", count)
    return {"deactivated": count}


# ── Phase 3: Reflexive memory tasks ───────────────────────────


@celery_app.task(name="workers.tasks.strategy_monitor_scan", bind=True)
def strategy_monitor_scan(self) -> dict:
    """
    Phase 3 (Week 15-16): Scan all error classes for high rejection rates.
    Flags classes with rate > 40% for strategy regeneration.
    """
    logger.info("strategy_monitor_scan: running")
    try:
        return _run(_run_strategy_monitor())
    except Exception as exc:
        logger.error("strategy_monitor_scan FAILED: %s", exc, exc_info=True)
        return {"flagged": 0, "error": str(exc)}


async def _run_strategy_monitor() -> dict:
    from agents.strategy_monitor import StrategyMonitor
    monitor = StrategyMonitor()
    failing = await monitor.scan()
    logger.info("strategy_monitor_scan: flagged=%d classes", len(failing))
    return {
        "flagged": len(failing),
        "classes": [f.error_class for f in failing],
    }


@celery_app.task(name="workers.tasks.run_strategy_evaluations", bind=True)
def run_strategy_evaluations(self) -> dict:
    """
    Phase 3 (Week 17-18): A/B test all TESTING-status strategy variants.
    Auto-promotes winners to ACTIVE, retires losers.
    """
    logger.info("run_strategy_evaluations: running")
    try:
        return _run(_evaluate_strategies())
    except Exception as exc:
        logger.error("run_strategy_evaluations FAILED: %s", exc, exc_info=True)
        return {"evaluated": 0, "promoted": 0, "error": str(exc)}


async def _evaluate_strategies() -> dict:
    import uuid
    from sqlalchemy import select
    from agents.strategy_evaluator import StrategyEvaluator
    from storage.models import StrategyVariant
    from storage.postgres import get_postgres

    pg = get_postgres()
    evaluator = StrategyEvaluator()
    evaluated, promoted = 0, 0

    # Find all pairs of variants in TESTING status grouped by error_class
    async with pg.session() as sess:
        rows = (await sess.execute(
            select(StrategyVariant)
            .where(StrategyVariant.status == "testing")
            .order_by(StrategyVariant.error_class, StrategyVariant.created_at)
        )).scalars().all()

    # Group by error_class and pair consecutive variants for A/B test
    by_class: dict[str, list] = {}
    for row in rows:
        by_class.setdefault(row.error_class, []).append(row)

    for error_class, variants in by_class.items():
        if len(variants) < 2:
            continue
        # Test first two variants (most recently created pair)
        a, b = variants[0], variants[1]
        result = await evaluator.run_ab_test(
            str(a.id), str(b.id), error_class
        )
        evaluated += 1
        if result.promoted:
            promoted += 1
        logger.info(
            "run_strategy_evaluations: %s winner=%s promoted=%s",
            error_class, result.winner, result.promoted,
        )

    return {"evaluated": evaluated, "promoted": promoted}


@celery_app.task(name="workers.tasks.run_anomaly_detection", bind=True)
def run_anomaly_detection(self) -> dict:
    """
    Phase 3 (Week 18-19): Z-score scan across all services for latency spikes,
    error surges, and call volume drops. Writes alerts to anomaly_alerts table.
    """
    logger.info("run_anomaly_detection: running")
    try:
        return _run(_detect_anomalies())
    except Exception as exc:
        logger.error("run_anomaly_detection FAILED: %s", exc, exc_info=True)
        return {"alerts": 0, "error": str(exc)}


async def _detect_anomalies() -> dict:
    from monitoring.anomaly_detector import AnomalyDetector
    detector = AnomalyDetector()
    alerts = await detector.run_full_scan()
    logger.info("run_anomaly_detection: %d alerts generated", len(alerts))
    return {
        "alerts": len(alerts),
        "services_scanned": len({a.service_name for a in alerts}),
    }


@celery_app.task(name="workers.tasks.export_fine_tuning_data", bind=True)
def export_fine_tuning_data(self) -> dict:
    """
    Phase 3 (Week 19-20): Export high-quality (error, root_cause, fix) triples
    to MinIO as JSONL for future LLM fine-tuning.
    """
    logger.info("export_fine_tuning_data: running (weekly export)")
    try:
        return _run(_export_triples())
    except Exception as exc:
        logger.error("export_fine_tuning_data FAILED: %s", exc, exc_info=True)
        return {"exported": 0, "error": str(exc)}


async def _export_triples() -> dict:
    from datetime import datetime, timedelta, timezone
    from export.fine_tuning_exporter import FineTuningExporter
    exporter = FineTuningExporter()
    since = datetime.now(timezone.utc) - timedelta(days=7)
    result = await exporter.export(since_timestamp=since)
    logger.info(
        "export_fine_tuning_data: exported=%d triples path=%s",
        result.triple_count if result else 0,
        result.minio_path if result else "N/A",
    )
    return {
        "exported": result.triple_count if result else 0,
        "path": result.minio_path if result else None,
        "dry_run": False,
    }

