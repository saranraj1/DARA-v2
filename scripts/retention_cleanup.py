"""
scripts/retention_cleanup.py
============================
DARA data retention cleanup — purges errors, pipeline runs, and Qdrant
vectors older than DATA_RETENTION_DAYS (default: 90 days).

Run manually or schedule as a daily cron/Celery beat task:

    # Manual:
    poetry run python scripts/retention_cleanup.py

    # Dry-run (shows what WOULD be deleted without deleting):
    poetry run python scripts/retention_cleanup.py --dry-run

    # Custom retention window:
    poetry run python scripts/retention_cleanup.py --days 30

Environment variables:
    DATA_RETENTION_DAYS   Purge errors older than N days (default: 90)
    POSTGRES_URL          PostgreSQL connection string
    QDRANT_URL            Qdrant base URL

The script is idempotent and safe to run repeatedly.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, ".")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("dara.retention")


async def purge_old_errors(pg, cutoff: datetime, dry_run: bool) -> int:
    """
    Delete error_events (and cascade to pipeline_runs, fixes) older than cutoff.
    Returns the count of deleted errors.
    """
    from sqlalchemy import delete, func, select, text
    from storage.models import ErrorEvent, PipelineRun

    async with pg.session() as sess:
        # Count what we're about to delete
        count_result = await sess.execute(
            select(func.count()).where(ErrorEvent.created_at < cutoff)
        )
        count = count_result.scalar() or 0

        if count == 0:
            logger.info("Retention: no error_events older than %s", cutoff.date())
            return 0

        logger.info(
            "Retention: found %d error_events older than %s (%s cutoff)",
            count, cutoff.date(), "DRY RUN" if dry_run else "WILL DELETE",
        )

        if dry_run:
            return count

        # Collect IDs to delete (for Qdrant cleanup)
        old_ids = (await sess.execute(
            select(ErrorEvent.id).where(ErrorEvent.created_at < cutoff)
        )).scalars().all()

        # Delete pipeline_runs first (foreign key constraint)
        pr_del = await sess.execute(
            delete(PipelineRun).where(PipelineRun.error_id.in_(old_ids))
        )
        logger.info("Retention: deleted %d pipeline_runs", pr_del.rowcount)

        # Delete errors (cascades to related tables)
        err_del = await sess.execute(
            delete(ErrorEvent).where(ErrorEvent.id.in_(old_ids))
        )
        logger.info("Retention: deleted %d error_events", err_del.rowcount)

    return count


async def purge_qdrant_vectors(cutoff: datetime, dry_run: bool) -> int:
    """
    Delete Qdrant vectors for errors older than cutoff.
    Uses the payload field 'created_at' stored on each vector point.
    Returns the count of deleted vectors.
    """
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, Range

        from config.settings import get_settings
        settings = get_settings()
        client = AsyncQdrantClient(url=settings.qdrant_url)

        cutoff_ts = cutoff.timestamp()

        # Filter: points older than cutoff
        scroll_filter = Filter(
            must=[
                FieldCondition(
                    key="created_at",
                    range=Range(lte=cutoff_ts),
                )
            ]
        )

        # Scroll to count first
        results, _ = await client.scroll(
            collection_name="error_contexts",
            scroll_filter=scroll_filter,
            limit=10000,
            with_payload=False,
            with_vectors=False,
        )
        count = len(results)

        if count == 0:
            logger.info("Retention[Qdrant]: no vectors older than %s", cutoff.date())
            return 0

        logger.info(
            "Retention[Qdrant]: found %d vectors older than %s (%s)",
            count, cutoff.date(), "DRY RUN" if dry_run else "WILL DELETE",
        )

        if dry_run:
            return count

        point_ids = [r.id for r in results]
        await client.delete(
            collection_name="error_contexts",
            points_selector=point_ids,
        )
        logger.info("Retention[Qdrant]: deleted %d vectors", count)
        return count

    except Exception as e:
        logger.warning("Retention[Qdrant]: skipped — %s", e)
        return 0


async def main(days: int, dry_run: bool) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    mode   = "DRY RUN" if dry_run else "LIVE"

    logger.info("=" * 60)
    logger.info("DARA Retention Cleanup  [%s]", mode)
    logger.info("Cutoff: %s  (%d days)", cutoff.isoformat(), days)
    logger.info("=" * 60)

    try:
        from storage.postgres import PostgresClient
        pg = PostgresClient()
    except Exception as e:
        logger.error("Cannot connect to Postgres: %s", e)
        return 1

    pg_deleted  = await purge_old_errors(pg, cutoff, dry_run)
    qd_deleted  = await purge_qdrant_vectors(cutoff, dry_run)

    await pg.close()

    logger.info("=" * 60)
    logger.info("Retention summary [%s]:", mode)
    logger.info("  Postgres rows deleted : %d", pg_deleted)
    logger.info("  Qdrant vectors deleted: %d", qd_deleted)
    if dry_run:
        logger.info("  (No data was actually deleted — re-run without --dry-run)")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="DARA data retention cleanup")
    parser.add_argument(
        "--days",
        type=int,
        default=int(__import__("os").environ.get("DATA_RETENTION_DAYS", "90")),
        help="Purge data older than N days (default: 90 or DATA_RETENTION_DAYS env var)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    args = parser.parse_args()

    if args.days < 7:
        print("ERROR: --days must be >= 7 to prevent accidental data loss")
        sys.exit(1)

    sys.exit(asyncio.run(main(days=args.days, dry_run=args.dry_run)))
